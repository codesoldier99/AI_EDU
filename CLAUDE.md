# CLAUDE.md

福建理工大学人工智能与交通工程学院 · 院长实验班 AI 教学系统

---

## 1. 项目一句话

把学生的真实学习状态沉淀为结构化数据，把教师的判错经验积累为可复用资产，让大模型只负责表达。

服务对象：院长工作室实验班（约 60 名学生、10 名导师、5–10 个真实产业项目）。

**核心范式：知识由项目需求拉取，不由课程顺序推送。**

---

## 2. 架构铁律（违反即返工，优先级高于一切）

> 这七条不是靠自觉，已由 `tests/test_layering.py`（AST 静态检查）与
> `migrations/002_append_only.sql`（数据库触发器）双重固化。`make check` 可随时验证。

1. **L3 不得直接写 L2 状态。** 生成层只能读取学生状态、写入行为事件；所有掌握度变更必须经 `packages/state` 的追踪算法产生。

2. **任何可积累的东西，不得依赖任何可替换的东西的内部实现。** 知识图谱、错误模式库、学生状态历史不得存储模型专有格式（embedding 除外，且须记录模型版本以便重算）。

3. **大模型输出不得作为事实来源。** 凡涉及数值计算、代码正确性、成绩判定，必须走确定性工具，模型只做解释与表达。

4. **不确定必须显式表达。** 状态查询返回 `confidence`，证据不足时前端必须显示"数据不足，仅供参考"。

5. **禁止以"当场正确率"或"降低难度"作为任何模块的优化目标。**
   已有证据：不加限制的 AI 辅助会使学生独立考试成绩下降约 17%；引导式提示则显著优于传统方式。
   合法的优化目标只有四个：**延迟后测表现、迁移任务表现、项目产出质量、主动使用率**。

6. **一次做对不算掌握。** 达标只是"暂定掌握"；只有跨时间再次做对才算**已验证掌握**，
   且掌握度随时间衰减、到期必须叫回来复检。系统的义务不是把人送过及格线就撒手，
   而是陪到他真的会、并且能证明。
   实现见 `packages/state/verification.py`；衰减**只算不写**——掌握度必须始终等于
   事件流折叠的结果，否则 `make replay` 失去意义（与铁律 1 同源）。

7. **判不了 ≠ 判错。** 确定性判分拿不准时（关键词覆盖落在判定带间、作答里出现多个数字、
   选项解析不出来），必须返回"未判定"并进教师人工队列，**禁止猜一个结果写进事件流**。
   同理，证据权重随判定的确定性走：教师判分 1.0 > 规则判分 1.0 > 数值校验 0.7 > 文本规则 0.5。
   理由见 `docs/deeptutor-研判.md` §4：BKT 的证据流是本系统最贵的东西，
   掺进去一条"其实没判出来"的证据，后面所有诊断都会歪，而且歪得看不出来。
   实现见 `packages/quiz/grader.py`。

---

## 3. 四条设计范式（决定功能长什么样）

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

### 3.3 掌握的三个问题

"会不会"是 BKT 回答的。但只回答这一个问题的系统，会在三个地方骗自己：

| 问题 | 不问的后果 | 本系统的答法 |
|---|---|---|
| **验证了吗** | 当堂练到做对就记为掌握，下周全忘 | 两次做对间隔 ≥ `verify_gap_days` 才算已验证 |
| **还在吗** | 学期初的达标一直挂在那里，无人回访 | 按半衰期衰减，掉到 `retention_threshold` 以下进复检队列 |
| **是自己挣来的吗** | 把认知外包出去的正确率当成学习成效 | 区分"提示前做对"与"提示后做对"，提示依赖度可见 |

第三条尤其要小心：**提示依赖度是诊断指标，禁止作为优化目标、激励对象或排名依据**。
指标一旦被当成目标就会被优化，然后失去意义——这与铁律 5 是同一条道理。

三者全部是**派生视图**，只读事件流。它们让"陪到你真正掌握"这句话变成可判定的规则，
而不是一句产品文案。

### 3.4 顺着学法，而不只是顺着难度

自适应若只做难度调节，仍然是千人一面的另一种形态。系统必须记住：
**对这个学生而言，反例质疑管用、类比迁移不管用**。
`verification.style_effectiveness` 按实测有效率给追问方式排序，反哺 `AskingStrategy`；
证据不足时不猜，用默认序。判定"是否有进展"走确定性规则，不是模型说了算。

---

## 4. 技术栈

| 层 | 目标选型 | 当前实现（Phase 0/1） | 说明 |
|---|---|---|---|
| 后端 | Python 3.11 + FastAPI | 标准库 HTTP + FastAPI 风格路由 | 路由函数签名可原样迁移 |
| 数据库 | PostgreSQL 16 + pgvector | SQLite（SQL 写成双兼容子集） | **单库解决图、状态、向量**，不引入 Neo4j/Milvus |
| 任务队列 | Celery + Redis | 同步调用 | 仅用于异步批处理，尚不需要 |
| 大模型 | Qwen / DeepSeek，OpenAI 兼容接口 | 同左 + 无 Key 时离线降级 | 经 `packages/llm` 封装，可整体替换 |
| 前端 | React + TypeScript + Tailwind | 原生 JS + CSS，零构建 | 先保证零依赖可演示 |
| 3D 图谱 | three.js | three.js（**本地 vendor，不走 CDN**） | 演示环境常常没有外网 |
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
    graph3d.js        知识宇宙：three.js 渲染壳（不可单测）
    kg-core.js        知识宇宙：布局物理 / 色阶 / 降噪（纯逻辑，Node 可测）
    vendor/           three.js 与 OrbitControls 的本地副本
/packages
  /graph              L1 知识图谱：知识点、依赖边、能力模块、任务-知识点映射
  /state              L2 学生状态：BKT、掌握度、能力画像
  /engagement         参与度：连续天数、破关、回归（只读事件流，不回写状态）
  /agents             L3 智能体：诊断、追问、任务、审查、画像、副驾驶
  /rag                检索：三个知识库、混合召回
  /adapters           项目数据适配器（每项目一个）
  /llm                大模型统一封装（可替换层）
  /errors             错误模式库（核心资产）
  /quiz               题库 + 确定性选题 + 确定性判分（可积累资产，**不依赖大模型**）
  /tools              确定性工具箱：安全算术求值、落地性检查、文本匹配（铁律 3 的实体，零依赖）
  /skills             教学技能包装载（SKILL.md，教师写 Markdown 即扩展）
  /exam               在线考试：一次性准考凭据、服务端计时、判分、切线、导出
  /state/verification.py   掌握的质量：跨时间验证、遗忘复检、提示依赖、学法有效率
  /state/coverage.py       培养方案覆盖度：项目铺开全景、学分认定、悬空需求队列
  /agents/universe.py      知识宇宙的视图投影（只读 L1+L2，什么都不写）
  /agents/quiz.py          出题（草案进待审队列）/ 组卷 / 批改
  /agents/solve.py         分步解题：一次只交出一步
  /agents/research.py      限域调研：只查院内三库，产出计入交付物不计入掌握度
  /agents/visualize.py     图示：SVG 由确定性代码生成，模型只写解读
  /agents/kpmatch.py       知识点自动匹配：确定性检索 + 依赖边扩展，候选进待审队列
  /tools/lexmatch.py       中英混排 TF-IDF 匹配（判定部分，零依赖、可复算）
  /adapters/dac3d.py       DAC-3D 产业项目适配器：读真实 git 仓库 + 交出任务语料
/skills               教学技能包（教师可写，进 git，不从网上装）
/migrations
/scripts
/docs
/tests
```

**依赖方向严格单向：** `apps → agents → {quiz, exam, state, engagement, rag, llm, skills} → graph`，
`tools` 是叶子（谁都能用它，它谁都不用）。
禁止反向依赖；禁止 `graph` 引用 `llm`；**禁止 `engagement` 写 `state`**；
**禁止 `quiz` / `exam` / `skills` / `tools` 引用 `llm`**——判分与技能包一旦掺进模型，
成绩就不可复算了。考试尤其：分数决定谁进实验班，也是两年纵向研究的基线。

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

# packages/graph — 培养方案（学分认定的坐标系）
Program(id, code, name, level, klass, version)              # 人工智能（专升本）·院长实验班
ProgramCourse(program_id, course_id, module, semester,      # 同一门课可出现在多个方案里，
              credit, hours, exam_type)                     # 各自的学期与学分不同
CoursePrefix(prefix, course_id)          # 知识点代码前缀 → 课程，用于给悬空需求归位
KPDemand(id, code, course_code, demanded_by, project_code,  # ★ 拉取式图谱建设：
         kind, status, first_seen)                          # 引用了还没建的知识点 = 一条需求
# 知识点归属**唯一**（knowledge_point.course_id）：一松，同一个点会在三门课里各算一次学分

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

# packages/quiz — 可积累资产（与错误模式库同级）
Question(id, kp_id, qtype, stem, options, answer, keywords, rationale,
         difficulty, origin, grader, citations, teacher_verified, retired)
# origin=llm 的题一律 teacher_verified=0；只能标 retired，禁止 DELETE（触发器固化）
QuizPaper(id, student_id, purpose, question_ids, reasons)   # 只记发了什么、为什么发
# ★ 作答结果不建表：作答即事件，写 LearningEvent(event_type='quiz')，
#   source_ref='paper:<pid>#question:<qid>'，判分细节进 payload

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

**掌握的质量不建表**：验证状态、遗忘后估计、提示依赖度全部由事件流现算
（`packages/state/verification.py`）。凡是能从事件流算出来的，就不要落第二份真相——
多一张表就多一个会和事件流对不上的地方。

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
- **迁移文件名一旦被应用过就不许改名。** 迁移是按**文件名**记录在 `schema_migration`
  里的，改名等于让所有已升级的库重跑一遍。多人并行开发时编号撞车（两个 `003_`）
  是正常的，也无害——按文件名排序仍然确定。撞车了就让它撞着，别改名。
  新迁移取号请先 `ls migrations/` 看最大值再 +1，并一律写 `IF NOT EXISTS`。
- **测试不要断言运行环境。** 需要离线表达器就用 `tests/base.OfflineLLMMixin` 钉死，
  别依赖"本机没配 API Key"——同事配了 Key 你的测试就红，而红的原因与被测行为无关
- **前端与后端同构**：可确定性计算的部分抽成纯模块（`kg-core.js`）并单测，
  渲染壳只管画。这与 agents 的 `plan()` / `express()` 是同一条规矩
- **前端不得引入 CDN 依赖**：第三方库一律 vendor 到 `apps/web/vendor/`，
  演示环境必须在没有外网时完整可用

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
node tests/web_core.test.mjs     # 知识宇宙的布局物理 / 色阶 / 降噪（已并入 make test）
make replay STUDENT=<id>         # 从事件流重算状态，校验一致性
make gap STUDENT=<id> TASK=<code># 打印任务知识缺口（调试拉取逻辑）
make practice STUDENT=<id>       # 打印此刻该练什么及其理由（调试选点逻辑）
make skills                      # 列出已装载的教学技能包
make kpmatch A="propose PRJ-DAC" # 知识点自动匹配（propose/queue/why/accept/reject/stats/eval）
make signals                     # 采集全部项目的五类标准信号（幂等，可反复跑）
make program A="map PRJ-DAC"     # 培养方案视图（map/demand/coverage/courses）
make demand                      # 悬空需求队列：下一门课该先建哪几个知识点
make deck D=pm                   # 生成汇报 PPT（all=院领导版 / pm=教师版）
make test-study                  # 仅测学习工作台（题库/判分/解题/调研/图示）
make test-exam                   # 仅测在线考试（凭据/计时/判分/切线/权限）
make test-kpmatch                # 仅测知识点自动匹配（候选边界/证据/依赖边扩展）
make test-program                # 仅测培养方案（归属移交/需求队列/学分认定）
make exam-setup                  # 导入并发布选拔考卷
make exam A="tickets ML-SELECT-2026"   # 考试运维（见 scripts/exam.py）
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
| 已验证掌握 | validated mastery | 跨时间再次做对，而非当堂练到做对 |
| 复检 | retention review | 掌握度按遗忘曲线掉到阈值以下，需重新确认 |
| 提示依赖度 | assistance dependency | 有多少"做对"发生在提示之后（诊断用，非考核用）|
| 知识宇宙 | knowledge universe | 3D 知识图谱视图 |
| 学习工作台 | study workbench | 出题测练 / 分步解题 / 限域调研 / 图示，共用同一学生上下文 |
| 待审草案 | pending draft | 模型出的题，教师确认前不得发给学生 |
| 未判定 | pending | 确定性判分拿不准，转人工，不写掌握度证据 |
| 候选映射 | mapping candidate | 机器提的任务-知识点映射，教师采纳前不进任何计算 |
| 依赖边扩展 | prereq expansion | 把命中知识点的前置也提为候选，召回率 47%→64% |
| 培养方案 | program | 专业的课程清单与学分要求；课程是知识点的**归集视图** |
| 归属移交 | reattribution | 把知识点划给培养方案里的真实课程；**只改归属，不改代码** |
| 悬空需求 | kp demand | 任务引用了还没建的知识点；按需求次数排序 = 图谱建设优先级 |
| 覆盖度 | coverage | 学生在某课程**已验证掌握**的知识点占该课已建知识点的比例 |
| 落地性 | groundedness | 生成文字与检索材料的 n-gram 重合率，低于阈值标"低支撑" |
| 教学技能包 | skill pack | 教师用 Markdown 写的"怎么问、怎么出题"，不改代码即生效 |
| 概念锚题 | anchor item | 跨年反复重测的固定题目，**考后不讲评、不进日常组卷池** |
| 准考口令 | exam ticket | 一次性 6 位口令，开考凭据，不是账号密码 |
| 断点回归 | RDD | 按分数线切分实验班时，卡线两侧近乎随机，可排除选择偏倚 |
| 归一化增益 | Hake's g | (后测−前测)/(100−前测)，消掉起点差异，纵向可比 |
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
- ❌ 把遗忘衰减写回 MasteryState（衰减是视图，不是事实）
- ❌ 把"提示依赖度""无提示做对率"做成激励、排名或考核指标
- ❌ 把一次做对当成掌握并从此不再回访
- ❌ 前端引用 CDN 上的 JS/CSS/字体
- ❌ 把模型生成的题目直接发给学生（必须先过教师审核）
- ❌ 让知识点自动匹配直接写入 `task_kp_link`（映射是学分认定依据，只能教师采纳后写入）
- ❌ 给候选映射做「一键采纳全部高置信度」（点下去，导师标注就变回了模型推测）
- ❌ 适配器猜测 git 作者对应哪个学生（映射不到就跳过，宁可少记不能记错人）
- ❌ 让一个知识点同时归属多门课（学分会重复计，且看不出来）
- ❌ 图谱只建了几个点就下"覆盖度 X%、可认定学分"的结论（假精度，属"判不了"）
- ❌ 把项目专有知识硬塞进某门课凑覆盖度（硬凑一次，认定的可信度就没了）
- ❌ 为了让归属整齐而修改已被考卷或事件流引用的知识点代码（代码是身份，归属才是属性）
- ❌ 组卷时把答案或解析一起下发给学生（批改后才给）
- ❌ 确定性判分拿不准时猜一个结果写进事件流（必须转人工）
- ❌ 分步解题在未降级前交出任何一步的结论
- ❌ 让调研 / 图示这类产出物影响掌握度
- ❌ 让教学技能包影响"算不算掌握"（它只能影响怎么说）
- ❌ 展示"本次正确率"这类当场指标
- ❌ 把概念锚题拿去讲评、返还或放进日常练习（泄漏一次，纵向比较报废）
- ❌ 用 `student:<学号>` 这类可猜令牌参与考试（令牌可猜等于分数可改）
- ❌ 让客户端时间参与考试计时判定
- ❌ 把考试中"判不了"的题当成 0 分计入排名（空白才是 0 分）
- ❌ 两年观测期内从实验班淘汰学生（差异流失会摧毁整个准实验设计）

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

**Phase 3.5（已完成）：** 掌握的质量（跨时间验证 / 遗忘复检 / 提示依赖 / 学法适配）、
知识宇宙 3D 图谱（three.js，含根因链点亮）。

**Phase 3.6（已完成）：** 吸收 DeepTutor 的能力面——题库与确定性判分、针对性练习选点、
分步解题（一次一步 + 降级留痕）、限域调研（三库内检索 + 落地性检查）、
确定性 SVG 图示、教学技能包（SKILL.md）。取舍理由见 `docs/deeptutor-研判.md`。

**Phase 4（进行中）：** 效果验证的数据采集。已完成入学选拔考系统与 T0 基线设计
（`packages/exam`、`docs/measurement-plan.md`）：128 人全部录取、严格按分数线取 53 人，
落选 75 人构成天然对照组，切分点构成断点回归设计。

**Phase 4.1（已完成）：** 第一个**真实在研产业项目**接入——DAC-3D 色散共聚焦光学检测系统。
领域知识图谱 101 个知识点并与 ML 图谱跨课程连边（15 条），六关卡任务树 41 个任务 / 154 条映射；
知识点自动匹配（确定性检索 + 依赖边扩展，实测 Top-6 召回 64.3%）与教师审核队列；
适配器读真实 git 仓库产出五类信号。**核心代码改动 0 行**，做法见 `docs/project-onboarding.md`。

**Phase 4.2（已完成）：** 挂上真实培养方案。导入《人工智能（专升本）· 院长实验班人才培养方案》
（28 门课 / 77.5 学分）；把数学从"机器学习第 2 章"移交给高等数学B、线性代数A、概率论与数理统计A
三门真实课程，深度学习独立成课——**只改归属不改代码**，446 条事件与 335 条掌握态一条未动；
新增**悬空需求队列**（引用尚未建的知识点即登记成需求，按需求数排序回答"下一门课建什么"）
与**学分认定视图**（两道闸：图谱建设度 + 覆盖度，拿不准明确返回"判不了"）。
实测：DAC 一个项目触及 11/28 门课、拉取 158 个知识点，其中 97 个属项目专有、不折算学分。
做法见 `docs/project-onboarding.md`（教师指导手册）。

**待推进：** T1–T5 各测点的平行卷与迁移任务命题、项目产出五维量规的录入界面、
按需求队列补建 6 门被需要但尚未建图谱的课程、
候选映射的教师实审（采纳率是判断这套匹配是否可用的唯一诚实指标，目前尚无判决），
以及 `docs/decisions.md` 里参数的教师拍板。
