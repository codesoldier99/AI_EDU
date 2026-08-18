# DeepTutor 研判：抄什么，不抄什么，为什么

> 对象：[HKUDS/DeepTutor](https://github.com/HKUDS/DeepTutor)（香港大学数据智能实验室，
> 2025 年 12 月开源，Apache-2.0，约 3.6 万星）。
> 结论：**能力面全抄，执行方式全改。**
> 落地见 `packages/{quiz,tools,skills}`、`packages/agents/{quiz,solve,research,visualize}.py`、
> `migrations/003_study.sql`、`apps/web/study.js`。

---

## 一、它到底是什么

DeepTutor 的自我描述是 "agent-native personalized learning assistant"。
拆开看，它押了三个注：

1. **一个 agent loop 跑所有能力。** Chat、Quiz、Deep Research、Visualize、Deep Solve、
   Mastery Path 共用同一个工具调用循环，"switch the objective, not the engine"。
   学生换目标时上下文跟着走，而不是换一个入口重新开始。
2. **记忆是文件，且可追溯。** 三层：L1 是每个界面的原始事件日志（`trace/<surface>/<date>.jsonl`，
   只追加），L2 是每个界面的摘要（带指回 L1 的引用），L3 是跨界面综合
   （`profile.md` / `recent.md` / `scope.md` / `preferences.md`）。
   它有一个 Memory Graph，能把 L3 里的任何一条结论回溯到 L1 的原始事件——
   官方说法是 "nothing in your profile is unaccountable"。
3. **一切可插拔。** 五种 RAG 引擎（LlamaIndex / PageIndex / GraphRAG / LightRAG /
   LightRAG Server）、六种文档解析器、开放的 Agent-Skills 格式（`SKILL.md` =
   YAML frontmatter + Markdown playbook）、101+ MCP 服务、CLI Apps、
   十余种 IM 渠道的 Partners。

第 2 条值得单独说一句：**它和我们的事件流是同一个想法**。
我们的 `learning_event` 是 L1，`mastery_state` 与 `verification.build()` 是 L2/L3，
`make replay` 就是它的 Memory Graph。两套系统在完全不同的语境下独立收敛到同一条原则——
"派生结论必须能回溯到原始事件"——这件事本身就说明这条原则大概率是对的。

## 二、根本分歧只有一条

| | DeepTutor | 本系统 |
|---|---|---|
| 谁做判断 | 模型（在 agent loop 里想） | 确定性算法（BKT / 图算法 / 求值器） |
| 模型的角色 | 大脑 | 嘴 |
| 好处 | 灵活，什么都能做 | 每一条结论可被教师质疑、被系统复算 |
| 代价 | 没有一处能复算 | 每个能力都要先想清楚"什么是可判定的" |

这不是谁对谁错的问题，是**面向的场景不同**：DeepTutor 服务的是自学者，
错了自己承担；我们服务的是一个学院的实验班，错了要写进学生的能力档案、
要给教师看、要经得起第三方核查（这正是 Alpha School 被击穿的地方）。

所以取舍的口径只有一句：**能力面全抄，判定权不让。**

## 三、逐条取舍

| DeepTutor 的能力 | 判定 | 我们怎么做 | 落地 |
|---|---|---|---|
| **Quiz** 出题 + 批改 + 题库 | ✅ 吸收（重构） | 出题选点走确定性；LLM 只写题面且必须挂教材依据；模型出的题一律进待审队列；判分先规则后人工，**判不了就不判** | `packages/quiz/`、`packages/agents/quiz.py` |
| **Mastery Path** 分级练习 | ✅ 吸收（我们已有更严的） | 它的"掌握门槛"是当堂达标；我们的 `verification.py` 要求跨时间再做对才算已验证。练习目标直接接到复检队列与待验证队列上 | `packages/quiz/selector.py` |
| **Deep Solve** 分步推理 | ⚠️ 吸收但反过来做 | 它把完整推理讲清楚；我们**一次只交出一步**，学生先答，判定走确定性工具，答不上来才逐级降级，降到 L3 才给该步结论并标记教师介入 | `packages/agents/solve.py` |
| **Deep Research** 调研报告 | ⚠️ 吸收但限域 | 不接外网六家搜索，只查院内三库；生成后再量一次落地性（n-gram 重合率），低支撑段落标出来不删；**产出计入交付物，不计入掌握度** | `packages/agents/research.py`、`packages/tools/ground.py` |
| **Visualize** 图表动画 | ⚠️ 吸收但反过来做 | 它让模型写 Chart.js/Mermaid 代码；我们由确定性纯函数生成 SVG，模型只写图下面那句解读。零图表库、零 CDN | `packages/agents/visualize.py` |
| **Memory L1/L2/L3 可追溯** | ✅ 已有，理念一致 | 事件流即 L1，掌握度与验证视图即 L2/L3，`make replay` 即 Memory Graph | 既有 |
| **Skills**（`SKILL.md` playbook） | ✅ 吸收（收窄） | 教师写一个 Markdown 就能改"怎么问、怎么出题"，不改代码、不重启。但技能包**够不到"算不算掌握"**，且不做远程安装 | `packages/skills/`、`skills/` |
| **多引擎 RAG** | ◐ 部分吸收 | 我们的 retriever 本就是"向量 + 图谱结构化加权"的 GraphRAG 变体（沿前置边扩展检索范围）。接口已隔离，将来换引擎不动业务代码 | 既有 `packages/rag/` |
| **一个 loop 跑所有能力** | ◐ 抄形不抄神 | 前端做成一个「学习工作台」，四个能力共用学生上下文；但每个能力背后仍是 `plan()` / `express()` 两段 | `apps/web/study.js` |
| **Math Animator（Manim）** | ❌ 拒绝 | 需要 LaTeX + ffmpeg + 一堆系统库。演示环境装不动，违反"无外网必须完整可用" | — |
| **Partners / IM 渠道 / 多用户工作区** | ❌ 拒绝 | 我们已有身份与班级权限模型；再引入一套"合成用户 + 十几个 IM 渠道"只增加攻击面，不解决任何教学问题 | — |
| **101+ MCP / CLI Apps / 沙箱执行代码** | ❌ 现阶段拒绝 | 沙箱里跑模型生成的代码是独立的一条工程线，且演示环境无外网。真要跑学生代码，走已有的两级审查流水线 | — |
| **技能包远程 hub 安装** | ❌ 拒绝 | 从 hub 拉一个 playbook 进来直接生效，对教学系统是不该开的口子。技能包进 git，教师可读、可评审、可回滚 | — |

## 四、吸收过程中确立的三条新规矩

这三条不是从 DeepTutor 抄来的，是抄它的过程中被逼出来的，已写进 `CLAUDE.md`。

### 1. 判不了 ≠ 判错

`grader.GradeResult.is_correct` 允许为 `None`。关键词覆盖率落在 0.2–0.7 之间、
数值作答里出现多个数字、选择题作答解析不出选项——这些一律不判，写行为事件但不更新掌握度，
进教师人工队列。

理由很直接：BKT 的证据流是这套系统最贵的东西。往里面掺一条"其实没判出来但记成错了"的证据，
后面所有的诊断、根因回溯、复检排序都会跟着歪，而且**歪得看不出来**。

### 2. 证据权重随判定的确定性走

| 判定方式 | 记为 | 权重 |
|---|---|---|
| 教师人工判分 | `teacher` | 1.0 |
| 选择题 / 数值题规则判定 | `quiz` | 1.0 |
| 分步解题的数值校验 | `practice` | 0.7 |
| 关键词覆盖 / 追问文本规则 | `ask` | 0.5 |

同一个"做对了"，判得越准，写进 L2 的分量越重。这条在 `packages/agents/quiz.py`
的 `GRADER_SOURCE` 和 `solve.py` 的 `answer()` 里各实现了一处。

### 3. 生成之后还要再量一次落地性

RAG 只是**降低**了编造的概率，不能证明这一段没编。所以调研报告生成后，
用 n-gram 重合率再量一次（`packages/tools/ground.py`），低于阈值的段落
标成「低支撑」——不删掉，删掉就看不见问题了。

阈值 0.35 是个待拍板的参数，已进 `docs/decisions.md`。

## 五、没做的事（下一步候选）

- **Book / Co-Writer**（把多个来源编译成一本活的书、选区感知的 Markdown 起草）：
  对"技术报告与答辩材料"这一类交付物大概率有用，但要先想清楚它和
  `doc_delivery` 项目信号怎么衔接，否则又是一个只产出漂亮文档的功能。
- **代码沙箱**：真要让学生把代码丢进来跑，收益比出题大得多，但这是独立的一条工程线。
- **题库的 embedding 聚类去重**：现在按题面指纹去重，只能挡住字面重复。
  题量上千之后需要换成语义去重。

## 六、一句话

DeepTutor 证明了一件事：**一个学习助手该有的能力面，比"问答机器人"宽得多。**
我们照单全收了这个能力面，但把每一项里"谁说了算"的那部分抠出来，
换成了可复算、可质疑、可被教师推翻的确定性规则。

抄能力，不抄判断权。
