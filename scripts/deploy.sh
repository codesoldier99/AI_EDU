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

PY=python3.11
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
