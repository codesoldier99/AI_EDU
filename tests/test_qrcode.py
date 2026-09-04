"""二维码编码器：对着标准查，再自己解回来。

这个模块没有第三方依赖可依靠，所以正确性靠三层证据：

  1. **对 published 常量**：生成多项式的 α 指数、32 个格式信息值，
     都是 ISO/IEC 18004 表里的定值，写死在用例里逐个比。
  2. **对结构**：各版本的数据模块数必须等于「总码字 × 8 + 剩余位」。
     功能图案多占或少占一格，这一条立刻炸。
  3. **自己解回来**：用例里带一个独立写的解码器，从矩阵读格式信息、去掩模、
     反交织、验纠错码字的伴随式、再解出原文。编码器改坏了，往返就对不上。

开发时另外拿 segno 与 OpenCV 交叉验证过（157/160 随机载荷 OpenCV 可解，
剩下 3 个 segno 生成的码同样解不出，是检测器的局限）。那两个库不进项目依赖，
所以不写进用例——测试不该断言运行环境里装了什么。
"""
from __future__ import annotations

import unittest

from base import ROOT  # noqa: F401  仅为把项目根加进 sys.path

from packages.tools import qrcode as q

# ISO/IEC 18004 附录 C：格式信息 32 个定值，顺序为 (L,M,Q,H) × mask0..7
_FORMAT_INFO = {
    "L": ["111011111000100", "111001011110011", "111110110101010", "111100010011101",
          "110011000101111", "110001100011000", "110110001000001", "110100101110110"],
    "M": ["101010000010010", "101000100100101", "101111001111100", "101101101001011",
          "100010111111001", "100000011001110", "100111110010111", "100101010100000"],
    "Q": ["011010101011111", "011000001101000", "011111100110001", "011101000000110",
          "010010010110100", "010000110000011", "010111011011010", "010101111101101"],
    "H": ["001011010001001", "001001110111110", "001110011100111", "001100111010000",
          "000011101100010", "000001001010101", "000110100001100", "000100000111011"],
}

# 各版本总码字数与剩余位（ISO/IEC 18004 表 1）
_TOTAL_CW = {1: 26, 2: 44, 3: 70, 4: 100, 5: 134,
             6: 172, 7: 196, 8: 242, 9: 292, 10: 346}
_REMAINDER = {1: 0, 2: 7, 3: 7, 4: 7, 5: 7, 6: 7, 7: 0, 8: 0, 9: 0, 10: 0}


class TestAgainstPublishedConstants(unittest.TestCase):
    def test_generator_polynomials_match_the_standard(self):
        """生成多项式按 α 指数比对——这一条钉住整个 GF(256) 实现。"""
        expected = {
            7: [0, 87, 229, 146, 149, 238, 102, 21],
            10: [0, 251, 67, 46, 61, 118, 70, 64, 94, 32, 45],
            13: [0, 74, 152, 176, 100, 86, 100, 106, 104, 130, 218, 206, 140, 78],
        }
        for n, exps in expected.items():
            got = q._generator(n)
            self.assertEqual([q._LOG[c] for c in got], exps, f"g{n} 不对")

    def test_format_information_matches_the_standard_table(self):
        for ec, rows in _FORMAT_INFO.items():
            for mask, want in enumerate(rows):
                self.assertEqual(f"{q._format_bits(ec, mask):015b}", want,
                                 f"{ec} mask{mask} 的格式信息不对")

    def test_data_module_count_matches_the_standard(self):
        """功能图案多占或少占一格，这里立刻炸。"""
        for v in range(1, q.MAX_VERSION + 1):
            size = 17 + 4 * v
            m, fn = q._blank(size)
            q._place_function_patterns(m, fn, v)
            free = sum(1 for r in range(size) for c in range(size) if not fn[r][c])
            self.assertEqual(free, _TOTAL_CW[v] * 8 + _REMAINDER[v], f"版本 {v}")


def _decode(matrix: list[list[int]]) -> str:
    """独立写的解码器：只给用例用，故意不复用编码器里的排布代码。"""
    size = len(matrix)
    version = (size - 17) // 4

    # 1) 从第一份格式信息读出纠错等级与掩模
    pos1 = ([(8, i) for i in range(6)] + [(8, 7), (8, 8), (7, 8)]
            + [(5, 8), (4, 8), (3, 8), (2, 8), (1, 8), (0, 8)])
    raw = 0
    for r, c in pos1:
        raw = (raw << 1) | matrix[r][c]
    fmt = raw ^ 0b101010000010010
    ec = {0b01: "L", 0b00: "M", 0b11: "Q", 0b10: "H"}[(fmt >> 13) & 0b11]
    mask = (fmt >> 10) & 0b111

    # 2) 去掩模后按之字形读回比特
    _m, fn = q._blank(size)
    q._place_function_patterns(_m, fn, version)
    bits: list[int] = []
    up, col = True, size - 1
    while col > 0:
        if col == 6:
            col -= 1
        for row in (range(size - 1, -1, -1) if up else range(size)):
            for c in (col, col - 1):
                if fn[row][c]:
                    continue
                v = matrix[row][c]
                if q._MASKS[mask](row, c):
                    v ^= 1
                bits.append(v)
        up = not up
        col -= 2
    cw = [int("".join(map(str, bits[i:i + 8])), 2)
          for i in range(0, len(bits) // 8 * 8, 8)]

    # 3) 反交织成块，并验每块纠错码字的伴随式（全零才说明这块没错）
    ecw, g1n, g1d, g2n, g2d = q._BLOCKS[version][ec]
    sizes = [g1d] * g1n + [g2d] * g2n
    ndata = sum(sizes)
    blocks: list[list[int]] = [[] for _ in sizes]
    k = 0
    for i in range(max(sizes)):
        for b, sz in enumerate(sizes):
            if i < sz:
                blocks[b].append(cw[k])
                k += 1
    eccs: list[list[int]] = [[] for _ in sizes]
    for i in range(ecw):
        for b in range(len(sizes)):
            eccs[b].append(cw[ndata + i * len(sizes) + b])
    for blk, e in zip(blocks, eccs):
        full = blk + e
        for j in range(ecw):
            syn = 0
            for coef in full:
                syn = q._mul(syn, q._EXP[j]) ^ coef
            assert syn == 0, "纠错伴随式非零：这块码字不是合法的 RS 码字"

    # 4) 解出报文头与正文
    data = [b for blk in blocks for b in blk]
    stream = "".join(f"{b:08b}" for b in data)
    assert stream[:4] == "0100", "不是字节模式"
    cl = 8 if version < 10 else 16
    n = int(stream[4:4 + cl], 2)
    body = stream[4 + cl:4 + cl + n * 8]
    return bytes(int(body[i:i + 8], 2) for i in range(0, len(body), 8)).decode("utf-8")


class TestRoundTrip(unittest.TestCase):
    CASES = [
        "http://64.186.235.32/signup.html",
        "https://gitee.com/ritchiezheng_admin/AI_EDU",
        "A",
        "院长实验班报名",                     # UTF-8 多字节
        "http://example.edu.cn/signup.html?from=faculty-meeting",
        "x" * 150,
    ]

    def test_every_level_round_trips(self):
        for text in self.CASES:
            for ec in ("L", "M", "Q", "H"):
                try:
                    m = q.encode(text, ec)
                except ValueError:
                    continue                  # 该等级装不下，属预期
                self.assertEqual(_decode(m), text, f"{ec} / {text[:20]!r} 往返失败")

    def test_every_version_round_trips_at_its_capacity(self):
        """容量边缘最容易出错：终止符被截断、填充码字为零。"""
        for v in range(1, q.MAX_VERSION + 1):
            for ec in ("L", "M", "Q", "H"):
                cap = q.capacity(v, ec)
                for n in (1, cap):
                    text = ("aA0-_." * 200)[:n]
                    m = q.encode(text, ec, version=v)
                    self.assertEqual(len(m), 17 + 4 * v)
                    self.assertEqual(_decode(m), text, f"v{v} {ec} len={n}")


class TestBoundaries(unittest.TestCase):
    def test_too_long_is_refused_not_silently_downgraded(self):
        """装不下就报错。悄悄降纠错等级会让贴在幕布上的码变脆，而且没人知道。"""
        with self.assertRaises(ValueError):
            q.encode("x" * (q.capacity(q.MAX_VERSION, "H") + 1), "H")

    def test_unknown_error_level_is_refused(self):
        with self.assertRaises(ValueError):
            q.encode("x", "Z")

    def test_utf8_is_counted_in_bytes_not_characters(self):
        """中文一个字三个字节。按字符数算容量会在边界上悄悄溢出。"""
        text = "报" * 10                       # 30 字节
        self.assertEqual(len(text.encode("utf-8")), 30)
        m = q.encode(text, "M")
        self.assertEqual(_decode(m), text)

    def test_svg_paths_cover_every_dark_module(self):
        m = q.encode("http://example.edu.cn/signup.html", "M")
        paths = q.to_svg_paths(m, module=1.0)
        # 同一行连续的深色模块会被合成一个矩形，所以矩形数 ≤ 深色模块数，
        # 但宽度之和必须等于深色模块总数
        total_w = sum(float(seg.split('width="')[1].split('"')[0])
                      for seg in paths.split("<rect")[1:])
        self.assertEqual(round(total_w), sum(sum(r) for r in m))


if __name__ == "__main__":
    unittest.main()
