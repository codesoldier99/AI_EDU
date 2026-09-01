"""流程种类、状态与合法迁移。"""
from __future__ import annotations

# 流程项状态
STATES = frozenset({
    "draft", "pending_review", "claimed", "approved", "rejected", "retired",
})

# 终态
TERMINAL = frozenset({"approved", "rejected", "retired"})

# action → 目标状态
ACTIONS = {
    "claim": "claimed",
    "unclaim": "pending_review",
    "approve": "approved",
    "reject": "rejected",
    "retire": "retired",
}

# from_state → 允许的 action
TRANSITIONS: dict[str, frozenset[str]] = {
    "draft": frozenset({"retire"}),
    "pending_review": frozenset({"claim", "approve", "reject", "retire"}),
    "claimed": frozenset({"approve", "reject", "unclaim", "retire"}),
    "approved": frozenset(),
    "rejected": frozenset(),
    "retired": frozenset(),
}

# kind → (ref_table, 默认标题模板用的中文名, 裁决所需权限)
KINDS: dict[str, dict] = {
    "quiz_draft": {
        "ref_table": "question",
        "label": "题库草案审核",
        "perm": "quiz.review",
    },
    "kp_mapping": {
        "ref_table": "task_kp_candidate",
        "label": "知识点映射候选",
        "perm": "kpmatch.decide",
    },
    "review_finding": {
        "ref_table": "review_finding",
        "label": "代码/文档审查发现",
        "perm": "review.decide",
    },
    "error_pattern": {
        "ref_table": "error_pattern",
        "label": "错误模式确认",
        "perm": "error.verify",
    },
    "exam_grade": {
        "ref_table": "exam_answer",
        "label": "考试人工判分",
        "perm": "exam.score",
    },
    "quiz_grade": {
        "ref_table": "learning_event",
        "label": "练习人工判分",
        "perm": "quiz.grade",
    },
}
