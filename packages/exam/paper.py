"""试卷定义、入库与冻结。

一场考试发布之后，试卷快照（题目 id、分区、分值、顺序）就**冻结**在 exam.paper 里，
不再随题库变化。理由很直接：题库是活的，会被修订、被退役；而"这 128 个人当时做的
是哪张卷子"是一个历史事实，两年后还要拿出来对账。

分区的含义（docs/measurement-plan.md §4）：
    A  概念锚题——两年内反复重测，**考后不讲评、不返还**，origin='anchor'
    B  选拔专用——一次性使用，考后可以公开讲评，origin='teacher'
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from packages.core.db import dumps, get_db, loads
from packages.core.models import Message
from packages.core.timeutil import now_str
from packages.graph import repo as graph_repo
from packages.quiz import bank

PARTS = ("A", "B")
# 五种题型 -> 题库的 qtype / 判分器。程序题一律人工判分：
# 5 分的主观题让规则去猜，判分噪声会把分数线附近的人洗牌（measurement-plan §4.1）。
TYPE_SPEC = {
    "single":   {"qtype": "choice",  "grader": "choice",  "label": "单选题"},
    "judge":    {"qtype": "choice",  "grader": "choice",  "label": "判断题"},
    "fill":     {"qtype": "short",   "grader": "keyword", "label": "填空题"},
    "reading":  {"qtype": "short",   "grader": "keyword", "label": "程序阅读题"},
    "program":  {"qtype": "code",    "grader": "manual",  "label": "程序题"},
}


@dataclass
class PaperItem(Message):
    question_id: int = 0
    part: str = "B"
    itype: str = "single"
    points: float = 1.0


@dataclass
class PaperSpec(Message):
    exam_id: int = 0
    code: str = ""
    title: str = ""
    duration_min: int = 60
    items: list = field(default_factory=list)
    total_score: float = 0.0
    by_type: dict = field(default_factory=dict)
    by_part: dict = field(default_factory=dict)


def _yaml(path: Path) -> dict:
    import yaml

    return yaml.safe_load(path.read_text(encoding="utf-8"))


def import_paper(path: str | Path) -> PaperSpec:
    """从 YAML 导入一张试卷：题目入题库 + 建 exam + 冻结快照。

    幂等：题面指纹去重，重复导入不会产生重复题；已存在的 exam 按 code 覆盖草稿。
    已发布（status='published'）的考试拒绝覆盖——发布即冻结。
    """
    data = _yaml(Path(path))
    meta = data["exam"]
    db = get_db()

    exist = db.query_one("SELECT * FROM exam WHERE code=?", (meta["code"],))
    if exist and exist["status"] != "draft":
        raise ValueError(f"考试 {meta['code']} 已发布，试卷已冻结，不能再导入")

    items: list[PaperItem] = []
    unknown_kp: list[str] = []
    for raw in data.get("items", []):
        spec = TYPE_SPEC.get(raw["type"])
        if not spec:
            raise ValueError(f"未知题型 {raw['type']}；可用 {'/'.join(TYPE_SPEC)}")
        kp = graph_repo.get_kp_by_code(raw["kp"])
        if not kp:
            unknown_kp.append(raw["kp"])
            continue
        part = raw.get("part", "B")
        if part not in PARTS:
            raise ValueError(f"未知分区 {part}")
        # 允许逐题覆盖判分器：同是填空题，"结果形状是多少"该用数值求值器判，
        # "这一步做的是什么"该用关键词判。用错判分器就会把对的判成错的。
        g = raw.get("grader") or spec["grader"]
        qid = bank.add(
            kp_id=kp.id,
            stem=raw["stem"].strip(),
            answer=str(raw.get("answer", "")),
            qtype="numeric" if g == "numeric" else spec["qtype"],
            grader=g,
            tolerance=float(raw.get("tolerance", 0.0)),
            options=raw.get("options") or [],
            keywords=raw.get("keywords") or [],
            rationale=(raw.get("rationale") or "").strip(),
            difficulty=float(raw.get("difficulty", 0.5)),
            # 分区决定 origin：A 区是锚题，自动被日常组卷池排除
            origin="anchor" if part == "A" else "teacher",
        )
        items.append(PaperItem(question_id=qid, part=part, itype=raw["type"],
                               points=float(raw.get("points", 1))))

    if unknown_kp:
        raise ValueError(f"试卷引用了未知知识点代码：{unknown_kp[:5]}")
    if not items:
        raise ValueError("试卷没有任何题目")

    total = round(sum(i.points for i in items), 2)
    snapshot = dumps({"items": [i.to_dict() for i in items], "total": total})
    now = now_str()
    if exist:
        db.execute(
            "UPDATE exam SET title=?, duration_min=?, total_score=?, paper=?, shuffle=?,"
            " note=? WHERE id=?",
            (meta["title"], int(meta.get("duration_min", 60)), total, snapshot,
             int(meta.get("shuffle", 1)), meta.get("note", ""), exist["id"]),
        )
        exam_id = exist["id"]
    else:
        exam_id = db.execute(
            "INSERT INTO exam(code, title, duration_min, status, total_score, paper,"
            " shuffle, note, created_at) VALUES(?,?,?, 'draft', ?,?,?,?,?)",
            (meta["code"], meta["title"], int(meta.get("duration_min", 60)), total,
             snapshot, int(meta.get("shuffle", 1)), meta.get("note", ""), now),
        )
    return spec_of(exam_id)


def spec_of(exam_id: int) -> PaperSpec:
    row = get_db().query_one("SELECT * FROM exam WHERE id=?", (exam_id,))
    if not row:
        raise ValueError("考试不存在")
    snap = loads(row["paper"], {})
    items = [PaperItem.from_dict(i) for i in snap.get("items", [])]
    by_type: dict = {}
    by_part: dict = {}
    for i in items:
        t = by_type.setdefault(i.itype, {"n": 0, "points": 0.0,
                                         "label": TYPE_SPEC[i.itype]["label"]})
        t["n"] += 1
        t["points"] = round(t["points"] + i.points, 2)
        p = by_part.setdefault(i.part, {"n": 0, "points": 0.0})
        p["n"] += 1
        p["points"] = round(p["points"] + i.points, 2)
    return PaperSpec(
        exam_id=exam_id, code=row["code"], title=row["title"],
        duration_min=row["duration_min"], items=[i.to_dict() for i in items],
        total_score=row["total_score"], by_type=by_type, by_part=by_part,
    )


def get_exam(code_or_id) -> dict | None:
    db = get_db()
    if isinstance(code_or_id, int) or str(code_or_id).isdigit():
        return db.query_one("SELECT * FROM exam WHERE id=?", (int(code_or_id),))
    return db.query_one("SELECT * FROM exam WHERE code=?", (str(code_or_id),))


def publish(code_or_id, opens_at: str = "", closes_at: str = "") -> dict:
    """发布 = 冻结。发布之后试卷不能再改，只能整场作废重开。"""
    e = get_exam(code_or_id)
    if not e:
        raise ValueError("考试不存在")
    spec = spec_of(e["id"])
    if not spec.items:
        raise ValueError("空白试卷不能发布")
    get_db().execute(
        "UPDATE exam SET status='published', published_at=?, opens_at=?, closes_at=?"
        " WHERE id=?",
        (now_str(), opens_at or None, closes_at or None, e["id"]),
    )
    return {"exam_id": e["id"], "code": e["code"], "status": "published",
            "total_score": spec.total_score, "n_items": len(spec.items),
            "note": "试卷已冻结；如需修改必须整场作废重开，不能就地改题"}
