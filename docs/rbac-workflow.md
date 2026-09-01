# 权限与流程管理（RBAC / Workflow）

## 之前有什么

- 演示令牌：`teacher:<工号>` / `student:<学号>`（可猜）
- 路由层仅做 `role=` 字符串相等；考试令牌 `exam:` 已隔离
- 班级对象权限：`assert_can_view_student` / `assert_can_view_class`
- 各待审队列分散在 quiz / kpmatch / review / errors / exam，无统一状态机与审计

## 缺口与风险

- 明文令牌可猜 → 越权面大（考试侧已用随机票规避，教学侧仍弱）
- 无持久化角色-权限矩阵，助教/教务无法细粒度授权
- 教师裁决路径无统一认领/审计；`workflow_event` 级留痕缺失
- 部分裁决接口只拦 `role=teacher`，无法表达「可判分但不可采纳映射」

## 本实现

### RBAC（`packages/auth` + `migrations/009_rbac_workflow.sql`）

| 角色 | 要点 |
|---|---|
| `admin` | 全权限；空班级名单 = 全班可见 |
| `teacher` | 诊断、审核、考试发布、课件、流程 |
| `ta` | 判分/监考/流程子集，**无** `kpmatch.decide` / `exam.publish` |
| `student` | 仅本人 |

令牌：

1. `session:<不透明>` —— `POST /api/auth/login` 签发（推荐）
2. `admin:A001` / `teacher:T001` / `student:2026001` —— 演示兼容
3. `exam:…` —— 不变，仍只能进考生接口

### Workflow（`packages/workflow`）

种类：`quiz_draft` / `kp_mapping` / `review_finding` / `error_pattern` / `exam_grade` / `quiz_grade`

状态：`draft → pending_review → claimed → approved|rejected|retired`

- `workflow_item`：统一索引（不取代业务表）
- `workflow_event`：只追加审计（触发器禁止 UPDATE/DELETE）
- 原有教师 API 裁决后会 `record_external_decision` 回写

### 演示

```bash
make setup
make test
make check

# 明文演示
curl -H 'X-Auth-Token: teacher:T001' http://127.0.0.1:8900/api/whoami

# 换不透明会话
curl -X POST http://127.0.0.1:8900/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"kind":"teacher","ident":"T001"}'
# → {"token":"session:…", …}

# 同步并查看统一待审
curl -X POST -H 'X-Auth-Token: teacher:T001' \
  -H 'Content-Type: application/json' -d '{}' \
  http://127.0.0.1:8900/api/workflow/sync
curl -H 'X-Auth-Token: teacher:T001' \
  'http://127.0.0.1:8900/api/workflow/queue?state=open'

# 教务
curl -H 'X-Auth-Token: admin:A001' http://127.0.0.1:8900/api/auth/matrix
```

教师工作台侧栏新增「统一待审」；公开说明见 `GET /api/auth/demo`。
