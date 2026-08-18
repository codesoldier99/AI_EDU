"""参与度：连续天数、破关、回归、里程碑。

铁律：本包**只读事件流，不回写 L2 状态**（禁止 import tracker / write_mastery）。
所有派生态都可由事件流重算，tests/test_layering.py 会静态检查这一点。

设计范式（CLAUDE.md §3.2）：只对"坚持"给正反馈。
合法激励对象：连续天数 / 破关 / 回归 / 里程碑 / 真机首跑。
明令禁止：正确率、做题数量、停留时长、排名。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta

from packages.core.config import CONFIG
from packages.core.db import get_db
from packages.core.models import Message
from packages.core.timeutil import date_of, now_str, parse
from packages.state import repo as state_repo

# 允许的激励类型；此集合之外的一律不得进入 achievement 表
ALLOWED_KINDS = {"breakthrough", "milestone", "comeback", "first_run", "streak"}
# 明令禁止的激励对象，仅作自检用
FORBIDDEN_TARGETS = {"accuracy", "item_count", "dwell_time", "rank"}


@dataclass
class EngagementView(Message):
    student_id: int = 0
    current_days: int = 0
    longest_days: int = 0
    total_active_days: int = 0
    last_active_date: str = ""
    achievements: list = field(default_factory=list)
    recent_active_dates: list = field(default_factory=list)
    weekly_active: int = 0


# ---------------- streak ----------------
def active_dates(student_id: int) -> list[str]:
    rows = state_repo.list_events(student_id=student_id)
    return sorted({date_of(r["occurred_at"]) for r in rows if r["occurred_at"]})


def compute_streak(student_id: int, today: str | None = None) -> dict:
    """纯函数：由事件流算连续活跃天数，不落库。"""
    dates = active_dates(student_id)
    if not dates:
        return {"current_days": 0, "longest_days": 0, "last_active_date": "",
                "total_active_days": 0}
    longest = cur = 1
    for i in range(1, len(dates)):
        d0, d1 = parse(dates[i - 1]), parse(dates[i])
        if d0 and d1 and (d1 - d0).days == 1:
            cur += 1
        else:
            cur = 1
        longest = max(longest, cur)
    # 当前连续：从最后一个活跃日往回数
    cur_streak = 1
    for i in range(len(dates) - 1, 0, -1):
        d0, d1 = parse(dates[i - 1]), parse(dates[i])
        if d0 and d1 and (d1 - d0).days == 1:
            cur_streak += 1
        else:
            break
    ref = parse(today or date_of(now_str()))
    last = parse(dates[-1])
    if ref and last and (ref - last).days > 1:
        cur_streak = 0  # 已中断
    return {
        "current_days": cur_streak,
        "longest_days": longest,
        "last_active_date": dates[-1],
        "total_active_days": len(dates),
    }


def get_streak(student_id: int) -> dict:
    r = get_db().query_one("SELECT * FROM streak_state WHERE student_id=?", (student_id,))
    if not r:
        return {"current_days": 0, "longest_days": 0, "last_active_date": "",
                "total_active_days": 0}
    return dict(r)


def refresh_streak(student_id: int, today: str | None = None) -> dict:
    s = compute_streak(student_id, today)
    get_db().execute(
        "INSERT INTO streak_state(student_id, current_days, longest_days, last_active_date,"
        " total_active_days) VALUES(?,?,?,?,?) ON CONFLICT(student_id) DO UPDATE SET"
        " current_days=excluded.current_days, longest_days=excluded.longest_days,"
        " last_active_date=excluded.last_active_date,"
        " total_active_days=excluded.total_active_days",
        (student_id, s["current_days"], s["longest_days"], s["last_active_date"],
         s["total_active_days"]),
    )
    return s


# 只有这三个字段是事件流的纯函数；current_days 还依赖"今天是几号"。
REPLAYABLE_FIELDS = ("longest_days", "total_active_days", "last_active_date")


def verify_streak(student_id: int) -> dict:
    """重算校验：库中派生态是否与事件流一致。

    **current_days 不参与一致性判定**，这不是放水，是它压根不该参与：
    连续天数是 (事件流, 今天) 的函数，昨天算出来的 1 到今天本就该变成 0。
    把它算作"不一致"会让 `make replay` 对每个几天没活动的学生都报警——
    而一个天天喊狼来了的完整性检查，等于没有检查。

    库里那一行是缓存（任何一次 view() 都会 refresh），过期是正常的；
    真正必须严丝合缝的是不随时间变化的那三个字段。
    """
    stored, replay = get_streak(student_id), compute_streak(student_id)
    match = all(stored.get(k) == replay.get(k) for k in REPLAYABLE_FIELDS)
    return {
        "match": match,
        "stored": stored,
        "replay": replay,
        # 缓存是否已过期：仅供观测，不算违规
        "stale_current_days": stored.get("current_days") != replay.get("current_days"),
        "note": "current_days 依赖当前日期，属缓存；一致性只校验不随时间变化的字段",
    }


# ---------------- achievements ----------------
def _award(student_id: int, kind: str, ref_id: str, detail: str, earned_at: str) -> bool:
    if kind not in ALLOWED_KINDS:
        raise ValueError(f"禁止的激励类型：{kind}（只奖励坚持，不奖励正确率/题量/时长）")
    db = get_db()
    dup = db.query_one(
        "SELECT id FROM achievement WHERE student_id=? AND kind=? AND ref_id=?",
        (student_id, kind, ref_id),
    )
    if dup:
        return False
    db.execute(
        "INSERT INTO achievement(student_id, kind, ref_id, detail, earned_at) VALUES(?,?,?,?,?)",
        (student_id, kind, ref_id, detail, earned_at),
    )
    return True


def scan_achievements(student_id: int) -> list[dict]:
    """扫描事件流，补记应得的成就。幂等：重复执行不会重复发放。

    - comeback：中断 >= comeback_gap_days 后重新活跃（防流失最关键的一枪）
    - first_run：真机/系统首次跑通事件
    - milestone：项目里程碑事件
    - breakthrough：由 mark_breakthrough 在根因点被攻克时调用
    """
    cfg = CONFIG.teaching
    events = state_repo.list_events(student_id=student_id)
    new: list[dict] = []
    dates = active_dates(student_id)
    for i in range(1, len(dates)):
        d0, d1 = parse(dates[i - 1]), parse(dates[i])
        if d0 and d1 and (d1 - d0).days >= cfg.comeback_gap_days:
            if _award(student_id, "comeback", dates[i],
                      f"中断 {(d1 - d0).days} 天后回归，欢迎回来", dates[i] + "T00:00:00"):
                new.append({"kind": "comeback", "ref_id": dates[i]})
    for ev in events:
        if ev["event_type"] == "first_run":
            ref = ev["source_ref"] or str(ev["id"])
            if _award(student_id, "first_run", ref,
                      ev["payload"].get("detail", "真机首次跑通"), ev["occurred_at"]):
                new.append({"kind": "first_run", "ref_id": ref})
        elif ev["event_type"] == "milestone":
            ref = ev["source_ref"] or str(ev["id"])
            if _award(student_id, "milestone", ref,
                      ev["payload"].get("detail", "项目里程碑达成"), ev["occurred_at"]):
                new.append({"kind": "milestone", "ref_id": ref})
    s = refresh_streak(student_id)
    for days in (3, 7, 14, 30):
        if s["longest_days"] >= days:
            if _award(student_id, "streak", f"d{days}", f"连续活跃 {days} 天", now_str()):
                new.append({"kind": "streak", "ref_id": f"d{days}"})
    return new


def mark_breakthrough(student_id: int, kp_id: int, kp_name: str, unblocked: int,
                      earned_at: str | None = None) -> bool:
    """破关：攻克一个根因知识点。提示必须与根因绑定。"""
    detail = f"你攻克了「{kp_name}」"
    if unblocked:
        detail += f"，这是此前 {unblocked} 个卡点的共同原因"
    return _award(student_id, "breakthrough", f"kp:{kp_id}", detail,
                  earned_at or now_str())


def list_achievements(student_id: int, limit: int = 50) -> list[dict]:
    return get_db().query(
        "SELECT * FROM achievement WHERE student_id=? ORDER BY earned_at DESC LIMIT ?",
        (student_id, limit),
    )


def view(student_id: int) -> EngagementView:
    s = refresh_streak(student_id)
    dates = active_dates(student_id)
    ref = parse(date_of(now_str()))
    weekly = 0
    if ref:
        weekly = sum(
            1 for d in dates if (p := parse(d)) and 0 <= (ref - p).days < 7
        )
    return EngagementView(
        student_id=student_id,
        current_days=s["current_days"],
        longest_days=s["longest_days"],
        total_active_days=s["total_active_days"],
        last_active_date=s["last_active_date"],
        achievements=list_achievements(student_id, 20),
        recent_active_dates=dates[-30:],
        weekly_active=weekly,
    )


# ---------------- 班级层面的行为观测（Phase 1 前移的核心指标） ----------------
def class_engagement(klass: str | None = None, days: int = 7) -> dict:
    """主动使用率：Khanmigo 的教训要求第一阶段就观测行为，而不是等系统建完。"""
    students = state_repo.list_students(klass)
    ref = parse(date_of(now_str()))
    active = 0
    rows = []
    for st in students:
        dates = active_dates(st["id"])
        recent = [d for d in dates if (p := parse(d)) and ref and 0 <= (ref - p).days < days]
        if recent:
            active += 1
        s = compute_streak(st["id"])
        rows.append(
            {
                "student_id": st["id"],
                "sid": st["sid"],
                "name": st["name"],
                "active_days": len(recent),
                "current_streak": s["current_days"],
                "last_active": s["last_active_date"],
            }
        )
    rows.sort(key=lambda r: (r["active_days"], r["current_streak"]))
    n = len(students)
    return {
        "window_days": days,
        "n_students": n,
        "n_active": active,
        "active_rate": round(active / n, 4) if n else 0.0,
        "at_risk": [r for r in rows if r["active_days"] == 0][:10],
        "rows": rows,
    }
