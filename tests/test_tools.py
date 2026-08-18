"""确定性工具箱：算术求值与落地性检查。

这两件事必须是确定的，因为它们是"算不算对"和"这段有没有编"的最终依据。
"""
from __future__ import annotations

import unittest

from base import ROOT  # noqa: F401  仅为把项目根加进 sys.path

from packages.tools import calc, ground


class TestCalc(unittest.TestCase):
    def test_basic_arithmetic(self):
        self.assertAlmostEqual(calc.safe_eval("2+3*4"), 14)
        self.assertAlmostEqual(calc.safe_eval("(1+2)^2"), 9)      # ^ 归一成 **
        self.assertAlmostEqual(calc.safe_eval("sqrt(16)"), 4)
        self.assertAlmostEqual(calc.safe_eval("25%"), 0.25)
        self.assertAlmostEqual(calc.safe_eval("1,024*2"), 2048)   # 千分位
        self.assertAlmostEqual(calc.safe_eval("max(1,2)"), 2)     # 实参逗号要留着

    def test_rejects_anything_that_is_not_arithmetic(self):
        for bad in ("__import__('os')", "open('x')", "a+1", "[1,2]", "1 if 1 else 2",
                    "lambda: 1", "9**9**9"):
            with self.assertRaises(calc.CalcError, msg=bad):
                calc.safe_eval(bad)

    def test_parse_number_refuses_to_guess(self):
        self.assertAlmostEqual(calc.parse_number("答案是 3.5"), 3.5)
        # 一句话里出现多个数字 = 给的是过程不是结论，不猜
        self.assertIsNone(calc.parse_number("先算 2 再算 3 得到 6 吧"))
        self.assertIsNone(calc.parse_number("不知道"))

    def test_conclusion_after_the_last_equals_sign(self):
        """写了等号就按"结论在等号右边"读——把过程写出来不该被判成"说不清"。"""
        self.assertAlmostEqual(calc.parse_number("64*32=2048"), 2048)
        self.assertAlmostEqual(calc.parse_number("参数量 = 64*32 + 32 = 2080"), 2080)
        self.assertIsNone(calc.parse_number("答案 = 大概两千多"))

    def test_check_numeric_reports_pending_instead_of_wrong(self):
        r = calc.check_numeric("完全不会", "3.14")
        self.assertFalse(r.ok)
        self.assertIsNone(r.got)          # 判不了
        self.assertIn("人工", r.note)

    def test_expected_may_be_prose(self):
        """分步解题的每步结论是写给人看的（"权重参数为 2048 个"），也要能判。"""
        r = calc.check_numeric("64*32=2048", "权重参数为 2048 个。")
        self.assertTrue(r.ok)
        self.assertAlmostEqual(r.expected, 2048)
        # 标准答案里根本取不到数 -> 判不了，不判错
        self.assertIsNone(calc.check_numeric("2048", "答案见上文").got)

    def test_tolerance(self):
        self.assertTrue(calc.check_numeric("3.14", "pi", tolerance=0.01).ok)
        self.assertFalse(calc.check_numeric("3.0", "pi", tolerance=0.01).ok)


class TestGround(unittest.TestCase):
    material = "反向传播依赖链式法则，逐层把导数乘回去，最常见的错误是漏掉激活函数的导数。"

    def test_quoting_material_is_grounded(self):
        g = ground.check("反向传播依赖链式法则，逐层把导数乘回去。", self.material)
        self.assertTrue(g.grounded)
        self.assertGreater(g.ratio, 0.8)

    def test_off_material_text_is_flagged(self):
        g = ground.check("量子退火在组合优化里通常优于模拟退火。", self.material)
        self.assertFalse(g.grounded)

    def test_empty_text_is_not_grounded(self):
        self.assertEqual(ground.overlap_ratio("", self.material), 0.0)


if __name__ == "__main__":
    unittest.main()
