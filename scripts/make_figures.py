"""生成汇报用的三张示意图（SVG → PNG，经 LibreOffice 转换，无第三方 Python 依赖）。

    python3 scripts/make_figures.py            # 全部
    python3 scripts/make_figures.py tsunami    # 只出一张

为什么不去网上找图：一是演示环境常常没有外网（与前端不引 CDN 是同一条规矩），
二是来路不明的配图有版权风险，三是**图里的字要能随数据改**——
构想图上的角色清单会变，网图改不了。

SVG 是手写的：形状全部由确定性坐标算出，改一个数就能重出，可进 git 复算。
"""
from __future__ import annotations

import math
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import _bootstrap  # noqa: F401
from packages.core.config import CONFIG, ROOT
from packages.tools import qrcode

OUT = ROOT / "docs" / "images"
W, H = 1600, 900

# 与 PPT 主题同一套色，图和页面才像一件东西
INK, ACCENT, MUTED = "#1B1F24", "#1F6FEB", "#6B7280"
OK, WARN, BAD, VIOLET = "#1A7F37", "#BF8700", "#CF222E", "#7C3AED"
FONT = "Noto Sans CJK SC, Microsoft YaHei, sans-serif"


def _svg(body: str, bg: str = "#FFFFFF", defs: str = "") -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" '
        f'viewBox="0 0 {W} {H}" font-family="{FONT}">'
        f"<defs>{defs}</defs>"
        f'<rect width="{W}" height="{H}" fill="{bg}"/>{body}</svg>'
    )


def _wave(base: float, amp: float, phase: float, fill: str, opacity: float,
          periods: float = 1.6) -> str:
    """一条正弦水面。采样成折线而不是拼贝塞尔——采样点够密，看不出差别，
    但曲线的形状可以直接由公式控制，调参不用重画控制点。"""
    pts = []
    steps = 160
    for i in range(steps + 1):
        x = W * i / steps
        y = base + amp * math.sin(2 * math.pi * periods * i / steps + phase)
        pts.append(f"{x:.1f},{y:.1f}")
    return (f'<path d="M{" L".join(pts)} L{W},{H} L0,{H} Z" '
            f'fill="{fill}" opacity="{opacity}"/>')


# ------------------------------------------------------------------ 一、AI 海啸
def fig_tsunami() -> str:
    defs = (
        '<linearGradient id="sky" x1="0" y1="0" x2="0" y2="1">'
        '<stop offset="0" stop-color="#050B1C"/><stop offset="1" stop-color="#0A1A3A"/>'
        "</linearGradient>"
        '<linearGradient id="face" x1="0.9" y1="0" x2="0.1" y2="1">'
        '<stop offset="0" stop-color="#2E86FF"/><stop offset="0.45" stop-color="#1F6FEB"/>'
        '<stop offset="1" stop-color="#0A2A63"/></linearGradient>'
        '<linearGradient id="lip" x1="0" y1="0" x2="0.3" y2="1">'
        '<stop offset="0" stop-color="#FFFFFF"/><stop offset="1" stop-color="#8FC0FF"/>'
        "</linearGradient>"
        '<linearGradient id="shoulder" x1="1" y1="0" x2="0" y2="1">'
        '<stop offset="0" stop-color="#7FB3FF" stop-opacity="0.9"/>'
        '<stop offset="1" stop-color="#7FB3FF" stop-opacity="0"/></linearGradient>'
    )
    b = ['<rect width="%d" height="%d" fill="url(#sky)"/>' % (W, H)]
    # 夜空里的"数据星点"——提示这股浪是由信息构成的，不是水
    for i in range(90):
        x, y = (i * 173) % 1000, (i * 97) % 460
        b.append(f'<circle cx="{x}" cy="{y}" r="{1 + (i % 3) * 0.6}" '
                 f'fill="#9CC6FF" opacity="0.3"/>')

    # 岸上的校园：一排小得可怜的教学楼，浪压过来时的比例全靠它
    gx, gy = 90, 706
    for i, (w, h) in enumerate([(70, 90), (54, 130), (86, 70), (48, 110), (64, 96)]):
        x = gx + i * 96
        b.append(f'<rect x="{x}" y="{gy - h}" width="{w}" height="{h}" fill="#16305C"/>')
        for r in range(int(h // 26)):
            for c in range(int(w // 22)):
                b.append(f'<rect x="{x + 8 + c * 22}" y="{gy - h + 12 + r * 26}" '
                         f'width="8" height="10" fill="#F2C14E" opacity="0.6"/>')
    for i in range(5):
        x = 150 + i * 78
        b.append(f'<circle cx="{x}" cy="{gy + 16}" r="7" fill="#2A4B85"/>'
                 f'<rect x="{x - 6}" y="{gy + 24}" width="12" height="24" rx="5" fill="#2A4B85"/>')

    # 浪：一整面从右上压向左下的浪壁，顶上卷出一道唇。
    # 形状全部手算，改一个控制点就能重出——这也是不去网上找图的原因之一。
    face = ("M1600,900 L1600,70 "
            "C1400,86 1258,196 1176,352 "
            "C1104,492 936,624 690,700 "
            "C468,768 232,800 0,812 L0,900 Z")
    b.append(f'<path d="{face}" fill="url(#face)"/>')
    # 浪壁上的高光带，让那面墙有厚度
    b.append('<path d="M1600,70 C1400,86 1258,196 1176,352 C1104,492 936,624 690,700 '
             'C900,660 1080,560 1190,420 C1300,280 1420,150 1600,132 Z" '
             'fill="url(#shoulder)"/>')
    # 卷唇：从浪顶翻过来、向左下勾回去的那一钩
    b.append('<path d="M1600,70 C1398,86 1256,198 1174,354 '
             'C1252,272 1356,218 1470,220 '
             'C1352,266 1268,352 1226,462 '
             'C1244,332 1352,168 1600,158 Z" fill="url(#lip)" opacity="0.95"/>')
    # 唇下的泡沫与飞溅
    for i in range(60):
        a = i * 0.79
        x = 1210 + math.cos(a) * (40 + i * 7)
        y = 360 + math.sin(a) * (30 + i * 4) - i * 1.2
        b.append(f'<circle cx="{x:.0f}" cy="{y:.0f}" r="{2 + (i % 4)}" '
                 f'fill="#EAF3FF" opacity="{max(0.05, 0.6 - i * 0.009):.2f}"/>')
    # 浪脚拍在岸上的白沫
    for i in range(40):
        x = 620 - i * 14
        y = 712 + (i % 5) * 6
        b.append(f'<circle cx="{x}" cy="{y}" r="{2 + (i % 3)}" fill="#CFE4FF" '
                 f'opacity="{max(0.05, 0.45 - i * 0.01):.2f}"/>')

    # 浪里裹着的东西——这是它跟普通海浪的区别
    for txt, x, y, sz, op in [("大模型", 1442, 372, 34, 0.5),
                              ("智能体", 1330, 476, 30, 0.42),
                              ("生成式作业", 1440, 552, 26, 0.34),
                              ("检索即得", 1252, 606, 24, 0.3),
                              ("AI 代写", 1470, 676, 24, 0.28)]:
        b.append(f'<text x="{x}" y="{y}" font-size="{sz}" fill="#FFFFFF" '
                 f'opacity="{op}" text-anchor="middle">{txt}</text>')

    b.append(_wave(806, 10, 0.6, "#0A2A63", 0.95, 2.4))
    b.append('<text x="90" y="150" font-size="72" font-weight="bold" fill="#FFFFFF">'
             "AI 海啸已经到岸</text>")
    b.append('<text x="92" y="212" font-size="30" fill="#9CC6FF">'
             "它冲掉的不是某一门课，是我们判断「他学会了没有」的全部老办法</text>")
    return _svg("".join(b), "#050B1C", defs)


# ------------------------------------------------------------------ 二、诺亚方舟
def fig_ark() -> str:
    defs = (
        '<linearGradient id="dawn" x1="0" y1="0" x2="0" y2="1">'
        '<stop offset="0" stop-color="#08183E"/><stop offset="0.55" stop-color="#1B4E96"/>'
        '<stop offset="1" stop-color="#3E86CE"/></linearGradient>'
        '<linearGradient id="hull" x1="0" y1="0" x2="0" y2="1">'
        '<stop offset="0" stop-color="#A9702F"/><stop offset="1" stop-color="#5A3818"/>'
        "</linearGradient>"
        '<radialGradient id="sun" cx="0.5" cy="0.5" r="0.5">'
        '<stop offset="0" stop-color="#FFE9A8" stop-opacity="0.85"/>'
        '<stop offset="1" stop-color="#FFE9A8" stop-opacity="0"/></radialGradient>'
    )
    b = ['<rect width="%d" height="%d" fill="url(#dawn)"/>' % (W, H)]
    b.append('<circle cx="1330" cy="250" r="250" fill="url(#sun)"/>')
    b.append('<circle cx="1330" cy="250" r="62" fill="#FFD86B" opacity="0.9"/>')

    # 彩虹压得很低、也很淡：它是背景里的一个符号，不是主角，
    # 圆心放到画布下方，弧顶才不会顶到标题。
    for i, c in enumerate(["#CF222E", "#E06C00", "#BF8700", "#1A7F37", "#1F6FEB", "#7C3AED"]):
        r = 520 - i * 15
        b.append(f'<path d="M{800 - r},880 A{r},{r} 0 0 1 {800 + r},880" '
                 f'fill="none" stroke="{c}" stroke-width="15" opacity="0.16"/>')

    # 左岸：一道台地，跳板从这里搭上船
    b.append('<path d="M0,742 L330,742 L306,796 L0,796 Z" fill="#0E2A55"/>')

    # 船体：右侧起翘成船首
    b.append('<path d="M400,684 L1150,684 C1210,684 1250,700 1276,722 '
             'L1112,790 Q760,822 452,786 Z" fill="url(#hull)"/>')
    b.append('<rect x="396" y="666" width="790" height="22" rx="9" fill="#C08A44"/>')
    # 舱房与屋顶
    b.append('<rect x="586" y="536" width="404" height="130" rx="10" fill="#C9903F"/>')
    b.append('<path d="M562,536 L1014,536 L952,466 L624,466 Z" fill="#8A5A2B"/>')
    for i in range(5):
        x = 618 + i * 74
        b.append(f'<rect x="{x}" y="568" width="48" height="48" rx="6" fill="#123C86"/>'
                 f'<rect x="{x + 6}" y="574" width="36" height="36" rx="4" '
                 f'fill="#FFE9A8" opacity="0.9"/>')
    # 桅杆与帆
    b.append('<rect x="782" y="330" width="12" height="136" fill="#5C3A18"/>')
    b.append('<path d="M794,340 L946,394 L794,448 Z" fill="#F2F5F9"/>')

    # 跳板与正在上船的人 —— 这条跳板才是这一页真正想说的事
    b.append('<path d="M296,752 L470,690 L482,712 L308,774 Z" fill="#C08A44"/>')
    for i, t in enumerate((0.12, 0.36, 0.60, 0.84)):
        x = 302 + 174 * t
        y = 758 - 62 * t
        b.append(f'<circle cx="{x:.0f}" cy="{y - 40:.0f}" r="12" fill="#0B2E6B"/>'
                 f'<rect x="{x - 11:.0f}" y="{y - 26:.0f}" width="22" height="34" rx="10" '
                 f'fill="#0B2E6B"/>')

    b.append(_wave(796, 14, 0.0, "#2E6BB8", 0.85, 2.0))
    b.append(_wave(826, 11, 2.1, "#1F5AA8", 0.9, 2.8))
    b.append(_wave(858, 9, 4.0, "#123C86", 1.0, 3.4))

    b.append('<text x="90" y="140" font-size="68" font-weight="bold" fill="#FFFFFF">'
             "所以要赶在浪到之前，先把船造好</text>")
    b.append('<text x="92" y="198" font-size="29" fill="#CFE0F7">'
             "院长实验班就是这条船 —— 今天这场会，是在请各位上船</text>")
    # 图上不再自带右下角那句话：PPT 会在底部压一条说明栏，两行字会叠在一起。
    # 图只负责画，话交给页面说。
    return _svg("".join(b), "#08183E", defs)


# ------------------------------------------- 三、未来 AI 教育 Agent 构想图
SATELLITES = [
    ("教师 Agent", "备课 · 出题 · 讲解", ACCENT),
    ("项目导师 Agent", "任务 · 验收 · 代码审查", OK),
    ("图书馆 Agent", "资料 · 文献 · 可信语料", VIOLET),
    ("学工 Agent", "预警 · 关怀 · 生涯", WARN),
    ("教务 Agent", "选课 · 学分 · 培养方案", "#0E7490"),
    ("就业 Agent", "岗位 · 作品集 · 推荐", "#B45309"),
    ("心理健康 Agent", "状态 · 干预 · 转介", "#BE185D"),
    ("家长 Agent", "知情 · 协同 · 不越界", MUTED),
]


def fig_agents() -> str:
    # 椭圆环而不是正圆：画布是 16:9，正圆排布会让顶部节点顶到标题、
    # 左右又空一大片。rx/ry 分开给，八个节点才铺得开。
    cx, cy, rx, ry, R = W / 2, 470, 476, 238, 88
    b = []
    b.append('<text x="80" y="72" font-size="44" font-weight="bold" fill="#1B1F24">'
             '未来的 AI 教育：每一个角色都有一个 Agent</text>')
    b.append(f'<text x="82" y="114" font-size="23" fill="{MUTED}">'
             '它们不是八个聊天窗口 —— 它们说话的依据，是同一份学习事件流</text>')

    pos = []
    for i in range(len(SATELLITES)):
        a = -math.pi / 2 + 2 * math.pi * i / len(SATELLITES)
        pos.append((cx + math.cos(a) * rx, cy + math.sin(a) * ry))

    # 先连线后画节点，节点才压在线上面
    for (x, y), (_n, _s, color) in zip(pos, SATELLITES):
        b.append(f'<line x1="{cx}" y1="{cy}" x2="{x:.0f}" y2="{y:.0f}" '
                 f'stroke="{color}" stroke-width="2.5" opacity="0.4"/>')

    for (x, y), (name, sub, color) in zip(pos, SATELLITES):
        b.append(f'<circle cx="{x:.0f}" cy="{y:.0f}" r="{R}" fill="#FFFFFF" '
                 f'stroke="{color}" stroke-width="3"/>')
        b.append(f'<circle cx="{x:.0f}" cy="{y:.0f}" r="{R}" fill="{color}" opacity="0.08"/>')
        b.append(f'<text x="{x:.0f}" y="{y - 6:.0f}" font-size="19" font-weight="bold" '
                 f'fill="{color}" text-anchor="middle">{name}</text>')
        b.append(f'<text x="{x:.0f}" y="{y + 22:.0f}" font-size="13" fill="{MUTED}" '
                 f'text-anchor="middle">{sub}</text>')

    b.append(f'<circle cx="{cx}" cy="{cy}" r="130" fill="{ACCENT}" opacity="0.10"/>')
    b.append(f'<circle cx="{cx}" cy="{cy}" r="106" fill="{ACCENT}"/>')
    b.append(f'<text x="{cx}" y="{cy - 12}" font-size="30" font-weight="bold" '
             f'fill="#FFFFFF" text-anchor="middle">学生 Agent</text>')
    b.append(f'<text x="{cx}" y="{cy + 22}" font-size="17" fill="#D6E6FF" '
             f'text-anchor="middle">他的状态、缺口</text>')
    b.append(f'<text x="{cx}" y="{cy + 46}" font-size="17" fill="#D6E6FF" '
             f'text-anchor="middle">与成长轨迹</text>')

    # 底座：所有 Agent 共享的那一层，也是这张图真正的主张
    by = 796
    b.append(f'<rect x="80" y="{by}" width="{W - 160}" height="76" rx="14" '
             f'fill="#F2F5F9" stroke="#D5DBE3"/>')
    b.append(f'<text x="{cx}" y="{by + 32}" font-size="22" font-weight="bold" fill="{INK}" '
             f'text-anchor="middle">'
             f'共同的底座：只追加的学习事件流 · 知识图谱 · 错误模式库</text>')
    b.append(f'<text x="{cx}" y="{by + 60}" font-size="17" fill="{MUTED}" '
             f'text-anchor="middle">'
             f'没有这一层，八个 Agent 就是八个各说各话的聊天机器人</text>')
    for x, y in pos:                     # 只给下半圈画虚线，免得糊成一团
        if y > cy:
            b.append(f'<line x1="{x:.0f}" y1="{y + R:.0f}" x2="{x:.0f}" y2="{by}" '
                     f'stroke="{MUTED}" stroke-width="1.5" stroke-dasharray="5 5" '
                     f'opacity="0.5"/>')
    return _svg("".join(b))


# ------------------------------------------- 四、报名二维码
GITEE = "https://gitee.com/ritchiezheng_admin/AI_EDU"


def signup_url() -> str:
    """报名页的对外地址。

    退化成 127.0.0.1 是刻意的**响亮失败**：那种码在会场上扫出来是打不开的，
    而一张打不开的码比没有码更糟——所以图上会把这件事直接印出来。
    """
    base = (CONFIG.public_base_url or f"http://{CONFIG.host}:{CONFIG.port}").rstrip("/")
    return f"{base}/signup.html"


def _qr_block(text: str, x: float, y: float, side: float, ec: str = "M") -> str:
    """把一段文本画成边长 side 的二维码，含 4 模块静区（静区不留，很多扫码器认不出）。"""
    m = qrcode.encode(text, ec)
    n = len(m) + 8
    mod = side / n
    body = f'<rect x="{x}" y="{y}" width="{side}" height="{side}" fill="#FFFFFF"/>'
    body += f'<g fill="{INK}">'
    body += qrcode.to_svg_paths(m, module=mod, origin=(x + 4 * mod, y + 4 * mod))
    body += "</g>"
    return body


def fig_qr() -> str:
    url = signup_url()
    local = not CONFIG.public_base_url
    b = []
    b.append(f'<rect width="{W}" height="{H}" fill="#F7F9FC"/>')
    b.append(f'<text x="{W/2}" y="96" font-size="46" font-weight="bold" fill="{INK}" '
             f'text-anchor="middle">扫码报名，或克隆仓库一起做</text>')
    b.append(f'<text x="{W/2}" y="142" font-size="22" fill="{MUTED}" text-anchor="middle">'
             f'三种参与方式：当项目导师 · 审知识点与映射 · 一起做开发</text>')

    side = 380
    for i, (title, text, color) in enumerate([
            ("报名页", url, ACCENT), ("代码仓库（Gitee）", GITEE, VIOLET)]):
        cx = W / 2 + (i * 2 - 1) * 380
        x, y = cx - side / 2, 210
        b.append(f'<rect x="{x - 22}" y="{y - 22}" width="{side + 44}" height="{side + 44}" '
                 f'rx="18" fill="#FFFFFF" stroke="{color}" stroke-width="3"/>')
        b.append(_qr_block(text, x, y, side))
        b.append(f'<text x="{cx}" y="{y + side + 82}" font-size="26" font-weight="bold" '
                 f'fill="{color}" text-anchor="middle">{title}</text>')
        b.append(f'<text x="{cx}" y="{y + side + 120}" font-size="17" fill="{MUTED}" '
                 f'text-anchor="middle">{text}</text>')

    if local:
        b.append(f'<rect x="240" y="{H-118}" width="{W-480}" height="66" rx="10" '
                 f'fill="{BAD}" opacity="0.10"/>')
        b.append(f'<text x="{W/2}" y="{H-76}" font-size="20" fill="{BAD}" '
                 f'text-anchor="middle">⚠ 配置里没有填 public_base_url，'
                 f'这张码只有本机能扫。部署后请在 config.yaml 填实际地址并重出。</text>')
    else:
        b.append(f'<text x="{W/2}" y="{H-70}" font-size="19" fill="{MUTED}" '
                 f'text-anchor="middle">'
                 f'不登录也能填，只要姓名和一种参与方式；名单只有教师身份能看。</text>')
    return _svg("".join(b), "#F7F9FC")


FIGURES = {
    "tsunami": ("ai-tsunami.png", fig_tsunami),
    "ark": ("noahs-ark.png", fig_ark),
    "agents": ("agent-constellation.png", fig_agents),
    "qr": ("signup-qr.png", fig_qr),
}


def render(key: str) -> Path:
    name, fn = FIGURES[key]
    OUT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as td:
        svg = Path(td) / f"{key}.svg"
        svg.write_text(fn(), encoding="utf-8")
        subprocess.run(
            ["libreoffice", "--headless", "--convert-to", "png",
             str(svg), "--outdir", td],
            check=True, capture_output=True, timeout=300)
        png = Path(td) / f"{key}.png"
        if not png.exists():
            raise SystemExit(f"转换失败：{key}")
        dst = OUT / name
        shutil.copy(png, dst)
    return dst


def main() -> None:
    keys = sys.argv[1:] or list(FIGURES)
    for k in keys:
        if k not in FIGURES:
            raise SystemExit(f"未知的图：{k}（可选 {'/'.join(FIGURES)}）")
        print(f"已生成 {render(k)}")


if __name__ == "__main__":
    main()
