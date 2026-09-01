"""权限码与默认角色矩阵。

权限码是稳定契约：路由用 `perm="quiz.review"`，矩阵可调，码名尽量不改。
"""
from __future__ import annotations

# 权限码 → 说明
PERMISSIONS: dict[str, str] = {
    # 诊断与画像
    "diagnosis.read": "阅读班级/个体诊断与画像",
    "diagnosis.write": "触发诊断相关写操作（报告打开日志等）",
    # 学生学习数据
    "student.read_self": "学生阅读本人数据",
    "student.read_class": "教师阅读本班学生数据",
    "student.write_self": "学生写入本人学习事件/作答",
    # 题库与练习
    "quiz.draft": "模型出题草案",
    "quiz.review": "审核题库草案",
    "quiz.grade": "人工判分（练习侧 pending）",
    "quiz.bank": "查看题库",
    # 知识点映射
    "kpmatch.propose": "跑自动匹配提出候选",
    "kpmatch.decide": "采纳/否决候选映射",
    # 代码审查与错误模式
    "review.decide": "采纳/否决审查发现",
    "error.verify": "确认错误模式",
    # 考试
    "exam.publish": "发布考试 / 签发准考证",
    "exam.monitor": "监考与排名导出",
    "exam.score": "考试人工判分",
    # 课件
    "courseware.write": "生成/管理大纲课件",
    # 运维
    "admin.ops": "运维只读（用量、健康以外的敏感运维）",
    "admin.seed": "种子与账号管理",
    # 流程
    "workflow.read": "查看统一待审队列",
    "workflow.act": "认领/裁决流程项",
}

# 角色 → 权限集合。admin 拥有全部。
ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    "admin": frozenset(PERMISSIONS.keys()),
    "teacher": frozenset({
        "diagnosis.read", "diagnosis.write",
        "student.read_class", "student.write_self",
        "quiz.draft", "quiz.review", "quiz.grade", "quiz.bank",
        "kpmatch.propose", "kpmatch.decide",
        "review.decide", "error.verify",
        "exam.publish", "exam.monitor", "exam.score",
        "courseware.write",
        "workflow.read", "workflow.act",
    }),
    "ta": frozenset({
        "diagnosis.read",
        "student.read_class",
        "quiz.grade", "quiz.bank",
        "exam.monitor", "exam.score",
        "workflow.read", "workflow.act",
        "error.verify",
    }),
    "student": frozenset({
        "student.read_self", "student.write_self",
    }),
}

ROLE_META: dict[str, tuple[str, str]] = {
    "admin": ("教务/管理员", "可见全部班级；可做种子与运维"),
    "teacher": ("导师/教师", "本班诊断、审核队列、考试与课件"),
    "ta": ("助教", "本班判分与监考子集，无发布考/采纳映射权"),
    "student": ("学生", "仅本人学习数据"),
}

# 路由 role= 元数据与 RBAC 角色的兼容：标了 role="teacher" 的接口，
# admin 与 teacher 都可进；ta 仅当另标了 perm 且矩阵允许时放行。
ROLE_ALIASES: dict[str, frozenset[str]] = {
    "teacher": frozenset({"teacher", "admin"}),
    "admin": frozenset({"admin"}),
    "student": frozenset({"student"}),
    "ta": frozenset({"ta", "teacher", "admin"}),
    "examinee": frozenset({"examinee"}),
}
