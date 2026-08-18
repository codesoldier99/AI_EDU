"""安全算术求值器：判分与解题校验的确定性底座。

为什么要自己写一个：DeepTutor 那类系统把"这道题算得对不对"交给模型，
而生成式模型不适合精确计算（可汗学院被迫外挂计算器，见 DEVELOPMENT_PLAN §〇）。
本模块用 AST 白名单求值，同一个表达式永远得到同一个结果，且可被教师复算。

只支持：数字、四则运算、幂、一元正负、括号、白名单函数与常量。
任何名称、属性、下标、调用之外的节点一律拒绝——不是"过滤危险字符"，
而是**只允许已知安全的节点类型**（黑名单会漏，白名单不会）。
"""
from __future__ import annotations

import ast
import math
import re
from dataclasses import dataclass

# 白名单函数：只放纯数值、无副作用的
FUNCS = {
    "abs": abs, "round": round, "min": min, "max": max,
    "sqrt": math.sqrt, "exp": math.exp, "log": math.log, "log2": math.log2,
    "log10": math.log10, "sin": math.sin, "cos": math.cos, "tan": math.tan,
    "asin": math.asin, "acos": math.acos, "atan": math.atan, "atan2": math.atan2,
    "floor": math.floor, "ceil": math.ceil, "pow": math.pow, "fabs": math.fabs,
    "degrees": math.degrees, "radians": math.radians,
}
CONSTS = {"pi": math.pi, "e": math.e, "tau": math.tau, "inf": math.inf}

_BIN = {
    ast.Add: lambda a, b: a + b,
    ast.Sub: lambda a, b: a - b,
    ast.Mult: lambda a, b: a * b,
    ast.Div: lambda a, b: a / b,
    ast.FloorDiv: lambda a, b: a // b,
    ast.Mod: lambda a, b: a % b,
    ast.Pow: lambda a, b: a ** b,
}

# 指数上限：挡住 9**9**9 这种把 CPU 打满的写法
MAX_POW = 1e6


class CalcError(ValueError):
    """表达式不合法或含不被允许的成分。"""


def _eval(node: ast.AST) -> float:
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise CalcError("只接受数字常量")
        return float(node.value)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        v = _eval(node.operand)
        return v if isinstance(node.op, ast.UAdd) else -v
    if isinstance(node, ast.BinOp):
        fn = _BIN.get(type(node.op))
        if fn is None:
            raise CalcError(f"不支持的运算符：{type(node.op).__name__}")
        a, b = _eval(node.left), _eval(node.right)
        if isinstance(node.op, ast.Pow) and (abs(b) > 64 or abs(a) ** abs(b) > MAX_POW * MAX_POW):
            raise CalcError("指数过大")
        return float(fn(a, b))
    if isinstance(node, ast.Name):
        if node.id in CONSTS:
            return float(CONSTS[node.id])
        raise CalcError(f"未知名称：{node.id}")
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in FUNCS:
            raise CalcError("只允许调用白名单函数")
        if node.keywords:
            raise CalcError("不支持关键字参数")
        return float(FUNCS[node.func.id](*[_eval(a) for a in node.args]))
    raise CalcError(f"不允许的语法节点：{type(node).__name__}")


def safe_eval(expr: str) -> float:
    """求值一个算术表达式。不合法时抛 CalcError，绝不 eval 原文。"""
    text = (expr or "").strip()
    if not text or len(text) > 200:
        raise CalcError("表达式为空或过长")
    text = _preprocess(text)
    try:
        tree = ast.parse(text, mode="eval")
    except SyntaxError as exc:  # noqa: PERF203
        raise CalcError(f"无法解析：{exc.msg}") from exc
    v = _eval(tree)
    if math.isnan(v) or math.isinf(v):
        raise CalcError("结果不是有限数")
    return v


def _preprocess(text: str) -> str:
    """把学生常写的几种记法归一成 Python 表达式。"""
    text = text.replace("×", "*").replace("÷", "/").replace("^", "**")
    text = text.replace("（", "(").replace("）", ")").replace("，", ",")
    text = re.sub(r"(?<=\d),(?=\d{3}(?!\d))", "", text)   # 千分位；函数实参的逗号要留着
    text = re.sub(r"(?<=\d)[\s_]+(?=\d)", "", text)       # 数字内部的空白；词间空白保留，
    text = text.strip()                                   # 否则 "1 if 1" 会被粘成非法字面量
    # 百分数：25% -> (25/100)
    text = re.sub(r"(\d+(?:\.\d+)?)%", r"(\1/100)", text)
    return text


NUM_RE = re.compile(r"[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?")


def parse_number(text: str) -> float | None:
    """从一段自然语言作答里取出**唯一**的数值答案。

    刻意要求唯一：若一句话里出现多个数字，说明学生给的是过程而不是结论，
    此时判分应当交给人，而不是猜他想说哪一个（铁律 4：不确定必须显式表达）。
    """
    t = (text or "").strip()
    if not t:
        return None
    # "64*32=2048"：写了等号就说明结论在最后一个等号右边。
    # 这是一条明确的、可复算的约定，不是猜——学生把过程写出来不该被判成"说不清"。
    if "=" in t or "＝" in t:
        tail = re.split(r"[=＝]", t)[-1].strip()
        if tail:
            t = tail
    try:
        return safe_eval(t)
    except CalcError:
        pass
    nums = NUM_RE.findall(_preprocess(t))
    if len(nums) != 1:
        return None
    try:
        return float(nums[0])
    except ValueError:
        return None


@dataclass
class NumericCheck:
    ok: bool = False
    got: float | None = None
    expected: float | None = None
    rel_error: float | None = None
    note: str = ""


def numeric_equal(got: float, expected: float, tolerance: float = 0.0) -> bool:
    """相对误差比较；expected 为 0 时退化为绝对误差。"""
    tol = max(float(tolerance or 0.0), 1e-9)
    if expected == 0:
        return abs(got) <= tol
    return abs(got - expected) / abs(expected) <= tol


def check_numeric(response: str, expected: str, tolerance: float = 0.0) -> NumericCheck:
    """数值题的确定性判定。判不了就说判不了，不猜。"""
    # 标准答案两种写法都收：纯表达式（题库里的 numeric 题），
    # 以及"权重参数为 2048 个"这类带话的结论（分步解题的每步结论是给人看的）。
    exp = parse_number(expected)
    if exp is None:
        return NumericCheck(note="标准答案里取不到唯一数值，该步无法自动判定")
    got = parse_number(response)
    if got is None:
        return NumericCheck(expected=exp, note="作答中未找到唯一数值，需人工判定")
    rel = None if exp == 0 else abs(got - exp) / abs(exp)
    return NumericCheck(
        ok=numeric_equal(got, exp, tolerance),
        got=got, expected=exp,
        rel_error=None if rel is None else round(rel, 6),
    )
