"""二维码编码器（字节模式，零依赖，纯确定性）。

为什么自己写：`packages/tools` 是确定性工具箱，谁都能用它、它谁都不用；
汇报要在会场上让老师扫码报名，而演示环境不保证有外网、也不该为一张图
引进一个第三方库。同样的理由已经用在 three.js 本地 vendor 上。

实现范围刻意收窄：**只做字节模式、版本 1–10**。一个报名链接三十来个字符，
版本 3 就装得下；把版本 11–40 和数字/字母模式一并实现，只是增加没人走的分支。
装不下就抛错，不悄悄降级——降级会静默改变纠错等级。

输出是一个 bool 矩阵（True = 深色模块），怎么画交给调用方：
scripts/make_figures.py 画成 SVG，将来要画成别的也不用改这里。

正确性由 tests/test_qrcode.py 钉住：生成多项式与格式信息对published 值，
整幅矩阵对 segno 逐模块比对（segno 只在开发机的临时环境里当基准，不进依赖）。
"""
from __future__ import annotations

# ---------------------------------------------------------------- GF(256)
# QR 用的本原多项式是 0x11D。log/antilog 表一次算好，后面的乘除都是查表。
_EXP = [0] * 512
_LOG = [0] * 256
_x = 1
for _i in range(255):
    _EXP[_i] = _x
    _LOG[_x] = _i
    _x <<= 1
    if _x & 0x100:
        _x ^= 0x11D
for _i in range(255, 512):
    _EXP[_i] = _EXP[_i - 255]


def _mul(a: int, b: int) -> int:
    if a == 0 or b == 0:
        return 0
    return _EXP[_LOG[a] + _LOG[b]]


def _generator(n: int) -> list[int]:
    """生成多项式 g(x) = ∏(x − α^i)，i = 0..n−1，系数按降幂排列。"""
    g = [1]
    for i in range(n):
        g.append(0)
        for j in range(len(g) - 1, 0, -1):
            g[j] ^= _mul(g[j - 1], _EXP[i])
    return g


def _ecc(data: list[int], n: int) -> list[int]:
    """对一块数据码字算 n 个纠错码字（多项式带余除法）。"""
    gen = _generator(n)
    rem = list(data) + [0] * n
    for i in range(len(data)):
        coef = rem[i]
        if coef:
            for j, g in enumerate(gen):
                rem[i + j] ^= _mul(g, coef)
    return rem[len(data):]


# ---------------------------------------------------------------- 版本表
# (纠错码字/块, 组1块数, 组1数据码字, 组2块数, 组2数据码字)
_BLOCKS: dict[int, dict[str, tuple[int, int, int, int, int]]] = {
    1:  {"L": (7, 1, 19, 0, 0),   "M": (10, 1, 16, 0, 0),
         "Q": (13, 1, 13, 0, 0),  "H": (17, 1, 9, 0, 0)},
    2:  {"L": (10, 1, 34, 0, 0),  "M": (16, 1, 28, 0, 0),
         "Q": (22, 1, 22, 0, 0),  "H": (28, 1, 16, 0, 0)},
    3:  {"L": (15, 1, 55, 0, 0),  "M": (26, 1, 44, 0, 0),
         "Q": (18, 2, 17, 0, 0),  "H": (22, 2, 13, 0, 0)},
    4:  {"L": (20, 1, 80, 0, 0),  "M": (18, 2, 32, 0, 0),
         "Q": (26, 2, 24, 0, 0),  "H": (16, 4, 9, 0, 0)},
    5:  {"L": (26, 1, 108, 0, 0), "M": (24, 2, 43, 0, 0),
         "Q": (18, 2, 15, 2, 16), "H": (22, 2, 11, 2, 12)},
    6:  {"L": (18, 2, 68, 0, 0),  "M": (16, 4, 27, 0, 0),
         "Q": (24, 4, 19, 0, 0),  "H": (28, 4, 15, 0, 0)},
    7:  {"L": (20, 2, 78, 0, 0),  "M": (18, 4, 31, 0, 0),
         "Q": (18, 2, 14, 4, 15), "H": (26, 4, 13, 1, 14)},
    8:  {"L": (24, 2, 97, 0, 0),  "M": (22, 2, 38, 2, 39),
         "Q": (22, 4, 18, 2, 19), "H": (26, 4, 14, 2, 15)},
    9:  {"L": (30, 2, 116, 0, 0), "M": (22, 3, 36, 2, 37),
         "Q": (20, 4, 16, 4, 17), "H": (24, 4, 12, 4, 13)},
    10: {"L": (18, 2, 68, 2, 69), "M": (26, 4, 43, 1, 44),
         "Q": (24, 6, 19, 2, 20), "H": (28, 6, 15, 2, 16)},
}

# 定位图案中心坐标（版本 1 没有）
_ALIGN = {1: [], 2: [6, 18], 3: [6, 22], 4: [6, 26], 5: [6, 30], 6: [6, 34],
          7: [6, 22, 38], 8: [6, 24, 42], 9: [6, 26, 46], 10: [6, 28, 50]}

_EC_BITS = {"L": 0b01, "M": 0b00, "Q": 0b11, "H": 0b10}
MAX_VERSION = 10


def capacity(version: int, ec: str) -> int:
    """该版本 / 纠错等级下，字节模式能装多少字节。"""
    ecw, g1n, g1d, g2n, g2d = _BLOCKS[version][ec]
    total_data = g1n * g1d + g2n * g2d
    # 模式指示符 4 bit + 长度指示符（版本 1–9 为 8 bit，10 起为 16 bit）
    header = 4 + (8 if version < 10 else 16)
    return (total_data * 8 - header) // 8


def _pick_version(nbytes: int, ec: str) -> int:
    for v in range(1, MAX_VERSION + 1):
        if nbytes <= capacity(v, ec):
            return v
    raise ValueError(
        f"内容太长：{nbytes} 字节，纠错等级 {ec} 下版本 1–{MAX_VERSION} 最多装 "
        f"{capacity(MAX_VERSION, ec)} 字节。请缩短链接，而不是降低纠错等级——"
        "会场上贴在幕布上的码，纠错等级不能省。")


# ---------------------------------------------------------------- 数据编码
def _encode_data(payload: bytes, version: int, ec: str) -> list[int]:
    ecw, g1n, g1d, g2n, g2d = _BLOCKS[version][ec]
    total_data = g1n * g1d + g2n * g2d

    bits: list[int] = []

    def put(value: int, length: int) -> None:
        for i in range(length - 1, -1, -1):
            bits.append((value >> i) & 1)

    put(0b0100, 4)                                   # 字节模式
    put(len(payload), 8 if version < 10 else 16)
    for byte in payload:
        put(byte, 8)

    # 结束符最多 4 个 0，装不下就少放几个
    put(0, min(4, total_data * 8 - len(bits)))
    bits.extend([0] * (-len(bits) % 8))              # 补齐到字节边界

    codewords = [int("".join(str(b) for b in bits[i:i + 8]), 2)
                 for i in range(0, len(bits), 8)]
    # 填充码字在 0xEC / 0x11 之间交替，直到填满
    pad, k = (0xEC, 0x11), 0
    while len(codewords) < total_data:
        codewords.append(pad[k % 2])
        k += 1

    # 分块 → 各自算纠错 → 交织
    blocks: list[list[int]] = []
    pos = 0
    for count, size in ((g1n, g1d), (g2n, g2d)):
        for _ in range(count):
            blocks.append(codewords[pos:pos + size])
            pos += size
    eccs = [_ecc(b, ecw) for b in blocks]

    out: list[int] = []
    for i in range(max(len(b) for b in blocks)):
        for b in blocks:
            if i < len(b):
                out.append(b[i])
    for i in range(ecw):
        for e in eccs:
            out.append(e[i])
    return out


# ---------------------------------------------------------------- 矩阵
def _blank(size: int) -> tuple[list[list[int]], list[list[bool]]]:
    """返回 (模块矩阵, 是否为功能图案)。功能图案不参与数据填充，也不被掩模翻转。"""
    return ([[0] * size for _ in range(size)],
            [[False] * size for _ in range(size)])


def _place_function_patterns(m: list[list[int]], fn: list[list[bool]],
                             version: int) -> None:
    size = len(m)

    def finder(r0: int, c0: int) -> None:
        for dr in range(-1, 8):
            for dc in range(-1, 8):
                r, c = r0 + dr, c0 + dc
                if not (0 <= r < size and 0 <= c < size):
                    continue
                inner = 2 <= dr <= 4 and 2 <= dc <= 4
                ring = 0 <= dr <= 6 and 0 <= dc <= 6 and (
                    dr in (0, 6) or dc in (0, 6))
                m[r][c] = 1 if (inner or ring) else 0
                fn[r][c] = True

    finder(0, 0)
    finder(0, size - 7)
    finder(size - 7, 0)

    for i in range(8, size - 8):                      # 定时图案
        bit = 1 if i % 2 == 0 else 0
        m[6][i] = bit; fn[6][i] = True
        m[i][6] = bit; fn[i][6] = True

    for r in _ALIGN[version]:                         # 校正图案
        for c in _ALIGN[version]:
            if (r < 9 and c < 9) or (r < 9 and c > size - 10) \
                    or (r > size - 10 and c < 9):
                continue
            for dr in range(-2, 3):
                for dc in range(-2, 3):
                    m[r + dr][c + dc] = 1 if (
                        max(abs(dr), abs(dc)) != 1) else 0
                    fn[r + dr][c + dc] = True

    m[size - 8][8] = 1; fn[size - 8][8] = True        # 固定的深色模块

    for i in range(9):                                # 格式信息保留区
        if not fn[8][i]:
            fn[8][i] = True
        if not fn[i][8]:
            fn[i][8] = True
    for i in range(8):
        fn[8][size - 1 - i] = True
        fn[size - 1 - i][8] = True

    if version >= 7:                                  # 版本信息保留区
        for i in range(6):
            for j in range(3):
                fn[size - 11 + j][i] = True
                fn[i][size - 11 + j] = True


def _place_data(m: list[list[int]], fn: list[list[bool]], data: list[int]) -> None:
    """按之字形从右下角往上填：两列一组，右列先于左列。"""
    size = len(m)
    bits = [(byte >> i) & 1 for byte in data for i in range(7, -1, -1)]
    idx = 0
    up = True
    col = size - 1
    while col > 0:
        if col == 6:            # 第 6 列是定时图案，整列跳过
            col -= 1
        rows = range(size - 1, -1, -1) if up else range(size)
        for row in rows:
            for c in (col, col - 1):
                if fn[row][c]:
                    continue
                m[row][c] = bits[idx] if idx < len(bits) else 0
                idx += 1
        up = not up
        col -= 2


_MASKS = [
    lambda r, c: (r + c) % 2 == 0,
    lambda r, c: r % 2 == 0,
    lambda r, c: c % 3 == 0,
    lambda r, c: (r + c) % 3 == 0,
    lambda r, c: (r // 2 + c // 3) % 2 == 0,
    lambda r, c: (r * c) % 2 + (r * c) % 3 == 0,
    lambda r, c: ((r * c) % 2 + (r * c) % 3) % 2 == 0,
    lambda r, c: ((r + c) % 2 + (r * c) % 3) % 2 == 0,
]


def _penalty(m: list[list[int]]) -> int:
    """ISO/IEC 18004 表 11 的四条罚分规则。掩模选罚分最低的那一个。

    N3 是最容易写错的一条：要找的是 1:1:3:1:1 的深浅比例（1011101），
    且其前或后有 4 个模块宽的浅色区。图案贴在符号边缘时，静区就是那片浅色，
    同样计分——只用 "10111010000" 这样的定长子串去匹配会漏掉边缘的情形，
    进而选错掩模。选错掩模生成的码依然"看起来是个二维码"，只是更难扫。
    """
    size = len(m)
    pattern = bytes((1, 0, 1, 1, 1, 0, 1))
    n1 = n2 = n3 = 0

    def n3_line(seq: bytes) -> int:
        count, i = 0, 0
        while True:
            idx = seq.find(pattern, i)
            if idx < 0:
                return count
            after = idx + 7
            if (idx in (0, size - 7)
                    or not any(seq[max(idx - 4, 0):idx])
                    or not any(seq[after:after + 4])):
                count += 40
                i = after
            else:
                # 前后都没有足够的浅色区：从图案内部下一个可能的起点接着找
                i = idx + 4

    cols = [bytes(m[r][c] for r in range(size)) for c in range(size)]
    for line in [bytes(row) for row in m] + cols:
        run, prev = 1, line[0]
        for v in line[1:]:
            if v == prev:
                run += 1
            else:
                if run >= 5:
                    n1 += run - 2          # 等价于 3 + (run - 5)
                run, prev = 1, v
        if run >= 5:
            n1 += run - 2
        n3 += n3_line(line)

    for r in range(size - 1):              # N2：每个 2×2 同色块记 3 分
        row, nxt = m[r], m[r + 1]
        for c in range(size - 1):
            if row[c] == row[c + 1] == nxt[c] == nxt[c + 1]:
                n2 += 3

    dark = sum(sum(row) for row in m)      # N4：深色模块占比偏离 50% 的程度
    n4 = 10 * int(abs(dark * 100 / (size * size) - 50) / 5)
    return n1 + n2 + n3 + n4


def _bch_remainder(value: int, gen: int) -> int:
    """多项式带余除法（GF(2) 上），用于格式信息与版本信息的 BCH 纠错位。"""
    gbits = gen.bit_length()
    while value.bit_length() >= gbits:
        value ^= gen << (value.bit_length() - gbits)
    return value


def _format_bits(ec: str, mask: int) -> int:
    """格式信息：5 bit 数据 + BCH(15,5) 纠错位，整体异或 0x5412。

    那个异或掩码不是可选项：不异或的话，(L, mask 0) 会得到全 0，
    而全 0 的格式信息在有污损时无法与"读到一片空白"区分开。
    """
    data = (_EC_BITS[ec] << 3) | mask
    rem = _bch_remainder(data << 10, 0b10100110111)
    return ((data << 10) | rem) ^ 0b101010000010010


def _version_bits(version: int) -> int:
    """版本信息：6 bit 版本号 + BCH(18,6)，生成多项式 0x1F25。版本 7 起才有。"""
    rem = _bch_remainder(version << 12, 0b1111100100101)
    return (version << 12) | rem


def _place_format(m: list[list[int]], ec: str, mask: int) -> None:
    """两份格式信息，**高位在前**沿各自的位置序列摆放。

    位序是这里最容易错的一处：15 位串按 MSB→LSB 依次落到下面的坐标上，
    而不是按 bit0→bit14。两份的坐标序列也不一样——第二份先走右下那 7 个，
    再跳到右上那 8 个，中间的 (size-8, 8) 是固定的深色模块，不属于格式信息。
    写反了照样能生成一张"看起来对"的码，只是扫不出来。
    """
    size = len(m)
    bits = _format_bits(ec, mask)
    seq = [(bits >> (14 - i)) & 1 for i in range(15)]

    pos1 = ([(8, i) for i in range(6)] + [(8, 7), (8, 8), (7, 8)]
            + [(5, 8), (4, 8), (3, 8), (2, 8), (1, 8), (0, 8)])
    pos2 = ([(size - 1 - i, 8) for i in range(7)]
            + [(8, size - 8 + i) for i in range(8)])
    for pos in (pos1, pos2):
        for (r, c), b in zip(pos, seq):
            m[r][c] = b


def _place_version(m: list[list[int]], version: int) -> None:
    """版本信息（版本 7 起）：18 位，**低位在前**，左下与右上各一份。

    注意它与格式信息的位序相反——这不是笔误，标准就是这么定的。
    """
    if version < 7:
        return
    size = len(m)
    bits = _version_bits(version)
    for i in range(18):
        b = (bits >> i) & 1
        m[i // 3][size - 11 + i % 3] = b
        m[size - 11 + i % 3][i // 3] = b


def encode(text: str, ec: str = "M", version: int | None = None) -> list[list[int]]:
    """把一段文本编成二维码矩阵。返回 size×size 的 0/1 二维列表（1 = 深色）。"""
    if ec not in _EC_BITS:
        raise ValueError(f"纠错等级只能是 L/M/Q/H，收到 {ec!r}")
    payload = text.encode("utf-8")
    version = version or _pick_version(len(payload), ec)
    if not 1 <= version <= MAX_VERSION:
        raise ValueError(f"只支持版本 1–{MAX_VERSION}，收到 {version}")
    if len(payload) > capacity(version, ec):
        raise ValueError(f"版本 {version} / 等级 {ec} 装不下 {len(payload)} 字节")

    data = _encode_data(payload, version, ec)
    size = 17 + 4 * version

    best = None
    for mask in range(8):
        m, fn = _blank(size)
        _place_function_patterns(m, fn, version)
        _place_data(m, fn, data)
        for r in range(size):
            for c in range(size):
                if not fn[r][c] and _MASKS[mask](r, c):
                    m[r][c] ^= 1
        _place_format(m, ec, mask)
        _place_version(m, version)
        score = _penalty(m)
        if best is None or score < best[0]:
            best = (score, m)
    return best[1]


def to_svg_paths(matrix: list[list[int]], module: float = 1.0,
                 origin: tuple[float, float] = (0.0, 0.0)) -> str:
    """把矩阵摊成一串 SVG <rect>。

    刻意不返回整幅 <svg>：调用方要把它放进更大的画布里，
    自己控制留白（quiet zone）与配色。
    """
    ox, oy = origin
    out = []
    for r, row in enumerate(matrix):
        c = 0
        while c < len(row):
            if not row[c]:
                c += 1
                continue
            run = 1                       # 同一行连续的深色模块合成一个矩形
            while c + run < len(row) and row[c + run]:
                run += 1
            out.append(f'<rect x="{ox + c * module:.3f}" y="{oy + r * module:.3f}" '
                       f'width="{run * module:.3f}" height="{module:.3f}"/>')
            c += run
    return "".join(out)
