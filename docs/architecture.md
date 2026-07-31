# 架构说明：为什么是这样

本文解释代码里那些"看起来可以更简单"的地方为什么不能更简单。
配套阅读：`../CLAUDE.md`（铁律）、`../DEVELOPMENT_PLAN.md`（路线）。

---

## 1. 一条分界线

系统的所有部件必须清晰地落在两侧：

| 部件 | 归属 | 设计要求 | 代码位置 |
|---|---|---|---|
| 大模型 | 可替换 | 接口标准化，随时可整体更换 | `packages/llm` |
| 检索与生成策略 | 可替换 | 允许迭代重写 | `packages/rag`、`packages/agents/*.express` |
| 知识图谱 | 可积累 | 格式长期稳定，只增不毁 | `packages/graph` |
| 错误模式库 | 可积累 | 持续沉淀，独立于模型版本 | `packages/errors` |
| 学生状态历史 | 可积累 | 结构化留存，支持长周期分析 | `packages/state` |

**铁律：任何可积累的东西，都不得依赖任何可替换的东西的内部实现。**

具体到代码，这条铁律长成三个可执行的检查：

1. `packages/graph`、`packages/state` 不得 import `packages.llm`（`tests/test_layering.py`）。
2. 向量落库必须记录 `embed_model` / `embed_version`，换模型时 `store.stale_embeddings()`
   能算出待重算的块数——embedding 是唯一允许存在于可积累层的模型产物，代价是必须可重算。
3. `error_pattern` 的 `signature` 是纯文本指纹而非 embedding 聚类结果。
   聚类精度可以后补，但**换模型不能让三届学生的错误样本作废**。

---

## 2. 每个智能体都被劈成两半

```python
class Agent:
    def plan(...)     # 确定性计算：可单测、可复现、可被教师质疑
    def express(...)  # 交给大模型说人话：可替换、可降级、不改变结论
```

`express()` 的输入格式被固定为 `- 键：值`，一个字段一行。
这不是为了好看，而是为了让离线表达器和真实大模型看到**同一份结构化输入**——
于是"关掉大模型，系统的判断是否改变"变成一个可以当场演示的问题。答案是不变。

`packages/llm/offline.py` 存在的真正意义在这里：它是这条架构主张的反证工具。

---

## 3. 事件流的规范顺序

状态是事件流按**记录顺序（自增 id）**折叠出来的结果，不是按 `occurred_at`。

理由：`occurred_at` 是业务时间元数据，可能补录、可能乱序；用它折叠会得到与线上不同的值，
重算就不再可判定。把"记录顺序"钉死为唯一规范顺序之后，
`make replay` 才能成为一个有意义的验收标准（`packages/state/replay.py`）。

代价：教师补录历史成绩不会"追溯改写"当时的状态曲线。这是刻意的取舍——
可审计性优先于叙事上的整洁。

数据库层面的三道锁：

```sql
-- 事件流只追加
CREATE TRIGGER learning_event_no_update ... RAISE(ABORT, ...)
CREATE TRIGGER learning_event_no_delete ... RAISE(ABORT, ...)
-- 掌握度必须挂事件
CREATE TRIGGER mastery_requires_event_ins WHEN NEW.last_event_id IS NULL ... RAISE(ABORT, ...)
```

PostgreSQL 上对应写法是 `REVOKE UPDATE, DELETE ON learning_event FROM app_user`。

---

## 4. 证据权重：为什么不是所有事件一视同仁

`packages/state/tracker.SOURCE_WEIGHT`：

```
exam/quiz 1.0 → homework/lab 0.9 → practice 0.7 → ask 0.5 → review 0.5 → project 0.4
```

越间接的证据权重越低。代码审查发现一处数据泄漏，只能算"半条"证据——
它指向的是这个学生在这一次提交里的表现，而不是一次独立命题的判定。

实现上不是"打折的 BKT"，而是在原值与完整更新值之间线性插值，
保证单调性与 [0,1] 闭合（`bkt.update_with_weight`）。

---

## 5. 置信度是一个公式，不是一个感觉

```
confidence = (1 - e^(-n/k)) × (0.4 + 0.6 × 0.5^(Δt/半衰期))
```

n 是证据条数，Δt 是距最近一次证据的天数。它显式、可解释、可被教师质疑。
所有对外 API 都返回 `confidence` 与 `evidence_count`；证据不足时前端必须显示
"数据不足，仅供参考"（`Evidenced.caveat`，前端 `caveatBox()`）。

开学前几周判断必然不准。与其猜一个数字，不如坦白——
教师对系统的信任一旦失去，很难重建。

---

## 6. 根因回溯：纯大模型做不可靠的那一步

```
对每个掌握度 < 阈值的知识点 K：
  沿 prerequisite 反向 BFS
  只沿"同样未掌握"的链条继续（已掌握的祖先说明链条到此为止）
  返回最深的低掌握度节点作为根因
```

例：反向传播出错 → 回溯到链式法则。

这一步依赖 L1 的**显式依赖结构**。大模型可以说出"可能是链式法则没学好"，
但它说不出"这个班 32 个人里有 19 个的反向传播错误可以归到同一个前置点上"，
更说不出这个结论的依据是什么。

`blocking_severity = 2 × 待办任务依赖数 + 0.5 × 下游知识点数`——
待攻克点的排序用的是"低掌握 × 高挡路度"，不是简单的错误率排序。
一个所有人都错、但不挡任何路的边缘知识点，不该占用下节课的时间。

---

## 7. 拉取式带来的两处连带改动

范式从"推送"改为"拉取"，不只是换一个调度函数：

1. **新增了一张表** `task_kp_link`——没有它拉取式无从谈起。
   而且它必须由**导师标注**：这是学分认定与能力追溯的依据，
   大模型可以给候选，教师确认。`annotated_by` 字段留了痕。
2. **删掉了一个功能**：课程进度百分比。
   过程完全个体化之后，"全班进度"这个概念本身就不成立了。
   `TaskAgent.project_progress` 只返回任务进度，并在返回值里写明这一点，
   `tests/test_pull.py` 会检查它不含 `course_progress`。

排序规则是 `(缺口内前置卡点数, -挡路度, 拓扑序)`：
**能立刻学的优先**——推一个学生此刻还学不动的知识点，等于什么也没推。

---

## 8. 只奖励坚持

`packages/engagement` 的白名单：

```python
ALLOWED_KINDS = {"breakthrough", "milestone", "comeback", "first_run", "streak"}
```

传入白名单之外的类型直接 `ValueError`。禁止的激励对象是正确率、做题数、停留时长、排名。

其中 `comeback` 权重最高——流失往往发生在中断后的第一天。
`breakthrough` 的文案必须与根因绑定："你攻克了链式法则，这是之前 3 个卡点的共同原因"，
否则它只是又一个无意义的徽章。

engagement 是**派生态**：只读事件流，从不回写 state。
清掉 `streak_state` 表后重算，结果必须一致（`tests/test_replay.py`）。

---

## 9. 适配器：接第 5 个和第 10 个项目的成本相同

异构项目（机器人、视觉、大模型应用、数据平台、边缘）的原始数据形态差异极大，
但都必须归一化为五类标准信号：

```
code_commit / build_test / runtime / doc_delivery / collaboration
```

`tests/test_adapters.py` 用一个很直接的办法检验这条设计：
在测试里新增一个适配器、跑通信号入库，然后**对核心文件做 md5 比对**——
一个字节都不能变。

五类信号到 M1–M8 的权重矩阵写在配置里（`config.teaching.signal_weights`），
由教师团队标定，不硬编码。

---

## 10. 审查回写：宁缺毋滥

`ReviewFinding.related_kp_ids` 是把工程问题翻回知识判断的**唯一通道**。
但绝大多数代码缺陷映射不到具体学科知识点——裸 except、函数过长、圈复杂度高，
这些说明工程素养，不说明"某个知识点没掌握"。

因此 `data/seed/rule_kp_map.yaml` 里大部分规则是 `reliable: false`，只作建议展示。
真正允许回写的是 D 系列（数据科学专用规则）：

| 规则 | 命中含义 | 映射知识点 |
|---|---|---|
| D001 | 划分数据集之前 fit 了预处理器 | 数据泄漏的识别与防范 |
| D002 | 在测试集上调阈值或选模型 | 验证集调参导致的乐观偏差 |
| D003 | 不平衡数据只报准确率 | 准确率的局限 |
| D004 | 未固定随机种子 | 复现实验与随机性控制 |
| E008 | 硬编码密钥 | 数据合规与隐私保护 |

且必须**教师采纳之后**才回写，否决记录用于优化规则
（`ReviewAgent.rejection_stats()` 会告诉你哪条规则最常被否决——那就是该改或该删的）。

---

## 11. 不做什么

- 不替代教师的判断：一切输出都是建议，可采纳、可修改、可否决。
- 不追求全自动：先做诊断，后做批改。教师批完一百份作业，真正有用的信息
  往往只有一句话——某道题半数人做错，原因是混淆了两个概念。
- 不掩饰不确定：数据不足时明说数据不足。
- 不生成针对教师的评价性数据：`tests/test_layering.py` 检查 schema 里
  不存在 `teacher_score` / `teacher_rating` 之类的字段。
  这一条若失守，教师会本能地抵制，再好的系统也推不动。

`report_open_log` 记录教师主动打开诊断报告的次数——那是对**系统**是否被真正使用的观测
（Phase 1 验收指标：教师周打开 ≥ 3 次），接口返回值里也写明了它不进入任何教师考核。
