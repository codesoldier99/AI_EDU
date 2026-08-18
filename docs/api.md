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

## 学习工作台（出题测练 / 分步解题 / 限域调研 / 图示）

设计说明与取舍理由见 `docs/deeptutor-研判.md`。

### 题库（教师侧）

| 方法 | 路径 | 角色 | 说明 |
|---|---|---|---|
| POST | `/api/quiz/draft` | 教师 | `{kp_id, n, qtype}` → 模型起草题目。**必须挂教材依据**，无依据拒绝出题；产出一律 `teacher_verified=0` |
| GET | `/api/quiz/review-queue` | 教师 | 待审草案 + 题库统计 |
| POST | `/api/quiz/review/{qid}` | 教师 | `{action: accept\|reject\|edit, patch?}`。reject 只标 `retired`，不删除 |
| GET | `/api/quiz/bank?kp_id=` | 教师 | 某知识点下的题；不带 `kp_id` 只返回统计 |

### 练习（学生侧）

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/practice-plan` | 只看"该练什么"与理由，不出卷。排序依据仅四类：`retention` / `verify` / `gap` / `root_cause`；附 `missing_bank`（该练但题库没题） |
| POST | `/api/quiz/assemble` | `{task_id?, per_kp?, limit?}` → 组卷。返回的题目**不含 answer 与 rationale** |
| POST | `/api/quiz/submit` | `{paper_id, answers:{qid: 作答}}` → 批改。判不了的题 `is_correct=null`、不给解析、转人工 |
| GET | `/api/quiz/history/{id}` | 作答记录本身；**不返回正确率** |
| GET | `/api/quiz/pending` | 教师：确定性判分放弃的作答队列 |
| POST | `/api/quiz/grade/{event_id}` | 教师：`{is_correct, note}` → **追加一条新事件**，不修改旧事件 |

### 分步解题

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/solve/start` | `{problem, kp_id?}` → 拆步骤，只返回第一步的"要做什么" |
| POST | `/api/solve/answer` | `{session_id, text, stuck?}` → 判定这一步。答对才前进；连续答不上来逐级降级 |
| GET | `/api/solve/{session_id}` | 会话视图。**未通过且未降级的步骤，`expected` 恒为空串** |
| GET | `/api/solve/sessions/{id}` | 最近的解题会话 |

### 限域调研与图示

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/research` | `{topic, project?}` → 只查院内三库的结构化报告。每节带 `groundedness`，低于阈值标 `grounded=false` |
| GET | `/api/notes/{id}` | 调研记录列表 |
| GET | `/api/note/{note_id}` | 报告全文（Markdown） |
| GET | `/api/figure?kind=&kp_id=` | `kind ∈ {mastery_bars, retention_curve, ability_radar, root_cause_chain}`。返回服务端生成的 SVG + 模型写的解读；根因链另附 Mermaid 源码 |
| GET | `/api/skills` | 公开：已装载的教学技能包及其文件指纹 |

## 在线考试

设计见 `docs/measurement-plan.md`。**考生令牌 `exam:<随机串>` 与教学令牌完全隔离**：
它只能访问下表中标注 `examinee` 的接口，碰不到掌握度、碰不到别人、碰不到答案。

| 方法 | 路径 | 角色 | 说明 |
|---|---|---|---|
| POST | `/api/exam/login` | 公开 | `{exam_code, sid, ticket}` → 会话令牌。全系统唯一对未鉴权请求开放的写接口 |
| GET | `/api/exam/paper` | examinee | 本人卷面。**不含 answer / rationale / keywords / grader** |
| POST | `/api/exam/save` | examinee | `{question_id, response}`，前端每 15 秒兜底保存一次 |
| POST | `/api/exam/submit` | examinee | 交卷 → 冻结卷面 → 判分 → 判定结果写事件流 |
| GET | `/api/exam/heartbeat` | examinee | 剩余秒数。**倒计时以此为准，不信客户端时钟** |
| GET | `/api/exam/list` | 教师 | 考试列表 |
| GET | `/api/exam/{id}/spec` | 教师 | 试卷快照（发布后冻结） |
| POST | `/api/exam/{id}/publish` | 教师 | 发布即冻结，之后不能改题 |
| POST | `/api/exam/{id}/tickets` | 教师 | 签发一次性准考口令 |
| GET | `/api/exam/{id}/monitor` | 教师 | 考场监控：谁在考、谁交了、谁没来 |
| POST | `/api/exam/{id}/sweep` | 教师 | 收回所有超时未交的卷 |
| GET | `/api/exam/{id}/pending` | 教师 | 待人工判分队列（程序题在此） |
| POST | `/api/exam/score` | 教师 | `{session_id, question_id, score, note}` → 人工给分，同时补写事件 |
| GET | `/api/exam/{id}/ranking?cutoff=53` | 教师 | 排名与切线，含并列裁决依据与风险警告 |
| GET | `/api/exam/{id}/export` | 教师 | 分析数据集：running variable、处理变量、中心化分数 |

考场页面：`/exam.html?exam=<考试代码>`（独立页面，无任何通往教学系统的入口）。

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
