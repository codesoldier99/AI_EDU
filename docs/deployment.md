# 服务器部署指南

配套材料：[`scripts/deploy.sh`](../scripts/deploy.sh)（一键拉取+校验+重启）、
[`deploy/aiedu.service`](../deploy/aiedu.service)（systemd 单元）、
[`deploy/nginx.conf.example`](../deploy/nginx.conf.example)（反向代理示例）。

---

## 0. 这份指南解决什么，不解决什么

系统现在能在本机演示（`make setup && make dev`），零外部依赖、单进程、SQLite 单文件。
这份指南把**同一套代码**放到一台服务器上，让实验班的老师/学生能通过网络访问，
并且改完代码之后能通过 `git pull` 持续更新，而不是每次上去手改文件。

**不做的事**：不把系统改造成 CLAUDE.md 第 4 节"目标选型"里的
FastAPI + PostgreSQL + Celery 那一套。当前部署形态与本机演示走的是完全相同的逻辑，
只是换了台机器、前面挂了个反向代理——这正是"演示环境与生产环境必须跑同一套逻辑"
这条工程判断的自然延伸。

---

## 1. 先读这三条，再决定怎么部署

1. **规模小，单机就够**：60 学生、10 教师、内部使用，不需要负载均衡或多实例。
   `ThreadingHTTPServer` 每个请求一个线程，这个量级绰绰有余。
2. **鉴权现状是明确的短板，必须显式处理**：当前令牌是 `teacher:T001` /
   `student:2026001` 这种可猜测的明文格式，没有密码，`apps/api/auth.py` 里
   `resolve()` 只查表不做任何身份校验（注释里写明了"接校内统一身份认证时替换
   `resolve()` 即可"，但**目前还没接**）。README 已经把令牌格式公开写在文档里。
   → **绝不能把服务端口不加防护地暴露到公网**，见第 4 节。
3. **数据是单个 SQLite 文件**，没有内建备份、没有主从。见第 3.8 节。

---

## 2. 服务器最低配置

| 项 | 建议值 |
|---|---|
| CPU | 1 vCPU 起 |
| 内存 | 1 GB 起（SQLite + 单进程 Python，很轻） |
| 磁盘 | 10 GB 起 |
| 系统 | Ubuntu 22.04/24.04 或 Debian 12 |
| 网络 | 校内可达即可；是否需要公网 IP 取决于第 4 节的鉴权方案 |
| GPU | 不需要——除非打算本地私有化部署开源大模型，那是另一台机器的事 |

---

## 3. 部署步骤（systemd 原生部署，推荐，与项目"零依赖"精神一致）

不用 Docker 也能干净地做到——这不是省事，是因为额外引入容器运行时对一个
"Python 标准库 + SQLite"的系统没有实际收益，反而多一层需要维护的东西。
如果学院基础设施要求统一走容器，appendix（第 7 节）给了最小 Dockerfile 思路。

### 3.1 基础环境

```bash
sudo apt update
sudo apt install -y python3.11 python3.11-venv git nginx sqlite3
```

`pyyaml` 是可选依赖（用于 `config.yaml`，缺失时退化为纯默认值）：

```bash
sudo apt install -y python3-yaml   # 或 pip install --user pyyaml
```

### 3.2 建专用系统用户 + 拉代码

不要用 root 跑服务；给应用一个不能登录的专用账号。

```bash
sudo useradd -r -m -d /opt/aiedu -s /usr/sbin/nologin aiedu
sudo -u aiedu -H git clone https://github.com/codesoldier99/AI_EDU.git /opt/aiedu/app
```

如果仓库是私有的，用部署密钥而不是个人 PAT（PAT 泄漏影响面更大）：

```bash
sudo -u aiedu -H ssh-keygen -t ed25519 -f /opt/aiedu/.ssh/deploy_key -N ""
# 把 /opt/aiedu/.ssh/deploy_key.pub 加到 GitHub 仓库 Settings → Deploy keys（只读即可）
sudo -u aiedu -H git -C /opt/aiedu/app remote set-url origin \
  git@github.com:codesoldier99/AI_EDU.git
```

### 3.3 配置

```bash
sudo -u aiedu -H cp /opt/aiedu/app/config.example.yaml /opt/aiedu/app/config.yaml
sudo -u aiedu -H chmod 600 /opt/aiedu/app/config.yaml
```

编辑 `config.yaml`：

- `host: 127.0.0.1`、`port: 8900` —— **保持监听在回环地址**，对外访问一律走
  nginx（第 3.6 节）。这台服务没有 TLS，也没有真实鉴权，不能直接对外监听。
- 大模型的 `llm_api_key` 等敏感项**不要写进 config.yaml**（虽然它已被
  `.gitignore` 排除，不会入库，但仍建议走环境变量，见下）——用
  `/opt/aiedu/app.env`（权限 600，同样不入 git）：

```bash
sudo -u aiedu -H tee /opt/aiedu/app.env >/dev/null <<'EOF'
AIEDU_HOST=127.0.0.1
AIEDU_PORT=8900
LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
LLM_API_KEY=sk-xxx
LLM_MODEL=qwen2.5-72b-instruct
EOF
sudo -u aiedu -H chmod 600 /opt/aiedu/app.env
```

没有 Key 也能先跑起来——系统会自动降级为离线确定性表达器（README 第一段演示的
那条主张），等真要接模型再补这个文件即可。

### 3.4 初始化数据库

```bash
cd /opt/aiedu/app
sudo -u aiedu -H python3.11 scripts/migrate.py
sudo -u aiedu -H python3.11 scripts/seed.py     # 知识图谱 / 项目任务 / 知识库，真实数据，一次性
```

**不要在生产库上跑 `make demo` / `scripts/demo.py`**——那个脚本会生成 60 名
*虚构*学生及其八周模拟学习记录，用来在评审现场演示，混进真实班级数据会污染
诊断结果，且没有反向清除的干净方式（事件流只追加，删不掉）。

> **已知缺口**：目前仓库里没有"导入真实学生/教师名册"的脚本——
> `scripts/seed.py` 灌的是知识图谱和项目结构，`scripts/demo.py` 灌的是虚构学生。
> 真实学生上线前需要补一个按 `data/seed/course_ml.yaml` 同样的模式往
> `student` / `teacher` 表插入真实名单（学号、姓名、班级）的小脚本——这是
> Phase 4（`DEVELOPMENT_PLAN.md`）之前必须补的一环，如果需要现在就要，
> 可以单独开一个任务来写，不在这份部署指南范围内。

### 3.5 systemd 服务

模板已放在 [`deploy/aiedu.service`](../deploy/aiedu.service)，装上：

```bash
sudo cp /opt/aiedu/app/deploy/aiedu.service /etc/systemd/system/aiedu.service
sudo systemctl daemon-reload
sudo systemctl enable --now aiedu
sudo systemctl status aiedu
journalctl -u aiedu -f   # 看日志；框架已静默访问日志，只剩启动信息与报错
```

### 3.6 nginx 反向代理 + HTTPS

示例配置在 [`deploy/nginx.conf.example`](../deploy/nginx.conf.example)。

```bash
sudo cp /opt/aiedu/app/deploy/nginx.conf.example /etc/nginx/sites-available/aiedu
# 编辑：把 server_name 换成实际域名
sudo ln -s /etc/nginx/sites-available/aiedu /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

有域名可用 Let's Encrypt 免费证书：

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d aiedu.example.edu.cn
```

没有可用域名（纯校内网络访问）：跳过证书，走 HTTP + 第 4 节的网络层限制即可，
不必为了"看起来正式"强行接 TLS。

### 3.7 防火墙

```bash
sudo ufw allow OpenSSH
sudo ufw allow 'Nginx Full'    # 80/443
sudo ufw enable
```

8900 端口不开放给外部——只有本机 nginx 通过 `127.0.0.1:8900` 访问它。

### 3.8 数据备份

SQLite 备份很轻量，直接 cron：

```bash
sudo -u aiedu -H mkdir -p /opt/aiedu/backup
sudo -u aiedu crontab -e
```

加一行（每天凌晨 2 点，保留最近 30 天）：

```
0 2 * * * sqlite3 /opt/aiedu/app/var/aiedu.db ".backup '/opt/aiedu/backup/aiedu-$(date +\%F).db'" && find /opt/aiedu/backup -name '*.db' -mtime +30 -delete
```

条件允许的话再加一条 `rsync`/`rclone` 把 `/opt/aiedu/backup` 同步到异地，
避免单机故障导致全班数据丢失——`LearningEvent` 是只追加事件流，是这个系统
最不能丢的一份资产。

### 3.9 上线前验收清单

```bash
sudo systemctl status aiedu --no-pager      # active (running)
curl -sf http://127.0.0.1:8900/ >/dev/null && echo OK
cd /opt/aiedu/app && sudo -u aiedu -H python3.11 -m unittest discover -s tests -t tests -q
cd tests && sudo -u aiedu -H python3.11 -m unittest test_layering -q   # 架构铁律
```

浏览器打开 `https://<域名>/`（或校内地址），确认右上角显示"大模型：离线降级"
（或已配置的模型名），切换身份能看到各自应看到的数据、看不到其他班级。

---

## 4. 关于鉴权的强烈建议（部署前必读，优先级高于其他一切美化）

按安全性从高到低、成本从低到高排列，选一个现阶段能落地的：

| 方案 | 做法 | 适合 |
|---|---|---|
| **A. 只在内网/VPN 可达** | 服务器不接公网，只挂校园网或教育网 VPN | **推荐，当前阶段最简单也最安全** |
| B. nginx 层加一道闸 | `nginx.conf.example` 里已给了 Basic Auth 的注释位；也可做 IP 白名单 | 想先给部分外部评审开放时的临时补丁 |
| C. 接入校内统一身份认证 | 替换 `apps/api/auth.py::resolve()`，路由层不用动（注释里已经留好了口子） | 长期正确方案，但是独立的开发任务，不在本次部署范围内 |

**明确不要做的事**：
- 不要把 8900 端口直接开放给公网；
- 不要觉得"学号很长所以够安全"——`resolve()` 完全不做频率限制，脚本枚举学号
  是分钟级的事；
- 不要在没有 A/B/C 任一条落地之前，把系统地址发给班级以外的人。

---

## 5. Git 迭代工作流：部署之后怎么继续演进

关键前提，也是这套流程能做到"轻量但不失手"的原因：迁移是只追加
（`migrations/002_append_only.sql`，`make check` 固化了架构铁律，`make replay`
能把状态从事件流重算出来逐条比对）——这意味着"这次改动能不能上线"本身就是
可自动判定的，`scripts/deploy.sh` 把这个判定做成部署前置门槛，而不是人工肉眼看。

### 5.1 日常开发（本地，不在服务器上改代码）

本地分支开发 → 提交 → push 到 GitHub → 合并到 `main`。
合并前本地务必跑一遍：

```bash
make test    # 全量测试
make check   # 架构铁律自检
```

### 5.2 服务器侧拉取更新

```bash
sudo -u aiedu -H /opt/aiedu/app/scripts/deploy.sh
```

`scripts/deploy.sh`（已放进仓库，见文件）做的事：

1. 记录当前 commit，方便失败回滚；
2. `git fetch` + `git merge --ff-only`（只做快进合并，服务器上不产生分叉）；
3. 跑迁移（`scripts/migrate.py`，本身是幂等的）；
4. 跑全量测试 + 架构铁律检查；**任一失败就自动 `git checkout` 回到部署前的
   commit 并退出，不重启服务**——服务器上永远只跑"验证过"的代码；
5. 全部通过才 `systemctl restart aiedu`。

### 5.3 触发方式：先手动，不急着接自动化

60 人的内部系统，人工看一眼部署输出比"push 即上线"更安全。建议前几周都是
改完 → SSH 上去手动跑一次 `deploy.sh`。真觉得频率高到值得自动化时，
再考虑 GitHub webhook 触发，但那本身是给项目新增一个常驻依赖（webhook
receiver），要和"零依赖"这条工程判断权衡，不建议在验证完部署流程之前引入。

如果想让"合并到 main 之前"就拦住坏改动（比部署时才发现更早一步），
在仓库加一个 GitHub Actions workflow 跑 `make test && make check`，
作为 PR 的必过检查——这个可以随时加，不影响本指南其余部分。

### 5.4 回滚

```bash
cd /opt/aiedu/app
git log --oneline -10
git checkout <上一个好的 commit>
sudo systemctl restart aiedu
```

因为迁移只做新增（表/列），不做删除或改类型，旧代码在新库结构上通常仍能跑；
`deploy.sh` 的自动回滚已经覆盖了"这次部署直接失败"的情况，这里说的是
"部署当时测试都过了，上线后才发现业务问题"的手动回滚路径。

---

## 6. 最小化 checklist（建议直接贴在服务器上的备忘录里）

- [ ] `systemctl status aiedu` 是 `active`，`enable` 过（重启机器能自愈）
- [ ] nginx + （有条件的话）TLS 已配置，8900 端口对外不可达
- [ ] `config.yaml` / `app.env` 权限 600，均未入 git
- [ ] 第 4 节的鉴权方案 A/B/C 至少落地一条，不是"裸奔"
- [ ] 每日 SQLite 备份定时任务已装，`/opt/aiedu/backup` 里能看到文件在增长
- [ ] `scripts/deploy.sh` 已经手动跑过一次，包括故意制造一次测试失败验证过
      自动回滚确实生效
- [ ] `make replay` 在服务器上跑通（"重算校验一致"）

---

## 7. 可选：容器化（仅当基础设施强制要求时）

项目本身不需要容器带来的隔离收益（单进程、SQLite、无系统级依赖冲突），
如果学院统一走 Docker/K8s 部署，最小化思路：

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY . .
RUN pip install --no-cache-dir pyyaml
VOLUME ["/app/var"]
EXPOSE 8900
CMD ["python3", "-m", "apps.api.server"]
```

`var/` 挂成持久卷（SQLite 文件在里面），敏感环境变量通过容器编排的 secret
机制注入，其余（反向代理放前面、鉴权方案照第 4 节选）不变。
