"""学生报名的读写。所有 SQL 收在这里（开发规范：写操作走 repository 层）。"""
from __future__ import annotations

import re

from packages.core.db import get_db
from packages.core.timeutil import now_str

MAXLEN = {"name": 40, "phone": 20, "source": 40}
# 手机号允许中间夹空格/短横线（有人会习惯性分段输入），校验前先清洗掉，
# 但落库存清洗后的纯数字——名单是要真拿去发短信/打电话的，格式不统一没法用。
_PHONE_STRIP = re.compile(r"[\s\-()（）]")
_PHONE_SHAPE = re.compile(r"^1[3-9]\d{9}$")   # 中国大陆手机号：1[3-9] 开头共 11 位


class EnrollError(ValueError):
    """报名内容不合法。专门的异常类型，让路由层能翻成 400 而不是 500。"""


def _clean_name(value: object) -> str:
    s = str(value or "").strip()
    if not s:
        raise EnrollError("请填写姓名")
    if len(s) > MAXLEN["name"]:
        raise EnrollError(f"姓名超长（上限 {MAXLEN['name']} 字）")
    return s


def _clean_phone(value: object) -> str:
    s = _PHONE_STRIP.sub("", str(value or "").strip())
    if not s:
        raise EnrollError("请填写手机号")
    if not _PHONE_SHAPE.match(s):
        raise EnrollError("手机号格式不对，请填 11 位手机号")
    return s


def create(name: str, phone: str, source: str = "") -> int:
    name = _clean_name(name)
    phone = _clean_phone(phone)
    src = str(source or "").strip()[: MAXLEN["source"]]
    return get_db().execute(
        "INSERT INTO class_enroll(name, phone, source, created_at) VALUES(?,?,?,?)",
        (name, phone, src, now_str()),
    )


def list_all(limit: int = 500) -> list[dict]:
    return [dict(r) for r in get_db().query(
        "SELECT * FROM class_enroll ORDER BY id DESC LIMIT ?", (min(limit, 2000),))]


def stats() -> dict:
    """只给计数，不给名单——公开接口不能顺手把姓名手机号也吐出来。"""
    return {"total": get_db().scalar("SELECT COUNT(*) FROM class_enroll") or 0}
