"""教师工作台仓储层。本文件（与本包内不含 subprocess 的其余文件）是
packages/courseware 里唯一允许出现 SQL 的地方——教学大纲/授课计划/课件产物

三张表都是"教师可修订的教学设计文档"，不是学生状态，因此不走 002 的
append-only 触发器；但本文件绝不改写学生掌握度表或知识图谱表——
那两类写操作分别只能经 packages.state.tracker 和 packages.graph.repo，
见 tests/test_layering.py。
"""
from __future__ import annotations

from packages.core.db import dumps, get_db, loads
from packages.core.timeutil import now_str


# ---------------- 教学大纲 ----------------
def save_syllabus(course_id: int, content: dict, generator_version: str = "",
                   llm_model_version: str = "", created_by: str = "") -> int:
    """新增一版大纲。旧版本不动，只在这里追加——修订历史天然保留。"""
    return get_db().execute(
        "INSERT INTO syllabus(course_id, version, status, content_json, generator_version,"
        " llm_model_version, created_by, created_at) VALUES(?,"
        " COALESCE((SELECT MAX(version)+1 FROM syllabus WHERE course_id=?), 1),"
        " 'draft', ?, ?, ?, ?, ?)",
        (course_id, course_id, dumps(content), generator_version, llm_model_version,
         created_by, now_str()),
    )


def get_syllabus(syllabus_id: int) -> dict | None:
    r = get_db().query_one("SELECT * FROM syllabus WHERE id=?", (syllabus_id,))
    if r:
        r["content"] = loads(r["content_json"], {})
    return r


def latest_syllabus(course_id: int) -> dict | None:
    r = get_db().query_one(
        "SELECT * FROM syllabus WHERE course_id=? ORDER BY version DESC LIMIT 1", (course_id,)
    )
    if r:
        r["content"] = loads(r["content_json"], {})
    return r


def confirm_syllabus(syllabus_id: int) -> None:
    get_db().execute(
        "UPDATE syllabus SET status='teacher_confirmed', confirmed_at=? WHERE id=?",
        (now_str(), syllabus_id),
    )


def update_syllabus_chapter_narrative(syllabus_id: int, seq: int, narrative: str) -> dict:
    """对话式修订落地口：只改某一章的说明文字，章节结构（unit/kp_codes/kp_names）
    不受聊天指令影响——结构事实永远只能由 SyllabusAgent.plan() 重新生成。"""
    syl = get_syllabus(syllabus_id)
    if not syl:
        raise ValueError(f"教学大纲不存在：syllabus_id={syllabus_id}")
    content = syl["content"]
    chapter = next((c for c in content.get("chapters", []) if c["seq"] == seq), None)
    if chapter is None:
        raise ValueError(f"章节不存在：syllabus_id={syllabus_id} seq={seq}")
    chapter["narrative"] = narrative
    get_db().execute("UPDATE syllabus SET content_json=? WHERE id=?",
                     (dumps(content), syllabus_id))
    return chapter


def list_syllabus(course_id: int) -> list[dict]:
    return get_db().query(
        "SELECT id, course_id, version, status, created_by, created_at, confirmed_at"
        " FROM syllabus WHERE course_id=? ORDER BY version DESC", (course_id,)
    )


# ---------------- 授课计划 ----------------
def save_teaching_plan(syllabus_id: int, items: list[dict]) -> list[int]:
    """整体替换某版大纲下的授课计划（重新生成即覆盖，不追加历史——
    这是"计划"不是"证据流"，教师改主意时不需要保留旧的每周安排）。"""
    db = get_db()
    db.execute("DELETE FROM teaching_plan WHERE syllabus_id=?", (syllabus_id,))
    ids = []
    for it in items:
        ids.append(db.execute(
            "INSERT INTO teaching_plan(syllabus_id, seq, title, kp_codes_json, duration_min,"
            " narrative, created_at) VALUES(?,?,?,?,?,?,?)",
            (syllabus_id, it["seq"], it["title"], dumps(it.get("kp_codes", [])),
             it.get("duration_min", 90), it.get("narrative", ""), now_str()),
        ))
    return ids


def list_teaching_plan(syllabus_id: int) -> list[dict]:
    rows = get_db().query(
        "SELECT * FROM teaching_plan WHERE syllabus_id=? ORDER BY seq", (syllabus_id,)
    )
    for r in rows:
        r["kp_codes"] = loads(r["kp_codes_json"], [])
    return rows


def get_teaching_plan_item(item_id: int) -> dict | None:
    r = get_db().query_one("SELECT * FROM teaching_plan WHERE id=?", (item_id,))
    if r:
        r["kp_codes"] = loads(r["kp_codes_json"], [])
    return r


def update_session_narrative(item_id: int, narrative: str) -> None:
    get_db().execute("UPDATE teaching_plan SET narrative=? WHERE id=?", (narrative, item_id))


# ---------------- 课件产物 ----------------
def save_deck(teaching_plan_id: int, deck_plan: dict, render_result: dict) -> int:
    return get_db().execute(
        "INSERT INTO courseware_deck(teaching_plan_id, deck_plan_json, artifact_type,"
        " render_tool, render_tool_version, file_path, degraded, kp_coverage_json, created_at)"
        " VALUES(?,?,?,?,?,?,?,?,?)",
        (
            teaching_plan_id, dumps(deck_plan), render_result.get("artifact_type", "pptx"),
            render_result.get("render_tool", ""), render_result.get("render_tool_version", ""),
            render_result.get("file_path", ""), int(render_result.get("degraded", False)),
            dumps(deck_plan.get("slides") and _flatten_coverage(deck_plan) or []),
            now_str(),
        ),
    )


def _flatten_coverage(deck_plan: dict) -> list[str]:
    seen: set[str] = set()
    for s in deck_plan.get("slides", []):
        seen |= set(s.get("kp_codes", []))
    return sorted(seen)


def get_deck(deck_id: int) -> dict | None:
    r = get_db().query_one("SELECT * FROM courseware_deck WHERE id=?", (deck_id,))
    if r:
        r["deck_plan"] = loads(r["deck_plan_json"], {})
        r["kp_coverage"] = loads(r["kp_coverage_json"], [])
    return r


def update_deck_slide_bullets(deck_id: int, slide_index: int, bullets: list[str]) -> dict:
    """对话式修订落地口：只改某一页的要点文字。哪些知识点、有几页，
    结构事实同样不受聊天指令影响——只能由 DeckAgent.plan_deck() 重新生成。"""
    deck = get_deck(deck_id)
    if not deck:
        raise ValueError(f"课件不存在：deck_id={deck_id}")
    plan = deck["deck_plan"]
    slides = plan.get("slides", [])
    if not (0 <= slide_index < len(slides)):
        raise ValueError(f"页码越界：deck_id={deck_id} slide_index={slide_index}")
    slides[slide_index]["bullets"] = bullets
    get_db().execute("UPDATE courseware_deck SET deck_plan_json=? WHERE id=?",
                     (dumps(plan), deck_id))
    return slides[slide_index]


def update_deck_render(deck_id: int, render_result: dict) -> None:
    """重新渲染后回写文件路径/渲染工具信息，deck_plan_json 内容不变（那是"重新渲染"，
    不是"重新生成"——知识点覆盖范围不应该因为换了一次渲染而改变）。"""
    get_db().execute(
        "UPDATE courseware_deck SET artifact_type=?, render_tool=?, render_tool_version=?,"
        " file_path=?, degraded=? WHERE id=?",
        (render_result.get("artifact_type", "pptx"), render_result.get("render_tool", ""),
         render_result.get("render_tool_version", ""), render_result.get("file_path", ""),
         int(render_result.get("degraded", False)), deck_id),
    )


def list_decks(teaching_plan_id: int) -> list[dict]:
    rows = get_db().query(
        "SELECT id, teaching_plan_id, artifact_type, render_tool, render_tool_version,"
        " file_path, degraded, created_at FROM courseware_deck WHERE teaching_plan_id=?"
        " ORDER BY id DESC", (teaching_plan_id,),
    )
    return rows
