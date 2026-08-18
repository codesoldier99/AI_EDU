"""生成汇报用 PPTX（纯标准库，不依赖 python-pptx / LibreOffice）。

    python3 scripts/make_deck.py            # → docs/slides/院长实验班AI教学系统_汇报.pptx

幻灯片内容写在 deck_content.py 里，改文案不用碰这里。
所有数字都从当前数据库读，避免 PPT 与系统对不上。

底层 OOXML 拼装引擎已抽到 packages/courseware/pptx_writer.py——课件生成功能
（packages/courseware/deck.py）在 OfficeCLI 不可用时的降级路径复用的是同一份
引擎，这里只保留"这份汇报 PPT 具体长什么样"的编排逻辑。
"""
from __future__ import annotations

from pathlib import Path

import _bootstrap  # noqa: F401
from packages.core.config import ROOT
from packages.courseware.pptx_writer import _png_size, build_slide, write_pptx


def main() -> None:
    from deck_content import build_specs

    specs = build_specs()
    total = len(specs)
    media: dict[int, Path] = {}
    for i, spec in enumerate(specs, 1):
        if spec.get("image"):
            img = ROOT / spec["image"]
            if img.exists():
                spec["_imgsize"] = _png_size(img)
                spec["_rid"] = "rId2"      # slideLayout 占 rId1，图片顺位 rId2
                media[i] = img
            else:
                spec.pop("image")
    slides = [build_slide(s, i, total) for i, s in enumerate(specs, 1)]
    out = ROOT / "docs" / "slides" / "院长实验班AI教学系统_汇报.pptx"
    write_pptx(slides, out, "院长实验班 AI 教学系统 · 汇报", media)
    print(f"已生成 {out}  （{total} 页）")


if __name__ == "__main__":
    main()
