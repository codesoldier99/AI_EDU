"""教学技能包：让教师用一个 Markdown 文件扩展系统，而不必改代码。

格式沿用 DeepTutor 的开放 Agent-Skills 约定（YAML frontmatter + Markdown 正文），
但用途被收窄了一格：**技能包只能影响"怎么问、怎么出题"，不能影响"算不算掌握"**。
掌握度永远由 BKT 依据事件产生（铁律 1 与禁止事项第一条），
任何人往 skills/ 里丢一个文件都改变不了这一点。

与 DeepTutor 的另一处不同：不做远程安装。演示环境常常没有外网，
且"从 hub 拉一个 playbook 进来直接生效"对教学系统是一条不该开的口子——
本地目录、教师可读、进 git，就是全部的分发机制。
"""
from .loader import (  # noqa: F401
    KINDS,
    SKILLS_DIR,
    SkillPack,
    for_kind,
    load_all,
    pick_asking_style,
    quiz_guidance,
    stats,
)
