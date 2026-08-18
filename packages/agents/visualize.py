"""图示：把抽象的状态画成图。**图由代码算，话由模型说。**

DeepTutor 的 Visualize 让模型直接写 Chart.js / Mermaid / HTML 代码，前端渲染。
灵活，但有两个问题对我们是致命的：
1. 模型画的图可能和数据对不上——而这里画的是学生的掌握度，画错等于诊断错；
2. 需要在前端引入图表库，违反"前端不得引用 CDN、无外网必须可用"。

所以这里反过来做：SVG 由确定性的纯函数生成（可单测、可复算、无依赖），
模型只写图下面那句解读，且解读不得改变任何数字。
同时导出一份 Mermaid 源码，方便教师贴进自己的文档里——但页面不依赖它渲染。
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from html import escape

from packages.core.config import CONFIG
from packages.core.models import Message, confidence_from_evidence
from packages.graph import algo as graph_algo
from packages.graph import repo as graph_repo
from packages.state import repo as state_repo
from packages.state import verification

from .base import Agent

KINDS = ("mastery_bars", "retention_curve", "ability_radar", "root_cause_chain")
KIND_LABEL = {
    "mastery_bars": "掌握度分布",
    "retention_curve": "遗忘与复检",
    "ability_radar": "能力画像雷达",
    "root_cause_chain": "根因链",
}


@dataclass
class Figure(Message):
    kind: str = ""
    title: str = ""
    svg: str = ""
    mermaid: str = ""
    data: list = field(default_factory=list)
    caption: str = ""
    confidence: float = 0.0
    evidence_count: int = 0
    caveat: str = ""
    degraded: bool = False
    note: str = "图形由确定性代码生成，模型只写解读；解读不改变任何数字"


# ---------------------------------------------------------------- 色阶
def heat_color(v: float) -> str:
    """掌握度色阶：红 → 黄 → 绿。与前端 app.js 的 heatColor 保持同一条曲线。"""
    t = max(0.0, min(1.0, float(v or 0.0)))
    stops = [(207, 34, 46), (191, 135, 0), (26, 127, 55)]
    i, k = (0, t * 2) if t < 0.5 else (1, (t - 0.5) * 2)
    a, b = stops[i], stops[i + 1]
    return "rgb(%d,%d,%d)" % tuple(round(a[j] + (b[j] - a[j]) * k) for j in range(3))


def _svg(w: int, h: int, body: str) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" '
        f'width="100%" height="{h}" role="img" font-family="inherit" font-size="12">'
        f"{body}</svg>"
    )


def _text(x: float, y: float, s: str, anchor: str = "start",
          fill: str = "currentColor", size: int = 12, opacity: float = 1.0) -> str:
    return (f'<text x="{x:.1f}" y="{y:.1f}" text-anchor="{anchor}" fill="{fill}" '
            f'font-size="{size}" opacity="{opacity}">{escape(str(s))}</text>')


# ---------------------------------------------------------------- 掌握度条形图
def mastery_bars_svg(rows: list[dict], width: int = 720) -> str:
    """每行一个知识点：实心条是当前掌握度，空心游标是计入遗忘后的估计。

    刻意把两者画在一起——只画掌握度会让人以为"达标了就没事了"。
    """
    if not rows:
        return _svg(width, 60, _text(12, 34, "暂无掌握度数据", opacity=0.6))
    pad_l, pad_r, row_h, top = 168, 56, 26, 34
    h = top + row_h * len(rows) + 16
    inner = width - pad_l - pad_r
    thr = CONFIG.teaching.mastery_threshold
    parts = [_text(12, 20, "掌握度（实心）与计入遗忘后的估计（游标）", size=12, opacity=0.7)]
    x_thr = pad_l + inner * thr
    parts.append(f'<line x1="{x_thr:.1f}" y1="{top - 8}" x2="{x_thr:.1f}" y2="{h - 12}" '
                 f'stroke="currentColor" stroke-dasharray="3 3" opacity="0.35"/>')
    parts.append(_text(x_thr + 3, top - 12, f"达标线 {thr}", size=11, opacity=0.6))
    for i, r in enumerate(rows):
        y = top + i * row_h
        p = float(r.get("p_mastery") or 0.0)
        ret = float(r.get("retained", p) or 0.0)
        name = str(r.get("name") or r.get("code") or "")
        parts.append(_text(pad_l - 8, y + 12, name[:14], anchor="end", size=12))
        parts.append(f'<rect x="{pad_l}" y="{y}" width="{inner}" height="15" rx="3" '
                     f'fill="currentColor" opacity="0.08"/>')
        parts.append(f'<rect x="{pad_l}" y="{y}" width="{inner * p:.1f}" height="15" rx="3" '
                     f'fill="{heat_color(p)}"/>')
        xr = pad_l + inner * ret
        parts.append(f'<line x1="{xr:.1f}" y1="{y - 2}" x2="{xr:.1f}" y2="{y + 17}" '
                     f'stroke="currentColor" stroke-width="2" opacity="0.75"/>')
        tag = "✓已验证" if r.get("validated") else ("⟳待复检" if r.get("due") else "")
        parts.append(_text(width - pad_r + 6, y + 12, f"{p:.2f}", size=11))
        if tag:
            parts.append(_text(width - 10, y + 12, tag, anchor="end", size=11, opacity=0.7))
    return _svg(width, h, "".join(parts))


# ---------------------------------------------------------------- 遗忘曲线
def retention_curve_svg(p0: float, halflife: float, threshold: float,
                        days_since: float = 0.0, span: int = 60,
                        width: int = 720, height: int = 200) -> str:
    """一条半衰期曲线 + 一条复检线 + 当前位置。为什么叫你复习，看图就懂。"""
    pad_l, pad_b, pad_t, pad_r = 42, 26, 18, 14
    iw, ih = width - pad_l - pad_r, height - pad_t - pad_b

    def xy(d: float, v: float) -> tuple[float, float]:
        return pad_l + iw * (d / span), pad_t + ih * (1 - max(0.0, min(1.0, v)))

    pts = []
    for step in range(span + 1):
        v = p0 * (0.5 ** (step / max(halflife, 1e-6)))
        x, y = xy(step, v)
        pts.append(f"{x:.1f},{y:.1f}")
    y_thr = xy(0, threshold)[1]
    parts = [
        f'<rect x="{pad_l}" y="{pad_t}" width="{iw}" height="{ih}" fill="currentColor" '
        f'opacity="0.04"/>',
        f'<polyline points="{" ".join(pts)}" fill="none" stroke="{heat_color(p0)}" '
        f'stroke-width="2"/>',
        f'<line x1="{pad_l}" y1="{y_thr:.1f}" x2="{pad_l + iw}" y2="{y_thr:.1f}" '
        f'stroke="currentColor" stroke-dasharray="4 3" opacity="0.5"/>',
        _text(pad_l + 4, y_thr - 4, f"复检线 {threshold}", size=11, opacity=0.7),
        _text(6, pad_t + 8, "1.0", size=11, opacity=0.6),
        _text(6, pad_t + ih, "0", size=11, opacity=0.6),
        _text(pad_l, height - 6, "今天", size=11, opacity=0.6),
        _text(pad_l + iw, height - 6, f"{span} 天后", anchor="end", size=11, opacity=0.6),
    ]
    if 0 <= days_since <= span:
        cx, cy = xy(days_since, p0 * (0.5 ** (days_since / max(halflife, 1e-6))))
        parts.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="4" fill="currentColor"/>')
        parts.append(_text(cx + 7, cy - 6, f"当前（第 {days_since:.0f} 天）", size=11))
    return _svg(width, height, "".join(parts))


# ---------------------------------------------------------------- 能力雷达
def ability_radar_svg(items: list[dict], size: int = 320) -> str:
    """M1–M8 雷达。证据不足的轴用虚线画——不确定必须看得见（铁律 4）。"""
    if not items:
        return _svg(size, 80, _text(12, 40, "暂无能力画像数据", opacity=0.6))
    cx = cy = size / 2
    r = size / 2 - 46
    n = len(items)
    parts = []
    for ring in (0.25, 0.5, 0.75, 1.0):
        pts = " ".join(
            f"{cx + r * ring * math.sin(2 * math.pi * i / n):.1f},"
            f"{cy - r * ring * math.cos(2 * math.pi * i / n):.1f}" for i in range(n)
        )
        parts.append(f'<polygon points="{pts}" fill="none" stroke="currentColor" '
                     f'opacity="{0.10 if ring < 1 else 0.3}"/>')
    poly, weak = [], False
    for i, it in enumerate(items):
        lv = max(0.0, min(1.0, float(it.get("level") or 0.0)))
        ang = 2 * math.pi * i / n
        poly.append(f"{cx + r * lv * math.sin(ang):.1f},{cy - r * lv * math.cos(ang):.1f}")
        lx, ly = cx + (r + 18) * math.sin(ang), cy - (r + 18) * math.cos(ang)
        anchor = "middle" if abs(math.sin(ang)) < 0.3 else ("start" if math.sin(ang) > 0 else "end")
        parts.append(_text(lx, ly + 4, it.get("code", ""), anchor=anchor, size=11))
        if float(it.get("confidence") or 0.0) < 0.5:
            weak = True
    dash = ' stroke-dasharray="5 4"' if weak else ""
    parts.append(f'<polygon points="{" ".join(poly)}" fill="rgb(31,111,235)" '
                 f'fill-opacity="0.18" stroke="rgb(31,111,235)" stroke-width="2"{dash}/>')
    if weak:
        parts.append(_text(cx, size - 6, "虚线：部分维度证据不足", anchor="middle",
                           size=11, opacity=0.7))
    return _svg(size, size, "".join(parts))


# ---------------------------------------------------------------- 根因链
def chain_svg(nodes: list[dict], width: int = 720) -> str:
    """根因链：从根因指向表层症状，颜色即掌握度。"""
    if not nodes:
        return _svg(width, 60, _text(12, 34, "未找到可追溯的根因链", opacity=0.6))
    box_w, box_h, gap, top = 150, 46, 34, 26
    per_row = max(1, (width - 20) // (box_w + gap))
    rows = math.ceil(len(nodes) / per_row)
    h = top + rows * (box_h + 30)
    parts = [_text(12, 16, "根因（左）→ 症状（右）：颜色即当前掌握度", size=12, opacity=0.7)]
    for i, nd in enumerate(nodes):
        r_i, c_i = divmod(i, per_row)
        x = 10 + c_i * (box_w + gap)
        y = top + r_i * (box_h + 30)
        p = float(nd.get("p_mastery") or 0.0)
        parts.append(f'<rect x="{x}" y="{y}" width="{box_w}" height="{box_h}" rx="7" '
                     f'fill="{heat_color(p)}" fill-opacity="0.16" '
                     f'stroke="{heat_color(p)}" stroke-width="1.5"/>')
        parts.append(_text(x + 10, y + 20, str(nd.get("name", ""))[:12], size=12))
        parts.append(_text(x + 10, y + 37, f"掌握度 {p:.2f}", size=11, opacity=0.75))
        if i < len(nodes) - 1 and c_i < per_row - 1:
            ax = x + box_w
            parts.append(f'<line x1="{ax + 4}" y1="{y + box_h / 2}" x2="{ax + gap - 6}" '
                         f'y2="{y + box_h / 2}" stroke="currentColor" opacity="0.5" '
                         f'stroke-width="1.5"/>')
            parts.append(f'<polygon points="{ax + gap - 6},{y + box_h / 2} '
                         f'{ax + gap - 13},{y + box_h / 2 - 4} '
                         f'{ax + gap - 13},{y + box_h / 2 + 4}" fill="currentColor" '
                         f'opacity="0.5"/>')
    return _svg(width, h, "".join(parts))


def chain_mermaid(nodes: list[dict]) -> str:
    """同一条链的 Mermaid 源码。页面不依赖它渲染，供教师贴进自己的文档。"""
    lines = ["graph LR"]
    for i, nd in enumerate(nodes):
        label = str(nd.get("name", "")).replace('"', "'")
        lines.append(f'  n{i}["{label}<br/>{float(nd.get("p_mastery") or 0):.2f}"]')
    for i in range(len(nodes) - 1):
        lines.append(f"  n{i} --> n{i + 1}")
    return "\n".join(lines)


# ---------------------------------------------------------------- 智能体
class VisualizeAgent(Agent):
    name = "visualize"
    system_prompt = (
        "你是图表解读助手。硬约束：\n"
        "1) 只解读给定的数字，不得给出任何新的数字或推断；\n"
        "2) 两到三句话，指出图里最该被注意的一点；\n"
        "3) 不评价学生聪明与否，不与他人比较。"
    )

    def render(self, kind: str, student_id: int, kp_id: int | None = None,
               limit: int = 12) -> Figure:
        if kind not in KINDS:
            raise ValueError(f"未知图形：{kind}；可用 {'/'.join(KINDS)}")
        fig = Figure(kind=kind, title=KIND_LABEL[kind])
        builder = getattr(self, f"_{kind}")
        summary = builder(fig, student_id, kp_id, limit)
        fig.caption, fig.degraded = self.express(
            {"意图": "图表解读", "图形": KIND_LABEL[kind], **summary}, max_tokens=260
        )
        return fig

    # ---- 各图的确定性数据准备 ----
    def _mastery_bars(self, fig: Figure, student_id: int, kp_id, limit: int) -> dict:
        v = verification.build(student_id)
        rows = sorted(v.items, key=lambda x: (x["p_mastery"], -x["days_since"]))[:limit]
        fig.data = rows
        fig.svg = mastery_bars_svg(rows)
        fig.evidence_count = sum(r["evidence_count"] for r in rows)
        fig.confidence = confidence_from_evidence(
            fig.evidence_count // max(1, len(rows) or 1))
        fig.caveat = v.caveat
        weak = [r["name"] for r in rows[:3]]
        return {"知识点数": len(rows), "最薄弱": "、".join(weak) or "（无）",
                "待复检数": sum(1 for r in rows if r.get("due")),
                "已验证数": sum(1 for r in rows if r.get("validated"))}

    def _retention_curve(self, fig: Figure, student_id: int, kp_id, limit: int) -> dict:
        t = CONFIG.teaching
        v = verification.build(student_id)
        item = None
        if kp_id:
            item = next((i for i in v.items if i["kp_id"] == int(kp_id)), None)
        if item is None:
            due = [i for i in v.items if i["due"]]
            item = (due or sorted(v.items, key=lambda x: -x["days_since"]) or [None])[0]
        if item is None:
            fig.svg = retention_curve_svg(0.0, t.retention_halflife_days,
                                          t.retention_threshold)
            fig.caveat = "暂无掌握度数据"
            return {"知识点": "（无）"}
        fig.data = [item]
        fig.title = f"{KIND_LABEL['retention_curve']}：{item['name']}"
        fig.svg = retention_curve_svg(item["p_mastery"], t.retention_halflife_days,
                                      t.retention_threshold, item["days_since"])
        fig.evidence_count = item["evidence_count"]
        fig.confidence = confidence_from_evidence(item["evidence_count"])
        fig.caveat = "" if item["evidence_count"] >= t.min_evidence_for_report else \
            "数据不足，仅供参考"
        return {"知识点": item["name"], "达标时掌握度": f"{item['p_mastery']:.2f}",
                "距上次": f"{item['days_since']:.0f} 天",
                "当前估计": f"{item['retained']:.2f}",
                "是否到期复检": "是" if item["due"] else "否",
                "半衰期": f"{t.retention_halflife_days} 天"}

    def _ability_radar(self, fig: Figure, student_id: int, kp_id, limit: int) -> dict:
        rows = state_repo.ability_rows(student_id)
        items = [{"code": r["code"], "name": r["name"], "level": r["level"],
                  "confidence": r["confidence"]} for r in rows]
        fig.data = items
        fig.svg = ability_radar_svg(items)
        # 能力画像是掌握度的加权聚合，证据条数取自底层掌握度记录
        fig.evidence_count = sum(
            m["evidence_count"] for m in state_repo.mastery_rows(student_id)
        )
        fig.confidence = round(
            sum(i["confidence"] for i in items) / len(items), 4) if items else 0.0
        if items:
            lo = min(items, key=lambda x: x["level"])
            hi = max(items, key=lambda x: x["level"])
            return {"维度数": len(items), "最强": f"{hi['code']} {hi['level']:.2f}",
                    "最弱": f"{lo['code']} {lo['level']:.2f}"}
        fig.caveat = "暂无能力画像数据"
        return {"维度数": 0}

    def _root_cause_chain(self, fig: Figure, student_id: int, kp_id, limit: int) -> dict:
        mastery = state_repo.mastery_vector(student_id)
        thr = CONFIG.teaching.mastery_threshold
        if not kp_id:
            weak = sorted(mastery.items(), key=lambda kv: kv[1])
            kp_id = weak[0][0] if weak else None
        if not kp_id:
            fig.svg = chain_svg([])
            fig.caveat = "暂无掌握度数据"
            return {"症状": "（无）"}
        rc = graph_algo.trace_root_cause(int(kp_id), mastery, thr)
        nodes = []
        for i in rc["path"]:
            kp = graph_repo.get_kp(i)
            if kp:
                nodes.append({"kp_id": i, "code": kp.code, "name": kp.name,
                              "p_mastery": round(mastery.get(i, 0.0), 4)})
        fig.data = nodes
        fig.svg = chain_svg(nodes)
        fig.mermaid = chain_mermaid(nodes)
        fig.evidence_count = len(nodes)
        fig.confidence = confidence_from_evidence(len(nodes))
        if rc["is_self"]:
            fig.caveat = "未找到更上游的薄弱点，问题就在这一层"
        return {"症状": nodes[-1]["name"] if nodes else "",
                "根因": nodes[0]["name"] if nodes else "",
                "链长": len(nodes)}
