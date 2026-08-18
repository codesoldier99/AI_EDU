"""在线考试：入学选拔，同时是两年纵向测评的基线。

设计见 docs/measurement-plan.md。与 packages/quiz 的分工：

    packages/quiz   日常教学的题库与判分（自适应组卷、判不了转人工）
    packages/exam   一场固定的、限时的、一次性的考试（服务端计时、冻结卷面）

复用的是 quiz.grader——**判分规则必须与平时完全一致**，否则考试分数和平时
掌握度不在同一把尺子上，两年后的纵向比较无从谈起。

不复用的是 quiz.selector——考试不是自适应组卷，是所有人同一张卷子。
"""
from . import paper, scoring, session  # noqa: F401
