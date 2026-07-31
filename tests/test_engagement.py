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
