#!/usr/bin/env bash
# 服务器侧一键更新脚本：拉取 → 迁移 → 测试/架构铁律 → 重启；任一环节失败自动回滚。
#
# 用法（在 /opt/aiedu/app 下，以 aiedu 用户运行）：
#   sudo -u aiedu -H ./scripts/deploy.sh
#
# 详见 docs/deployment.md 第 5 节。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# 解释器不写死版本号：服务器装的可能是 3.11 也可能是 3.12，写死一个就等着某天
# 换机器时在这里当场炸掉。要指定就用环境变量：PY=python3.11 ./scripts/deploy.sh
PY="${PY:-python3}"
command -v "$PY" >/dev/null || { echo "找不到解释器 $PY"; exit 1; }
"$PY" - <<'PYCHK' || { echo "Python 版本过低，需要 3.11+"; exit 1; }
import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)
PYCHK
SERVICE=aiedu

echo "==> 当前 commit"
PREV=$(git rev-parse HEAD)
echo "    $PREV"

rollback() {
  echo "==> 失败，回滚到 $PREV（不重启服务，继续用旧代码跑）"
  git checkout --quiet "$PREV"
  exit 1
}
trap rollback ERR

echo "==> 拉取更新（只做快进合并，服务器上不产生分叉）"
git fetch origin
# 生产环境永远跟 main。曾经出现过服务器停在一条侧分支上、和 main 各自演进的情况，
# 几十人协作时这是灾难的开始，所以这里显式纠正而不是默默快进当前分支。
CUR="$(git rev-parse --abbrev-ref HEAD)"
if [ "$CUR" != "main" ]; then
  echo "    当前在 $CUR，切回 main（生产环境只跟 main）"
  git rev-parse --verify --quiet main >/dev/null && git checkout --quiet main \
    || git checkout --quiet -b main origin/main
fi
git merge --ff-only origin/main

echo "==> 数据库迁移（幂等）"
"$PY" scripts/migrate.py

echo "==> 全量测试"
"$PY" -m unittest discover -s tests -t tests -q

echo "==> 架构铁律自检"
(cd tests && "$PY" -m unittest test_layering -q)

trap - ERR
echo "==> 校验通过，重启服务"
sudo systemctl restart "$SERVICE"
sleep 1
sudo systemctl status "$SERVICE" --no-pager

NEW=$(git rev-parse HEAD)
echo "==> 完成：$PREV -> $NEW"
