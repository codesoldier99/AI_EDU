"""生成汇报用 PPTX（纯标准库，不依赖 python-pptx / LibreOffice）。

    python3 scripts/make_deck.py            # → docs/slides/院长实验班AI教学系统_汇报.pptx

幻灯片内容写在 deck_content.py 里，改文案不用碰这里。
所有数字都从当前数据库读，避免 PPT 与系统对不上。
"""
from __future__ import annotations

import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

import _bootstrap  # noqa: F401
from packages.core.config import ROOT

W, H = 12192000, 6858000          # 16:9，EMU
INK, ACCENT, MUTED = "1B1F24", "1F6FEB", "6B7280"
BAD, OK, WARN = "CF222E", "1A7F37", "BF8700"
EA = "微软雅黑"
LATIN = "Segoe UI"


# ----------------------------------------------------------------- XML 片段
def _rpr(sz: int, color: str, bold: bool = False, italic: bool = False) -> str:
    return (
        f'<a:rPr lang="zh-CN" altLang="en-US" sz="{sz * 100}"'
        f'{" b=\"1\"" if bold else ""}{" i=\"1\"" if italic else ""} dirty="0">'
        f'<a:solidFill><a:srgbClr val="{color}"/></a:solidFill>'
        f'<a:latin typeface="{LATIN}"/><a:ea typeface="{EA}"/></a:rPr>'
    )


def _para(text: str, sz: int, color: str, bold: bool = False, indent: int = 0,
          bullet: str | None = None, space_before: int = 400,
          align: str = "l", italic: bool = False) -> str:
    marl = 320000 if bullet else 0
    bu = (f'<a:buFont typeface="Arial"/><a:buChar char="{escape(bullet)}"/>'
          if bullet else "<a:buNone/>")
    runs = ""
    # 用 ** 包裹的片段加粗，方便文案里做局部强调
    for i, seg in enumerate(text.split("**")):
        if not seg:
            continue
        runs += f"<a:r>{_rpr(sz, color, bold or i % 2 == 1, italic)}<a:t>{escape(seg)}</a:t></a:r>"
    if not runs:
        runs = f"<a:r>{_rpr(sz, color, bold, italic)}<a:t></a:t></a:r>"
    return (
        f'<a:p><a:pPr lvl="{indent}" marL="{marl}" indent="{-marl}" algn="{align}">'
        f'<a:lnSpc><a:spcPct val="118000"/></a:lnSpc>'
        f'<a:spcBef><a:spcPts val="{space_before}"/></a:spcBef>{bu}</a:pPr>{runs}</a:p>'
    )


def _txbox(idx: int, name: str, x: int, y: int, cx: int, cy: int, paras: str,
           anchor: str = "t") -> str:
    return (
        f'<p:sp><p:nvSpPr><p:cNvPr id="{idx}" name="{name}"/><p:cNvSpPr txBox="1"/>'
        f'<p:nvPr/></p:nvSpPr><p:spPr><a:xfrm><a:off x="{x}" y="{y}"/>'
        f'<a:ext cx="{cx}" cy="{cy}"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/>'
        f'</a:prstGeom><a:noFill/></p:spPr><p:txBody><a:bodyPr wrap="square" anchor="{anchor}">'
        f'<a:normAutofit/></a:bodyPr><a:lstStyle/>{paras}</p:txBody></p:sp>'
    )


def _rect(idx: int, x: int, y: int, cx: int, cy: int, color: str, alpha: int = 100000) -> str:
    return (
        f'<p:sp><p:nvSpPr><p:cNvPr id="{idx}" name="r{idx}"/><p:cNvSpPr/><p:nvPr/>'
        f'</p:nvSpPr><p:spPr><a:xfrm><a:off x="{x}" y="{y}"/><a:ext cx="{cx}" cy="{cy}"/>'
        f'</a:xfrm><a:prstGeom prst="roundRect"><a:avLst><a:gd name="adj" fmla="val 6000"/>'
        f'</a:avLst></a:prstGeom><a:solidFill><a:srgbClr val="{color}">'
        f'<a:alpha val="{alpha}"/></a:srgbClr></a:solidFill><a:ln><a:noFill/></a:ln>'
        f'</p:spPr><p:txBody><a:bodyPr/><a:lstStyle/><a:p/></p:txBody></p:sp>'
    )


def _table(idx: int, x: int, y: int, cx: int, rows: list[list[str]],
           widths: list[float], sz: int = 15, row_h: int = 380000) -> str:
    total = sum(widths)
    cols = "".join(f'<a:gridCol w="{int(cx * w / total)}"/>' for w in widths)
    body = ""
    for r, row in enumerate(rows):
        head = r == 0
        cells = ""
        for cell in row:
            fill = "F2F5F9" if head else "FFFFFF"
            color = INK if head else "333333"
            cells += (
                f'<a:tc><a:txBody><a:bodyPr/><a:lstStyle/>'
                f'{_para(cell, sz, color, bold=head, space_before=0)}</a:txBody>'
                f'<a:tcPr marL="91440" marR="91440" marT="45720" marB="45720" anchor="ctr">'
                f'<a:lnB w="6350"><a:solidFill><a:srgbClr val="E5E7EB"/></a:solidFill></a:lnB>'
                f'<a:solidFill><a:srgbClr val="{fill}"/></a:solidFill></a:tcPr></a:tc>'
            )
        body += f'<a:tr h="{row_h}">{cells}</a:tr>'
    return (
        f'<p:graphicFrame><p:nvGraphicFramePr><p:cNvPr id="{idx}" name="t{idx}"/>'
        f'<p:cNvGraphicFramePr/><p:nvPr/></p:nvGraphicFramePr>'
        f'<p:xfrm><a:off x="{x}" y="{y}"/><a:ext cx="{cx}" cy="{row_h * len(rows)}"/></p:xfrm>'
        f'<a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/table">'
        f'<a:tbl><a:tblPr firstRow="1"/><a:tblGrid>{cols}</a:tblGrid>{body}</a:tbl>'
        f'</a:graphicData></a:graphic></p:graphicFrame>'
    )


def _pic(idx: int, rid: str, x: int, y: int, cx: int, cy: int) -> str:
    return (
        f'<p:pic><p:nvPicPr><p:cNvPr id="{idx}" name="img{idx}"/>'
        f'<p:cNvPicPr><a:picLocks noChangeAspect="1"/></p:cNvPicPr><p:nvPr/></p:nvPicPr>'
        f'<p:blipFill><a:blip r:embed="{rid}"/><a:stretch><a:fillRect/></a:stretch></p:blipFill>'
        f'<p:spPr><a:xfrm><a:off x="{x}" y="{y}"/><a:ext cx="{cx}" cy="{cy}"/></a:xfrm>'
        f'<a:prstGeom prst="rect"><a:avLst/></a:prstGeom>'
        f'<a:ln w="9525"><a:solidFill><a:srgbClr val="D5DBE3"/></a:solidFill></a:ln>'
        f'</p:spPr></p:pic>'
    )


def _png_size(path: Path) -> tuple[int, int]:
    import struct

    with path.open("rb") as fh:
        head = fh.read(24)
    return struct.unpack(">II", head[16:24])


def _slide_xml(shapes: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
        'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">'
        '<p:cSld><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/>'
        '</p:nvGrpSpPr><p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/>'
        '<a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr>'
        f'{shapes}</p:spTree></p:cSld><p:clrMapOvr><a:overrideClrMapping bg1="lt1" tx1="dk1" '
        'bg2="lt2" tx2="dk2" accent1="accent1" accent2="accent2" accent3="accent3" '
        'accent4="accent4" accent5="accent5" accent6="accent6" hlink="hlink" '
        'folHlink="folHlink"/></p:clrMapOvr></p:sld>'
    )


# ----------------------------------------------------------------- 版式
M = 720000          # 左右边距
TOP = 620000


def build_slide(spec: dict, page: int, total: int) -> str:
    """spec: {kind, title, sub, bullets, table, note, quote, cols}"""
    idx = 10
    sh = ""
    kind = spec.get("kind", "bullets")

    if kind == "cover":
        sh += _rect(2, 0, 0, W, H, ACCENT, 100000)
        sh += _txbox(3, "t", M, 1900000, W - 2 * M, 1500000,
                     _para(spec["title"], 44, "FFFFFF", bold=True, space_before=0))
        sh += _txbox(4, "s", M, 3500000, W - 2 * M, 1600000,
                     "".join(_para(l, 19, "E8F0FE", space_before=260)
                             for l in spec.get("bullets", [])))
        sh += _txbox(5, "f", M, H - 900000, W - 2 * M, 500000,
                     _para(spec.get("note", ""), 14, "C7DAF7", space_before=0))
        return _slide_xml(sh)

    if kind == "twobig":
        # 两个并列的大结论：一眼看到，不需要读
        sh += _txbox(3, "title", M, TOP - 120000, W - 2 * M, 620000,
                     _para(spec["title"], 28, INK, bold=True, space_before=0, align="ctr"))
        if spec.get("sub"):
            sh += _txbox(4, "sub", M, TOP + 400000, W - 2 * M, 420000,
                         _para(spec["sub"], 15, MUTED, space_before=0, align="ctr"))
        panels = spec["panels"]
        gap = 300000
        pw = (W - 2 * M - gap) // 2
        py, ph = TOP + 950000, 3500000
        for i, (big, label, lines, color) in enumerate(panels):
            x = M + i * (pw + gap)
            sh += _rect(idx, x, py, pw, ph, color, 10000)
            idx += 1
            sh += _txbox(idx, f"b{i}", x, py + 260000, pw, 1500000,
                         _para(big, 96, color, bold=True, space_before=0, align="ctr"))
            idx += 1
            sh += _txbox(idx, f"l{i}", x, py + 1560000, pw, 500000,
                         _para(label, 19, INK, bold=True, space_before=0, align="ctr"))
            idx += 1
            body = "".join(_para(t, 14, "4A5561", space_before=300, align="ctr")
                           for t in lines)
            sh += _txbox(idx, f"t{i}", x + 200000, py + 2080000, pw - 400000,
                         ph - 2100000, body)
            idx += 1
        if spec.get("note"):
            sh += _txbox(idx, "note", M, H - 780000, W - 2 * M, 460000,
                         _para(spec["note"], 14, MUTED, space_before=0, align="ctr"))
            idx += 1
        sh += _txbox(90, "pg", W - M - 900000, H - 480000, 900000, 300000,
                     _para(f"{page} / {total}", 11, MUTED, space_before=0, align="r"))
        return _slide_xml(sh)

    if kind == "section":
        sh += _rect(2, 0, 0, W, H, "F7F9FC", 100000)
        sh += _rect(3, M, 2500000, 180000, 900000, ACCENT)
        sh += _txbox(4, "t", M + 380000, 2500000, W - 2 * M, 1000000,
                     _para(spec["title"], 36, INK, bold=True, space_before=0))
        if spec.get("sub"):
            sh += _txbox(5, "s", M + 380000, 3550000, W - 2 * M - 380000, 900000,
                         _para(spec["sub"], 19, MUTED, space_before=0))
        return _slide_xml(sh)

    # 常规页：标题 + 强调条 + 内容
    sh += _rect(2, M, TOP + 40000, 90000, 420000, ACCENT)
    sh += _txbox(3, "title", M + 220000, TOP, W - 2 * M, 620000,
                 _para(spec["title"], 27, INK, bold=True, space_before=0))
    if spec.get("sub"):
        sh += _txbox(4, "sub", M + 220000, TOP + 560000, W - 2 * M, 460000,
                     _para(spec["sub"], 15, MUTED, space_before=0))

    y = TOP + (1080000 if spec.get("sub") else 780000)

    if spec.get("quote"):
        sh += _rect(idx, M, y, W - 2 * M, 1100000, ACCENT, 12000)
        idx += 1
        sh += _txbox(idx, "q", M + 300000, y + 180000, W - 2 * M - 600000, 900000,
                     _para(spec["quote"], 22, ACCENT, bold=True, space_before=0))
        idx += 1
        y += 1350000

    if spec.get("table"):
        rows = spec["table"]
        widths = spec.get("widths") or [1] * len(rows[0])
        sh += _table(idx, M, y, W - 2 * M, rows, widths,
                     sz=spec.get("tsz", 15), row_h=spec.get("rowh", 400000))
        idx += 1
        y += 400000 * len(rows) + 260000

    if spec.get("cols"):
        colw = (W - 2 * M - 400000) // 2
        for i, (head, items, color) in enumerate(spec["cols"]):
            x = M + i * (colw + 400000)
            sh += _rect(idx, x, y, colw, 300000 + 330000 * len(items), color, 9000)
            idx += 1
            body = _para(head, 17, color, bold=True, space_before=0)
            body += "".join(_para(t, 15, "333333", bullet="•", space_before=200)
                            for t in items)
            sh += _txbox(idx, f"c{i}", x + 220000, y + 150000, colw - 440000,
                         300000 + 330000 * len(items), body)
            idx += 1
        y += 400000 + 330000 * max(len(c[1]) for c in spec["cols"])

    if spec.get("image"):
        iw, ih = spec["_imgsize"]
        avail_w = W - 2 * M
        avail_h = H - y - 900000
        scale = min(avail_w / iw, avail_h / ih)
        cw, ch = int(iw * scale), int(ih * scale)
        sh += _pic(idx, spec["_rid"], M + (avail_w - cw) // 2, y, cw, ch)
        idx += 1
        y += ch + 200000

    if spec.get("bullets"):
        body = ""
        for b in spec["bullets"]:
            lvl = 0
            text = b
            while text.startswith("  "):
                lvl += 1
                text = text[2:]
            sz = 19 if lvl == 0 else 16
            color = INK if lvl == 0 else "4A5561"
            body += _para(text, sz, color, indent=lvl,
                          bullet="▪" if lvl == 0 else "–", space_before=340)
        sh += _txbox(idx, "body", M, y, W - 2 * M, H - y - 800000, body)
        idx += 1

    if spec.get("note"):
        sh += _rect(idx, M, H - 900000, W - 2 * M, 460000, WARN, 10000)
        idx += 1
        sh += _txbox(idx, "note", M + 200000, H - 860000, W - 2 * M - 400000, 400000,
                     _para(spec["note"], 14, "8A6400", space_before=0))
        idx += 1

    sh += _txbox(90, "pg", W - M - 900000, H - 480000, 900000, 300000,
                 _para(f"{page} / {total}", 11, MUTED, space_before=0, align="r"))
    return _slide_xml(sh)


# ----------------------------------------------------------------- 打包
THEME = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<a:theme xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" name="AIEDU">'
    '<a:themeElements><a:clrScheme name="AIEDU"><a:dk1><a:srgbClr val="1B1F24"/></a:dk1>'
    '<a:lt1><a:srgbClr val="FFFFFF"/></a:lt1><a:dk2><a:srgbClr val="333333"/></a:dk2>'
    '<a:lt2><a:srgbClr val="F2F5F9"/></a:lt2><a:accent1><a:srgbClr val="1F6FEB"/></a:accent1>'
    '<a:accent2><a:srgbClr val="1A7F37"/></a:accent2><a:accent3><a:srgbClr val="BF8700"/>'
    '</a:accent3><a:accent4><a:srgbClr val="CF222E"/></a:accent4>'
    '<a:accent5><a:srgbClr val="6B7280"/></a:accent5><a:accent6><a:srgbClr val="8250DF"/>'
    '</a:accent6><a:hlink><a:srgbClr val="1F6FEB"/></a:hlink>'
    '<a:folHlink><a:srgbClr val="8250DF"/></a:folHlink></a:clrScheme>'
    f'<a:fontScheme name="AIEDU"><a:majorFont><a:latin typeface="{LATIN}"/>'
    f'<a:ea typeface="{EA}"/><a:cs typeface=""/></a:majorFont>'
    f'<a:minorFont><a:latin typeface="{LATIN}"/><a:ea typeface="{EA}"/><a:cs typeface=""/>'
    '</a:minorFont></a:fontScheme><a:fmtScheme name="AIEDU">'
    '<a:fillStyleLst><a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'
    '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'
    '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:fillStyleLst>'
    '<a:lnStyleLst><a:ln w="6350"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln>'
    '<a:ln w="12700"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln>'
    '<a:ln w="19050"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln></a:lnStyleLst>'
    '<a:effectStyleLst><a:effectStyle><a:effectLst/></a:effectStyle>'
    '<a:effectStyle><a:effectLst/></a:effectStyle>'
    '<a:effectStyle><a:effectLst/></a:effectStyle></a:effectStyleLst>'
    '<a:bgFillStyleLst><a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'
    '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill>'
    '<a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:bgFillStyleLst>'
    '</a:fmtScheme></a:themeElements></a:theme>'
)

EMPTY_TREE = (
    '<p:cSld><p:spTree><p:nvGrpSpPr><p:cNvPr id="1" name=""/><p:cNvGrpSpPr/><p:nvPr/>'
    '</p:nvGrpSpPr><p:grpSpPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="0" cy="0"/>'
    '<a:chOff x="0" y="0"/><a:chExt cx="0" cy="0"/></a:xfrm></p:grpSpPr></p:spTree></p:cSld>'
)

MASTER = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<p:sldMaster xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
    'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">'
    f'{EMPTY_TREE}<p:clrMap bg1="lt1" tx1="dk1" bg2="lt2" tx2="dk2" accent1="accent1" '
    'accent2="accent2" accent3="accent3" accent4="accent4" accent5="accent5" '
    'accent6="accent6" hlink="hlink" folHlink="folHlink"/>'
    '<p:sldLayoutIdLst><p:sldLayoutId id="2147483649" r:id="rId1"/></p:sldLayoutIdLst>'
    '</p:sldMaster>'
)

LAYOUT = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<p:sldLayout xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
    'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
    'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main" '
    f'type="blank" preserve="1">{EMPTY_TREE}</p:sldLayout>'
)


def write_pptx(slides: list[str], out: Path, title: str,
               media: dict[int, Path] | None = None) -> None:
    """media: {幻灯片序号(1起) -> 图片路径}"""
    media = media or {}
    n = len(slides)
    ct = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
          '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
          '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.'
          'relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>'
          '<Override PartName="/ppt/presentation.xml" ContentType="application/vnd.'
          'openxmlformats-officedocument.presentationml.presentation.main+xml"/>'
          '<Override PartName="/ppt/slideMasters/slideMaster1.xml" ContentType="application/'
          'vnd.openxmlformats-officedocument.presentationml.slideMaster+xml"/>'
          '<Override PartName="/ppt/slideLayouts/slideLayout1.xml" ContentType="application/'
          'vnd.openxmlformats-officedocument.presentationml.slideLayout+xml"/>'
          '<Override PartName="/ppt/theme/theme1.xml" ContentType="application/vnd.'
          'openxmlformats-officedocument.theme+xml"/>'
          '<Override PartName="/docProps/core.xml" ContentType="application/vnd.'
          'openxmlformats-package.core-properties+xml"/>'
          '<Override PartName="/docProps/app.xml" ContentType="application/vnd.'
          'openxmlformats-officedocument.extended-properties+xml"/>']
    if media:
        ct.append('<Default Extension="png" ContentType="image/png"/>')
    for i in range(1, n + 1):
        ct.append(f'<Override PartName="/ppt/slides/slide{i}.xml" ContentType="application/'
                  'vnd.openxmlformats-officedocument.presentationml.slide+xml"/>')
    ct.append("</Types>")

    sld_ids = "".join(
        f'<p:sldId id="{255 + i}" r:id="rId{i + 1}"/>' for i in range(1, n + 1)
    )
    pres = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<p:presentation xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" '
        'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">'
        '<p:sldMasterIdLst><p:sldMasterId id="2147483648" r:id="rId1"/></p:sldMasterIdLst>'
        f'<p:sldIdLst>{sld_ids}</p:sldIdLst>'
        f'<p:sldSz cx="{W}" cy="{H}"/><p:notesSz cx="{H}" cy="{W}"/></p:presentation>'
    )
    pres_rels = ['<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                 '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
                 'relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.'
                 'org/officeDocument/2006/relationships/slideMaster" '
                 'Target="slideMasters/slideMaster1.xml"/>']
    for i in range(1, n + 1):
        pres_rels.append(
            f'<Relationship Id="rId{i + 1}" Type="http://schemas.openxmlformats.org/'
            f'officeDocument/2006/relationships/slide" Target="slides/slide{i}.xml"/>')
    pres_rels.append(f'<Relationship Id="rId{n + 2}" Type="http://schemas.openxmlformats.org/'
                     'officeDocument/2006/relationships/theme" Target="theme/theme1.xml"/>')
    pres_rels.append("</Relationships>")

    def rels(*pairs):
        body = "".join(
            f'<Relationship Id="rId{i + 1}" Type="http://schemas.openxmlformats.org/'
            f'officeDocument/2006/relationships/{t}" Target="{tgt}"/>'
            for i, (t, tgt) in enumerate(pairs))
        return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/'
                f'relationships">{body}</Relationships>')

    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", "".join(ct))
        z.writestr("_rels/.rels", rels(
            ("officeDocument", "ppt/presentation.xml"),
            ("metadata/core-properties", "docProps/core.xml"),
            ("extended-properties", "docProps/app.xml")))
        z.writestr("docProps/core.xml",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/'
                   '2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/">'
                   f"<dc:title>{escape(title)}</dc:title>"
                   "<dc:creator>人工智能与交通工程学院</dc:creator></cp:coreProperties>")
        z.writestr("docProps/app.xml",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/'
                   f'extended-properties"><Slides>{n}</Slides></Properties>')
        z.writestr("ppt/presentation.xml", pres)
        z.writestr("ppt/_rels/presentation.xml.rels", "".join(pres_rels))
        z.writestr("ppt/theme/theme1.xml", THEME)
        z.writestr("ppt/slideMasters/slideMaster1.xml", MASTER)
        z.writestr("ppt/slideMasters/_rels/slideMaster1.xml.rels", rels(
            ("slideLayout", "../slideLayouts/slideLayout1.xml"),
            ("theme", "../theme/theme1.xml")))
        z.writestr("ppt/slideLayouts/slideLayout1.xml", LAYOUT)
        z.writestr("ppt/slideLayouts/_rels/slideLayout1.xml.rels", rels(
            ("slideMaster", "../slideMasters/slideMaster1.xml")))
        for i, s in enumerate(slides, 1):
            z.writestr(f"ppt/slides/slide{i}.xml", s)
            pairs = [("slideLayout", "../slideLayouts/slideLayout1.xml")]
            if i in media:
                z.writestr(f"ppt/media/image{i}.png", media[i].read_bytes())
                pairs.append(("image", f"../media/image{i}.png"))
            z.writestr(f"ppt/slides/_rels/slide{i}.xml.rels", rels(*pairs))


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
