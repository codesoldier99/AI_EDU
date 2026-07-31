"""三个知识库的落库与索引。

kb ∈ {course, project, program}
  course  教材、讲义、课件、题库、标准答案与解析   -> 追问、试卷生成
  project 项目技术文档、代码库、历史方案、行业资料 -> 任务、审查、副驾驶
  program 培养方案、课程目标、毕业要求、大纲模板   -> 基础功能层、学分映射
"""
from __future__ import annotations

import re
from pathlib import Path

from packages.core.config import CONFIG
from packages.core.db import dumps, get_db, loads
from packages.core.timeutil import now_str
from packages.llm import gateway

KBS = ("course", "project", "program")


def chunk_text(text: str, size: int = 420, overlap: int = 60) -> list[str]:
    """按段落聚合切块，尽量不在句中切断。"""
    paras = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks, buf = [], ""
    for p in paras:
        if len(buf) + len(p) <= size:
            buf = f"{buf}\n{p}" if buf else p
        else:
            if buf:
                chunks.append(buf)
            if len(p) <= size:
                buf = p
            else:
                for i in range(0, len(p), size - overlap):
                    chunks.append(p[i: i + size])
                buf = ""
    if buf:
        chunks.append(buf)
    return chunks


def index_text(kb: str, ref: str, title: str, text: str,
               kp_codes: list[str] | None = None) -> int:
    """把一段文本切块、嵌入、入库。返回入库块数。"""
    if kb not in KBS:
        raise ValueError(f"未知知识库：{kb}")
    db = get_db()
    db.execute("DELETE FROM doc_chunk WHERE kb=? AND ref=?", (kb, ref))
    chunks = chunk_text(text)
    if not chunks:
        return 0
    vecs = gateway.embed(chunks)
    now = now_str()
    for ch, v in zip(chunks, vecs):
        db.execute(
            "INSERT INTO doc_chunk(kb, ref, title, text, kp_codes, embedding, embed_model,"
            " embed_version, created_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (kb, ref, title, ch, dumps(kp_codes or []), dumps(v.values),
             v.model or CONFIG.embed_model, v.version or CONFIG.embed_version, now),
        )
    return len(chunks)


def strip_meta(text: str) -> str:
    """剥离 <!-- kb: --> / <!-- kp: --> 这类元数据注释，避免它们进入检索文本。"""
    return re.sub(r"^\s*<!--.*?-->\s*$", "", text, flags=re.M).strip()


def index_file(kb: str, path: Path, kp_codes: list[str] | None = None) -> int:
    body = strip_meta(path.read_text(encoding="utf-8"))
    m = re.search(r"^#\s+(.+)$", body, flags=re.M)
    title = m.group(1).strip() if m else path.stem
    return index_text(kb, path.name, title, body, kp_codes)


def index_dir(kb: str, directory: Path) -> int:
    total = 0
    for f in sorted(directory.glob("*.md")):
        # 文件名形如 ML-U03__knowledge_points.md 时，把 kp 前缀记为标签
        codes = []
        head = f.read_text(encoding="utf-8").splitlines()[:6]
        for line in head:
            m = re.match(r"^<!--\s*kp:\s*(.+?)\s*-->$", line.strip())
            if m:
                codes = [c.strip() for c in m.group(1).split(",") if c.strip()]
        total += index_file(kb, f, codes)
    return total


def load_chunks(kb: str | None = None) -> list[dict]:
    sql = "SELECT * FROM doc_chunk"
    params: list = []
    if kb:
        sql += " WHERE kb=?"
        params.append(kb)
    rows = get_db().query(sql, params)
    for r in rows:
        r["embedding"] = loads(r["embedding"], [])
        r["kp_codes"] = loads(r["kp_codes"], [])
    return rows


def stats() -> list[dict]:
    return get_db().query(
        "SELECT kb, COUNT(*) AS chunks, COUNT(DISTINCT ref) AS docs,"
        " MIN(embed_model) AS embed_model, MIN(embed_version) AS embed_version"
        " FROM doc_chunk GROUP BY kb"
    )


def stale_embeddings() -> int:
    """换模型后需要重算的块数。embedding 记录模型版本正是为了这一刻。"""
    return get_db().scalar(
        "SELECT COUNT(*) FROM doc_chunk WHERE embed_model<>? OR embed_version<>?",
        (CONFIG.embed_model, CONFIG.embed_version),
    ) or 0
