"""题库与确定性判分。

位置：介于 agents 与 state 之间（agents → quiz → state → graph）。
本包**不依赖大模型**——出题的"选哪个知识点、发哪道题、算不算对"全部是确定性的，
模型只在 packages/agents/quiz.py 里负责把题面写成人话，且产出一律进待审队列。

吸收自 DeepTutor 的 Quiz / Mastery Path，取舍见 docs/deeptutor.md。
"""
from . import bank, grader, selector  # noqa: F401
