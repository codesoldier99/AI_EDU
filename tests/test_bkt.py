"""BKT 与掌握度更新的性质测试。改 BKT 后必跑（make test-state）。"""
from __future__ import annotations

from base import DBTestCase

from packages.state.bkt import BKTParams, posterior, update, update_with_weight


class TestBKT(DBTestCase):
    def test_correct_raises_mastery(self):
        p = BKTParams.default()
        self.assertGreater(update(0.3, True, p), 0.3)

    def test_wrong_lowers_posterior(self):
        p = BKTParams.default()
        self.assertLess(posterior(0.6, False, p), 0.6)

    def test_bounded(self):
        p = BKTParams.default()
        v = 0.5
        for _ in range(200):
            v = update(v, True, p)
        self.assertLessEqual(v, 1.0)
        v = 0.5
        for _ in range(200):
            v = update(v, False, p)
        self.assertGreaterEqual(v, 0.0)

    def test_weight_interpolates(self):
        p = BKTParams.default()
        full = update(0.4, True, p)
        half = update_with_weight(0.4, True, p, 0.5)
        self.assertAlmostEqual(half, 0.4 + 0.5 * (full - 0.4), places=9)

    def test_zero_weight_is_noop(self):
        p = BKTParams.default()
        self.assertAlmostEqual(update_with_weight(0.4, True, p, 0.0), 0.4)

    def test_fit_requires_enough_samples(self):
        p = BKTParams.default()
        self.assertIs(p, __import__("packages.state.bkt", fromlist=["x"]).fit_params([True] * 10, p))


class TestTracker(DBTestCase):
    seed_course = True

    def test_mastery_requires_event(self):
        """铁律：禁止直接 UPDATE MasteryState 而不写 LearningEvent（DB 触发器兜底）。"""
        from packages.state import repo

        with self.assertRaises(Exception):
            repo.write_mastery(self.student, self.kp["A"], 0.9, 0.9, 1, None)

    def test_event_stream_is_append_only(self):
        from packages.state import repo

        eid = repo.append_event(self.student, "quiz", self.kp["A"], True, "quiz")
        with self.assertRaises(Exception):
            self.db.execute("UPDATE learning_event SET is_correct=0 WHERE id=?", (eid,))
        with self.assertRaises(Exception):
            self.db.execute("DELETE FROM learning_event WHERE id=?", (eid,))

    def test_record_updates_mastery(self):
        from packages.state import tracker

        r = tracker.record(self.student, "quiz", self.kp["A"], True, source="quiz")
        self.assertTrue(r.updated)
        self.assertGreater(r.p_after, r.p_before)
        self.assertEqual(r.evidence_count, 1)

    def test_behavior_event_does_not_update_mastery(self):
        """降级、里程碑这类行为事件不得改变掌握度。"""
        from packages.state import repo, tracker

        tracker.record(self.student, "escalation", self.kp["A"], None, source="ask")
        self.assertIsNone(repo.get_mastery(self.student, self.kp["A"]))

    def test_confidence_grows_with_evidence(self):
        from packages.state import tracker

        prev = 0.0
        for _ in range(5):
            r = tracker.record(self.student, "quiz", self.kp["A"], True, source="quiz")
            self.assertGreaterEqual(r.confidence, prev)
            prev = r.confidence
