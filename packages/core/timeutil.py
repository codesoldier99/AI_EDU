"""时间工具。全库统一使用 UTC ISO8601 字符串，保证 SQLite/PG 行为一致。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

ISO = "%Y-%m-%dT%H:%M:%S"


def now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def now_str() -> str:
    return now().strftime(ISO)


def to_str(dt: datetime) -> str:
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc)
    return dt.replace(microsecond=0).strftime(ISO)


def parse(s: str | None) -> datetime | None:
    if not s:
        return None
    s = s.strip().replace("Z", "").replace(" ", "T")
    for fmt in (ISO, "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%d"):
        try:
            return datetime.strptime(s[: len(fmt) + 6], fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def days_between(a: str | datetime | None, b: str | datetime | None) -> float:
    da = parse(a) if isinstance(a, str) else a
    db = parse(b) if isinstance(b, str) else b
    if not da or not db:
        return 0.0
    return abs((db - da).total_seconds()) / 86400.0


def date_of(s: str | None) -> str:
    d = parse(s)
    return d.strftime("%Y-%m-%d") if d else ""


def shift(dt: datetime, **kw) -> datetime:
    return dt + timedelta(**kw)
