"""参与度：只奖励坚持，禁止奖励正确率/题量/时长/排名。"""
from __future__ import annotations

from base import DBTestCase

from packages.engagement import service as eng
from packages.state import tracker


class TestEngagement(DBTestCase):
    seed_course = True

    def _day(self, d: str, correct=True):
        tracker.record(self.student, "quiz", self.kp["A"], correct, source="quiz",
                       occurred_at=f"{d}T10:00:00")

    def test_streak_counts_consecutive_days(self):
        for d in ("2026-03-01", "2026-03-02", "2026-03-03"):
            self._day(d)
        s = eng.compute_streak(self.student, today="2026-03-03")
        self.assertEqual(s["current_days"], 3)
        self.assertEqual(s["longest_days"], 3)

    def test_streak_breaks_on_gap(self):
        for d in ("2026-03-01", "2026-03-02", "2026-03-06"):
            self._day(d)
        s = eng.compute_streak(self.student, today="2026-03-06")
        self.assertEqual(s["current_days"], 1)
        self.assertEqual(s["longest_days"], 2)

    def test_comeback_awarded_after_break(self):
        for d in ("2026-03-01", "2026-03-08"):
            self._day(d)
        eng.scan_achievements(self.student)
        kinds = {a["kind"] for a in eng.list_achievements(self.student)}
        self.assertIn("comeback", kinds)

    def test_forbidden_award_kind_rejected(self):
        with self.assertRaises(ValueError):
            eng._award(self.student, "accuracy", "x", "正确率 90%", "2026-03-01T00:00:00")

    def test_breakthrough_binds_to_root_cause(self):
        ok = eng.mark_breakthrough(self.student, self.kp["A"], "链式法则", 3)
        self.assertTrue(ok)
        a = eng.list_achievements(self.student)[0]
        self.assertIn("链式法则", a["detail"])
        self.assertIn("3", a["detail"])

    def test_awards_are_idempotent(self):
        eng.mark_breakthrough(self.student, self.kp["A"], "链式法则", 1)
        eng.mark_breakthrough(self.student, self.kp["A"], "链式法则", 1)
        self.assertEqual(len(eng.list_achievements(self.student)), 1)

    def test_class_engagement_has_no_ranking(self):
        self._day("2026-03-01")
        c = eng.class_engagement("实验班A", days=3650)
        self.assertIn("active_rate", c)
        self.assertNotIn("rank", c)
        self.assertNotIn("leaderboard", c)


class TestStreakIntegrityCheck(DBTestCase):
    """`make replay` 的完整性校验不能对着时间函数喊狼来了。"""

    seed_course = True

    def test_stale_current_days_is_not_a_violation(self):
        from packages.engagement import service as eng

        # 学生在很久以前活跃过一次：写库时连续天数是 1，今天早就该归零
        tracker.record(self.student, "quiz", self.kp["A"], True, source="exam",
                       occurred_at="2026-01-01 09:00:00")
        eng.refresh_streak(self.student, today="2026-01-01")
        self.assertEqual(eng.get_streak(self.student)["current_days"], 1)

        v = eng.verify_streak(self.student)
        self.assertTrue(v["match"], "连续天数过期不应被判为事件流不一致")
        self.assertTrue(v["stale_current_days"])          # 但要如实报告缓存已过期
        self.assertEqual(v["replay"]["current_days"], 0)  # 重算的确是 0

    def test_real_corruption_is_still_caught(self):
        """真正的篡改还是要抓出来——放宽的只是那一个时间相关字段。"""
        from packages.engagement import service as eng

        tracker.record(self.student, "quiz", self.kp["A"], True, source="exam")
        eng.refresh_streak(self.student)
        self.db.execute("UPDATE streak_state SET total_active_days=999 WHERE student_id=?",
                        (self.student,))
        self.assertFalse(eng.verify_streak(self.student)["match"])
