# 接口清单

鉴权：请求头 `X-Auth-Token: teacher:<工号>` 或 `student:<学号>`（也可用 `?token=`）。
接校内统一身份认证时替换 `apps/api/auth.resolve()`，路由层不动。

**所有智能体接口都返回 `confidence` 与 `evidence_count`，禁止裸返回结论。**
证据不足时另有 `caveat: "数据不足，仅供参考"`。

---

## 公开

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/health` | 图谱规模、大模型状态、知识库状态、待重算向量数 |
| GET | `/api/config` | 当前教学参数（阈值、降级次数、BKT 默认值、信号权重） |
| GET | `/api/graph/stats` | 知识点/边/任务映射数量与环检测结果 |

## L1 图谱

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/courses` | 课程列表 |
| GET | `/api/courses/{code}/kps` | 某课程全部知识点（含前置与能力模块） |
| GET | `/api/kps/{id}` | 知识点详情：前置、后续、挡路度、被哪些任务需要 |
| GET | `/api/modules` | 能力模块 M1–M8 |
| GET | `/api/projects` | 项目列表 |
| GET | `/api/projects/{code}/tasks` | 项目任务树（含任务-知识点映射） |

## 模块一：诊断

| 方法 | 路径 | 角色 | 说明 |
|---|---|---|---|
| GET | `/api/diagnosis/class/{course}?klass=` | 教师 | 班级诊断：热力图、Top3 待攻克点 + 根因、需介入学生、降级热点、主动使用率 |
| GET | `/api/diagnosis/student/{id}` | 本人/教师 | 个体薄弱点与根因 |
| GET | `/api/students?klass=` | 教师 | 本班学生列表（跨班 403） |
| GET | `/api/students/{id}/mastery` | 本人/教师 | 掌握度明细 |
| GET | `/api/students/{id}/events` | 本人/教师 | 原始事件流 |

调用班级诊断会记一次 `report_open_log`（Phase 1 验收指标：教师周打开 ≥ 3 次）。

## 错误模式库

| 方法 | 路径 | 角色 | 说明 |
|---|---|---|---|
| GET | `/api/errors/patterns?kp_id=&verified=1` | 教师 | 模式列表（含原始样本） |
| POST | `/api/errors/patterns/{id}/verify` | 教师 | 确认/修正：`{note, description?, root_cause_kp_id?}` |
| GET | `/api/errors/stats` | 教师 | 模式数 / 已确认 / 实例数 |

## 模块二：苏格拉底追问

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/ask/start` | `{kp_id, student_id?}` → 会话与第一个问题 |
| POST | `/api/ask/reply` | `{session_id, text, stuck?}` → 下一个问题（或降级后的提示） |
| GET | `/api/ask/session/{id}` | 完整会话，含降级层级与引用 |

返回的 `plan.escalation_label` ∈ `无 / 关键提示 / 解题框架 / 完整解析（已标记教师介入）`；
`plan.gives_answer` 只有在 L3 才为 true。每次降级都会写一条 `escalation` 事件。

## 模块三：拉取式任务

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/gap?task_id=&student_id=` | 缺口计算（纯确定性，不经大模型） |
| GET | `/api/next-target?task_id=` | 缺口 + 自然语言建议，只推 1–2 个 |
| GET | `/api/board?project=&student_id=` | 任务看板 |
| POST | `/api/tasks/status` | `{task_id, status}`；里程碑完成会触发成就 |

## 模块四：代码与文档审查

| 方法 | 路径 | 角色 | 说明 |
|---|---|---|---|
| POST | `/api/review/source` | 任意 | `{source, path?, use_llm?}` → 两级审查结果 |
| GET | `/api/review/findings?student_id=` | 本人/教师 | 审查发现列表 |
| POST | `/api/review/findings/{id}/action` | 教师 | `{action: accepted\|modified\|rejected}`；只有可靠映射被采纳才回写 L2 |
| GET | `/api/review/rules` | 教师 | 规则命中与否决统计 |

## 模块五：画像与参与度

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/profile/{id}?course=` | 能力雷达、成长曲线、知识覆盖图、项目贡献 |
| GET | `/api/engagement/{id}` | 连续天数、成就、活跃日期 |
| GET | `/api/engagement?klass=&days=` | 班级主动使用率与流失风险名单（教师） |

## 副驾驶与简报

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/copilot` | `{question, task_id?}` → 绑定项目知识库与个人能力档案的回答 |
| GET | `/api/brief/student/{id}` | 学生版简报：下一步建议 + 本周破关 |
| GET | `/api/brief/teacher?klass=` | 教师版简报：异常与需介入事项 |
| POST | `/api/credit-map` | `{description, tech_stack}` → 学分映射建议（叠加规则校验，须委员会审核） |

## 作答回流

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/events` | `{items:[{kp_id, is_correct, event_type, source}]}` |

**唯一的状态写入口。** 试卷、作业、实验任务书的过程数据都从这里回流 L2，
经 `packages/state/tracker` 更新掌握度——绕过它直接写 `mastery_state` 会被数据库触发器拒绝。

## 可审计性与运维

| 方法 | 路径 | 角色 | 说明 |
|---|---|---|---|
| GET | `/api/whoami` | 任意 | 当前身份 |
| GET | `/api/replay/{id}` | 本人/教师 | 从事件流重算并逐条比对（掌握度 + 参与度） |
| GET | `/api/llm/usage` | 教师 | 按用途与模型的调用量与 token 分账 |
| GET | `/api/teacher/opens` | 教师 | 诊断报告打开次数（对系统的观测，非对教师的评价） |

---

## 权限边界

```
学生   → 只能读自己的数据；访问他人 403；`/api/students` 列表 403
教师   → 可读白名单班级；跨班 403；白名单为空表示可见全部
```

由 `apps/api/auth.assert_can_view_student / assert_can_view_class` 统一拦截，
`tests/test_api.py` 固化了这些边界。
