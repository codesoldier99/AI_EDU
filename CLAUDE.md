# CLAUDE.md

福建理工大学人工智能与交通工程学院 · 苏格拉底式 AI 教学系统

---

## 1. 项目一句话

把学生的真实学习状态沉淀为结构化数据，把教师的判错经验积累为可复用资产，让大模型只负责表达。

服务对象：院长工作室实验班（约 60 名学生、10 名导师、5–10 个真实产业项目）。

**核心范式：知识由项目需求拉取，不由课程顺序推送。**

---

## 2. 架构铁律（违反即返工，优先级高于一切）

> 这五条不是靠自觉，已由 `tests/test_layering.py`（AST 静态检查）与
> `migrations/002_append_only.sql`（数据库触发器）双重固化。`make check` 可随时验证。

1. **L3 不得直接写 L2 状态。** 生成层只能读取学生状态、写入行为事件；所有掌握度变更必须经 `packages/state` 的追踪算法产生。

2. **任何可积累的东西，不得依赖任何可替换的东西的内部实现。** 知识图谱、错误模式库、学生状态历史不得存储模型专有格式（embedding 除外，且须记录模型版本以便重算）。

3. **大模型输出不得作为事实来源。** 凡涉及数值计算、代码正确性、成绩判定，必须走确定性工具，模型只做解释与表达。

4. **不确定必须显式表达。** 状态查询返回 `confidence`，证据不足时前端必须显示"数据不足，仅供参考"。

5. **禁止以"当场正确率"或"降低难度"作为任何模块的优化目标。**
   已有证据：不加限制的 AI 辅助会使学生独立考试成绩下降约 17%；引导式提示则显著优于传统方式。
   合法的优化目标只有四个：**延迟后测表现、迁移任务表现、项目产出质量、主动使用率**。

---

## 3. 两条设计范式（决定功能长什么样）

### 3.1 拉取式学习（Pull, not Push）

传统做法按知识点依赖顺序排课，学生"先学半年螺丝刀再拆引擎"。本系统反过来：

```
学生接到项目任务
  → 系统识别该任务所需知识点
  → 计算缺口 = 所需 − 已掌握
  → 只推送此刻最挡路的 1–2 个
  → 学完立刻用回项目
```

**由此，知识图谱的角色发生转变：**

| | 旧角色 | 新角色 |
|---|---|---|
| 依赖边用来 | 排定教学顺序 | 回答"要做这个，还缺什么" |
| 图谱本质是 | 教学顺序表 | 按需检索索引 |
| 学习起点是 | 课程第一章 | 当前卡住的任务 |

拓扑序仍保留，但**只用于计算缺口内部的先后，不用于安排全员进度**。

### 3.2 只对"坚持"给正反馈

| 应奖励 | 禁止奖励 |
|---|---|
| 连续天数、中断后回归 | 正确率 |
| 攻克一个根因知识点（破关） | 做题数量 |
| 项目里程碑、真机跑通 | 停留时长 |
| 失败后重来并通过 | 一次做对 |

**原则：让"坚持"上瘾，不让"简单"上瘾。**

区分两种困难——
- **无意义的痛苦**（重复抄写、听已会内容、等全班进度）→ 系统应当消灭
- **有意义的困难**（想不出来时的挣扎、提取练习、间隔重复）→ 系统必须保留

追问降级机制是为了防止挫败感摧毁动机，**不是为了让学生轻松**。

---

## 4. 技术栈

| 层 | 目标选型 | 当前实现（Phase 0/1） | 说明 |
|---|---|---|---|
| 后端 | Python 3.11 + FastAPI | 标准库 HTTP + FastAPI 风格路由 | 路由函数签名可原样迁移 |
| 数据库 | PostgreSQL 16 + pgvector | SQLite（SQL 写成双兼容子集） | **单库解决图、状态、向量**，不引入 Neo4j/Milvus |
| 任务队列 | Celery + Redis | 同步调用 | 仅用于异步批处理，尚不需要 |
| 大模型 | Qwen / DeepSeek，OpenAI 兼容接口 | 同左 + 无 Key 时离线降级 | 经 `packages/llm` 封装，可整体替换 |
| 前端 | React + TypeScript + Tailwind | 原生 JS + CSS，零构建 | 先保证零依赖可演示 |
| 代码信号源 | 自建 Gitea + CI | `adapters/gitea` 读本地 git log | 换成 API 调用即可 |
| 测试 | pytest + pytest-asyncio | unittest（79 用例） | 用例可直接被 pytest 收集 |

**为什么先做零依赖版：** 演示环境与生产环境必须跑同一套逻辑。所有可替换件都在接口后面
（`packages/llm`、`packages/rag`、`packages/core/db`），替换时业务代码不动——
`tests/test_rag_llm.py` 用一个假模型客户端证明了这一点。

**为什么不用 Neo4j：** 单课程 300–500 知识点，PostgreSQL 递归 CTE 足够，省一套运维。接口已在 `packages/graph` 隔离，将来成瓶颈再迁。

---

## 5. 目录结构

```
/apps
  /api                FastAPI，仅路由与鉴权，无业务逻辑
  /web                前端
/packages
  /graph              L1 知识图谱：知识点、依赖边、能力模块、任务-知识点映射
  /state              L2 学生状态：BKT、掌握度、能力画像
  /engagement         参与度：连续天数、破关、回归（只读事件流，不回写状态）
  /agents             L3 智能体：诊断、追问、任务、审查、画像、副驾驶
  /rag                检索：三个知识库、混合召回
  /adapters           项目数据适配器（每项目一个）
  /llm                大模型统一封装（可替换层）
  /errors             错误模式库（核心资产）
/migrations
/scripts
/docs
/tests
```

**依赖方向严格单向：** `apps → agents → {state, engagement, rag, llm} → graph`
禁止反向依赖；禁止 `graph` 引用 `llm`；**禁止 `engagement` 写 `state`**。

---

## 6. 核心数据模型

```python
# packages/graph — L1 可积累
KnowledgePoint(id, course_id, code, name, description, granularity)
Prerequisite(from_kp_id, to_kp_id, edge_type, strength)   # DAG，禁止环
AbilityModule(id, code)                                    # M1–M8
KPAbilityLink(kp_id, module_id, weight)

ProjectTask(id, project_id, name, parent_id, milestone)
TaskKPLink(task_id, kp_id, necessity)   # ★ 拉取式核心：任务需要哪些知识点

# packages/state — L2 可积累（系统核心资产）
Student(id, cohort, enrolled_at)
MasteryState(student_id, kp_id, p_mastery, confidence, evidence_count,
             last_event_id, updated_at)
LearningEvent(id, student_id, kp_id, event_type, is_correct, source,
              source_ref, occurred_at, payload)    # 只追加，不修改
AbilityProfile(student_id, module_id, level, updated_at)

# packages/engagement — 派生态，可从事件流重算，不参与掌握度计算
StreakState(student_id, current_days, longest_days, last_active_date)
Achievement(id, student_id, kind, ref_id, earned_at)
# kind ∈ {breakthrough, milestone, comeback, first_run}

# packages/errors — 最有价值的资产
ErrorPattern(id, kp_id, description, root_cause_kp_id, occurrence_count,
             first_seen, teacher_verified)
ErrorInstance(id, pattern_id, student_id, event_id, raw_text)

# 项目侧
Project(id, name, type, adapter_key)
ProjectSignal(id, project_id, student_id, signal_class, value,
              occurred_at, raw_ref)
```

**BKT 参数**按知识点存：`p_init, p_transit, p_slip, p_guess`，冷启动用课程级默认值。

**LearningEvent 是只追加事件流**——任何状态（含 engagement）都可从事件流重算，这是可审计性的基础。禁止直接 UPDATE 掌握度而不写事件。

---

## 7. 五类标准项目信号

适配器必须把异构数据归一化为这五类：

```
code_commit      提交行为：频次、粒度、变更量、评审记录
build_test       构建质量：CI 成功率、测试覆盖、缺陷修复周期
runtime          运行结果：任务成功率、关键指标、异常重试、调参轨迹
doc_delivery     文档交付：设计文档、技术报告、答辩材料完整性
collaboration    协作记录：看板流转、评审互动、接口沟通
```

新项目接入 = 实现 `packages/adapters/base.py` 的 `ProjectAdapter`，**不改核心代码**。
接第 5 个和第 10 个适配器的成本应当相同。

---

## 8. 开发规范

- **中文注释**，变量与函数名用英文
- 所有对外 API 返回 `confidence` 与 `evidence_count`，禁止裸返回结论
- 智能体之间只传结构化消息（Pydantic model），**禁止自由对话**
- 任何 LLM 调用必须经 `packages/llm`，禁止业务代码直接 import openai
- 数据库写操作走 repository 层，禁止在 agent 里写 SQL
- 新增功能必须附 pytest；涉及状态变更的必须有事件流重算测试

---

## 9. 常用命令

```bash
make setup                       # 一键就绪：迁移 + 种子 + 60 人演示数据
make dev                         # 启动本地环境（API + 前端，默认 :8900）
make migrate                     # 数据库迁移
make seed [course|projects|kb]   # 导入知识图谱 / 项目任务映射 / 三个知识库
make demo N=60                   # 生成虚拟班级演示数据
make test                        # 全量测试
make test-state                  # 仅测状态层（改 BKT 后必跑）
make check                       # 架构铁律静态检查（本文件第 2、11 节）
make lint                        # ruff；无 ruff 时退化为内置自检
make replay STUDENT=<id>         # 从事件流重算状态，校验一致性
make gap STUDENT=<id> TASK=<code># 打印任务知识缺口（调试拉取逻辑）
```

---

## 10. 领域术语表

| 中文 | 代码 | 含义 |
|---|---|---|
| 知识点 | knowledge point, KP | 可独立命题、可独立判定掌握的最小单元 |
| 前置依赖 | prerequisite | 学 B 前必须先会 A |
| 知识缺口 | gap | 任务所需知识点 − 学生已掌握 |
| 挡路度 | blocking severity | 某缺口知识点阻塞多少后续任务 |
| 根因回溯 | root cause tracing | 沿依赖边向上找真正的薄弱点 |
| 掌握度 | mastery | 0–1 概率值，非分数 |
| 能力模块 | ability module, M1–M8 | 实验班能力维度划分 |
| 错误模式 | error pattern | 某知识点上反复出现的典型错误及归因 |
| 破关 | breakthrough | 攻克一个根因知识点 |
| 回归 | comeback | 中断后重新活跃 |
| 追问降级 | escalation | 学生反复卡住时逐级给出更多提示 |
| 免听不免考 | — | 理论课可自学但必须参加统考 |
| 30/70 考核 | — | 专业课 30% 笔试 + 70% 实践 |

---

## 11. 禁止事项

- ❌ 让大模型判断"学生是否掌握某知识点"——必须由 BKT 依据事件产生
- ❌ **以当场正确率、做题数、停留时长作为任何优化目标或激励对象**
- ❌ **为提升体验而降低任务难度或提前给答案**
- ❌ 按拓扑序给全员统一排课（应按项目缺口拉取）
- ❌ 向学生展示"课程进度百分比"（只有知识点掌握度）
- ❌ 追问引擎直接给最终答案（除非触发降级且已记录事件）
- ❌ 生成任何针对**教师**的评价性数据
- ❌ `packages/engagement` 写入 `packages/state`
- ❌ 在 `graph` / `state` 中引入对具体大模型的依赖
- ❌ 为某个特定项目在核心代码写 if 分支（应写 adapter）
- ❌ 直接 UPDATE MasteryState 而不写 LearningEvent

---

## 12. 当前阶段

> 每次开工前先读 `DEVELOPMENT_PLAN.md` 确认当前阶段与本周任务。

**Phase 0（已完成）：** 数据库 schema 与只追加约束、首门课知识图谱（183 知识点 / 224 依赖边）、
任务-知识点映射（2 个项目 / 35 任务 / 115 条映射）、LLM 封装层与可替换性验收。
验收：`make replay` 全班一致；`tests/test_rag_llm.py` 证明切模型不改业务代码。

**Phase 1（已完成）：** 班级/个体诊断与根因回溯、错误模式库 v1、参与度基线与主动使用率观测、
教师报告打开次数统计。

**Phase 2（已完成主体）：** 追问策略引擎与三级降级留痕、两级代码审查流水线与
"宁缺毋滥"的知识点回写。

**Phase 3（已完成主体）：** 拉取式缺口调度与挡路度排序、项目适配器（AGV/视觉/Gitea）、
只奖励坚持的激励系统、学生端与副驾驶。

**待推进：** Phase 4 效果验证的数据采集（对照组、延迟后测），
以及 `docs/decisions.md` 里 13 项参数的教师拍板。
