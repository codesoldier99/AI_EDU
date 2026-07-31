"""事件流重算一致性 —— Phase 0 的核心验收标准。"""
from __future__ import annotations

import random

from base import DBTestCase

from packages.engagement import service as engagement
from packages.state import replay, repo, tracker


class TestReplay(DBTestCase):
    seed_course = True

    def _write_events(self, n: int = 60) -> None:
        rng = random.Random(7)
        codes = list(self.kp)
        for i in range(n):
            code = rng.choice(codes)
            tracker.record(
                self.student, rng.choice(["quiz", "homework", "practice"]),
                self.kp[code], rng.random() < 0.55, source="homework",
                occurred_at=f"2026-0{1 + i % 3}-{1 + i % 27:02d}T10:00:00",
            )

    def test_replay_matches_stored(self):
        self._write_events()
        engagement.refresh_streak(self.student)
        d = replay.verify(self.student)
        self.assertTrue(d.ok, msg=f"不一致：{d.mismatches[:3]}")
        e = engagement.verify_streak(self.student)
        self.assertTrue(e["match"], msg=str(e))

    def test_rebuild_is_idempotent(self):
        self._write_events()
        before = repo.mastery_vector(self.student)
        replay.rebuild(self.student)
        after = repo.mastery_vector(self.student)
        self.assertEqual(sorted(before), sorted(after))
        for k in before:
            self.assertAlmostEqual(before[k], after[k], places=6)

    def test_detects_unbacked_mastery(self):
        """无事件支撑的掌握度必须被重算校验揪出来。"""
        self._write_events(10)
        eid = repo.append_event(self.student, "note", None, None, "manual")
        repo.write_mastery(self.student, self.kp["C"], 0.99, 0.9, 1, eid)
        d = replay.verify(self.student)
        self.assertFalse(d.ok)
        self.assertTrue(any("无事件支撑" in m["reason"] or "不一致" in m["reason"]
                            for m in d.mismatches))

    def test_engagement_is_derivable(self):
        """engagement 是派生态：清掉后可从事件流完全重算。"""
        self._write_events(30)
        s1 = engagement.refresh_streak(self.student)
        self.db.execute("DELETE FROM streak_state")
        s2 = engagement.refresh_streak(self.student)
        self.assertEqual(s1, s2)
