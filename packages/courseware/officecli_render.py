"""课件渲染适配层——本包内唯一允许出现 subprocess 的文件。

主渲染路径：OfficeCLI（github.com/iOfficeAI/OfficeCLI，.NET 自包含二进制，
纯确定性 OOXML 引擎，不含任何 LLM、不需要 API Key）。命令行契约：

    officecli create deck.pptx
    officecli batch deck.pptx --commands '[{"command":"add","parent":"/","type":"slide",
                                             "props":{...}}, ...]' --stop-on-error --json

批处理默认事务性（--stop-on-error 时任何一条失败即整体不落盘一份"缺页"的产物），
比逐条 add 调用更适合"一次课的课件必须是完整的一份"这个要求。

二进制不存在，或调用抖动/超时/返回非零，一律降级到内建 pptx_writer
（packages/courseware/pptx_writer.py，纯标准库、零外部依赖）——教师永远拿到
一份能打开的 pptx，只是版式简单一些，返回体里 degraded=True 会被前端展示出来。
"""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from packages.core.config import CONFIG

from . import pptx_writer
from .models import DeckPlan, RenderResult


def is_available() -> bool:
    path = CONFIG.officecli_path
    if not path:
        return False
    return shutil.which(path) is not None or Path(path).is_file()


def tool_version() -> str:
    if not is_available():
        return ""
    try:
        r = subprocess.run(
            [CONFIG.officecli_path, "--version"],
            capture_output=True, text=True, timeout=CONFIG.officecli_timeout_s,
        )
        out = (r.stdout or r.stderr).strip()
        return out.splitlines()[0] if out else ""
    except Exception:
        return ""


def render_deck(plan: DeckPlan, out_path: Path) -> RenderResult:
    """DeckPlan -> pptx。OfficeCLI 不可用/失败时自动降级，永不抛出到调用方。"""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if is_available():
        try:
            return _render_via_officecli(plan, out_path)
        except Exception as exc:
            # 外部二进制的抖动不应该让教师拿不到课件——降级，但把原因留痕在日志里
            print(f"[courseware] officecli 渲染失败，降级为内建引擎：{exc}")
    return _render_via_fallback(plan, out_path)


def _to_batch_commands(plan: DeckPlan) -> list[dict]:
    cmds = []
    for s in plan.slides:
        props: dict = {"layout": s.get("layout", "bullets")}
        if s.get("title"):
            props["title"] = s["title"]
        if s.get("subtitle"):
            props["subtitle"] = s["subtitle"]
        if s.get("bullets"):
            props["bullets"] = ";".join(s["bullets"])
        chart = s.get("chart")
        if chart:
            props["chart_type"] = chart.get("chart_type", "bar")
            props["categories"] = ",".join(str(c) for c in chart.get("categories", []))
            for series in chart.get("series", []):
                props[f"series_{series.get('name', '')}"] = ",".join(
                    str(v) for v in series.get("values", [])
                )
        cmds.append({"command": "add", "parent": "/", "type": "slide", "props": props})
    return cmds


def _render_via_officecli(plan: DeckPlan, out_path: Path) -> RenderResult:
    cli = CONFIG.officecli_path
    timeout = CONFIG.officecli_timeout_s
    subprocess.run(
        [cli, "create", str(out_path)], check=True, capture_output=True, timeout=timeout,
    )
    cmds_json = json.dumps(_to_batch_commands(plan), ensure_ascii=False)
    r = subprocess.run(
        [cli, "batch", str(out_path), "--commands", cmds_json, "--stop-on-error", "--json"],
        capture_output=True, text=True, timeout=timeout,
    )
    if r.returncode != 0:
        raise RuntimeError(f"officecli batch 返回非零：{(r.stderr or r.stdout)[:300]}")
    return RenderResult(
        file_path=str(out_path), artifact_type="pptx", render_tool="officecli",
        render_tool_version=tool_version(), degraded=False,
    )


def _render_via_fallback(plan: DeckPlan, out_path: Path) -> RenderResult:
    """内建降级引擎：复用 pptx_writer（scripts/make_deck.py 同款），保证零外部依赖也能出真 pptx。"""
    out_path = out_path.with_suffix(".pptx")
    specs = [_slide_to_spec(s) for s in plan.slides]
    slides_xml = [
        pptx_writer.build_slide(spec, i, len(specs)) for i, spec in enumerate(specs, 1)
    ]
    try:
        pptx_writer.write_pptx(slides_xml, out_path, plan.title or "课件")
        return RenderResult(
            file_path=str(out_path), artifact_type="pptx", render_tool="builtin_stdlib",
            render_tool_version="v1", degraded=True,
        )
    except Exception as exc:  # 理论上不应发生（纯 stdlib zipfile），兜底到纯文本
        return _render_as_markdown(plan, out_path.with_suffix(".md"), str(exc))


def _slide_to_spec(s: dict) -> dict:
    layout = s.get("layout", "bullets")
    if layout == "title":
        return {"kind": "cover", "title": s.get("title", ""),
                "bullets": [s.get("subtitle", "")] if s.get("subtitle") else [],
                "note": ""}
    if layout == "chart" and s.get("chart"):
        chart = s["chart"]
        rows = [["类别", *(ser.get("name", "") for ser in chart.get("series", []))]]
        for i, cat in enumerate(chart.get("categories", [])):
            rows.append([str(cat)] + [
                str(ser.get("values", [])[i] if i < len(ser.get("values", [])) else "")
                for ser in chart.get("series", [])
            ])
        return {"kind": "bullets", "title": s.get("title", ""), "table": rows,
                "note": f"数据来源：{chart.get('source', '')}" if chart.get("source") else ""}
    return {"kind": "bullets", "title": s.get("title", ""), "bullets": s.get("bullets", [])}


def _render_as_markdown(plan: DeckPlan, out_path: Path, reason: str) -> RenderResult:
    lines = [f"# {plan.title}", "", f"（渲染引擎异常已降级为结构化大纲：{reason}）", ""]
    for s in plan.slides:
        lines.append(f"## {s.get('title', '')}")
        for b in s.get("bullets", []):
            lines.append(f"- {b}")
        lines.append("")
    out_path.write_text("\n".join(lines), encoding="utf-8")
    return RenderResult(
        file_path=str(out_path), artifact_type="markdown", render_tool="fallback_markdown",
        render_tool_version="v1", degraded=True, error=reason,
    )
