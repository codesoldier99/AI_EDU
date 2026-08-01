"""掌握的质量：跨时间验证、遗忘复检、提示依赖、学法适配。

这一层对应 LearnVector 提出的两条主张，但把它们变成可判定的规则：
  "stays with you until you've mastered it **and can prove it**" → 跨时间验证
  "chatbots without guardrails harm learning"（认知外包）→ 提示依赖度可观测
"""
from __future__ import annotations

from base import DBTestCase

from packages.core.config import CONFIG
from packages.state import repo, verification
from packages.state import tracker


class TestValidation(DBTestCase):
    seed_course = True

    def _quiz(self, kp: str, ok: bool, date: str):
        tracker.record(self.student, "quiz", self.kp[kp], ok, source="quiz",
                       occurred_at=f"{date}T10:00:00")

    def test_same_day_repetition_is_not_validated(self):
        """当堂反复练到做对——证明的是短时记忆，不是掌握。"""
        for _ in range(6):
            self._quiz("A", True, "2026-03-01")
        v = verification.build(self.student)
        item = next(i for i in v.items if i["kp_id"] == self.kp["A"])
        self.assertGreaterEqual(item["p_mastery"], CONFIG.teaching.mastery_threshold)
        self.assertFalse(item["validated"], "同一天内做对多次不应算已验证")
        self.assertEqual(v.n_validated, 0)

    def test_spaced_repetition_is_validated(self):
        self._quiz("A", True, "2026-03-01")
        self._quiz("A", True, "2026-03-20")
        item = next(i for i in verification.build(self.student).items
                    if i["kp_id"] == self.kp["A"])
        self.assertTrue(item["validated"])
        self.assertGreaterEqual(item["validated_gap_days"], CONFIG.teaching.verify_gap_days)

    def test_gap_just_below_threshold_rejected(self):
        self._quiz("A", True, "2026-03-01")
        self._quiz("A", True, "2026-03-05")      # 只隔 4 天，低于 7 天门槛
        item = next(i for i in verification.build(self.student).items
                    if i["kp_id"] == self.kp["A"])
        self.assertFalse(item["validated"])

    def test_wrong_answers_do_not_count_toward_validation(self):
        self._quiz("A", True, "2026-03-01")
        self._quiz("A", False, "2026-03-20")
        item = next(i for i in verification.build(self.student).items
                    if i["kp_id"] == self.kp["A"])
        self.assertFalse(item["validated"])


class TestRetention(DBTestCase):
    seed_course = True

    def test_decay_is_monotonic_and_halves(self):
        hl = CONFIG.teaching.retention_halflife_days
        self.assertAlmostEqual(verification.decay_factor(0), 1.0)
        self.assertAlmostEqual(verification.decay_factor(hl), 0.5, places=6)
        self.assertGreater(verification.decay_factor(5), verification.decay_factor(50))

    def test_old_mastery_becomes_due(self):
        for _ in range(10):
            tracker.record(self.student, "quiz", self.kp["A"], True, source="exam",
                           occurred_at="2026-01-01T10:00:00")
        item = next(i for i in verification.build(self.student).items
                    if i["kp_id"] == self.kp["A"])
        self.assertGreaterEqual(item["p_mastery"], CONFIG.teaching.mastery_threshold)
        self.assertLess(item["retained"], item["p_mastery"])
        self.assertTrue(item["due"], "很久没碰的知识点应当进入复检队列")

    def test_decay_never_writes_back_to_mastery(self):
        """铁律：掌握度必须等于事件流折叠的结果。衰减只算不写，否则 replay 会对不上。"""
        for _ in range(8):
            tracker.record(self.student, "quiz", self.kp["A"], True, source="exam",
                           occurred_at="2026-01-01T10:00:00")
        before = repo.get_mastery(self.student, self.kp["A"])["p_mastery"]
        verification.build(self.student)
        verification.due_reviews(self.student)
        after = repo.get_mastery(self.student, self.kp["A"])["p_mastery"]
        self.assertEqual(before, after)
        from packages.state import replay

        self.assertTrue(replay.verify(self.student).ok)

    def test_due_reviews_ranked_by_drop_and_severity(self):
        for kp in ("A", "C"):
            for _ in range(10):
                tracker.record(self.student, "quiz", self.kp[kp], True, source="exam",
                               occurred_at="2026-01-01T10:00:00")
        due = verification.due_reviews(self.student)
        self.assertTrue(due)
        # A 是 C 的祖先，挡路度更高，应当排在前面
        self.assertEqual(due[0]["kp_id"], self.kp["A"])
        self.assertGreaterEqual(due[0]["priority"], due[-1]["priority"])


class TestIndependence(DBTestCase):
    seed_course = True

    def test_unaided_correct_counted(self):
        tracker.record(self.student, "quiz", self.kp["A"], True, source="quiz",
                       occurred_at="2026-03-01T10:00:00")
        ind = verification.independence(self.student)
        self.assertEqual(ind.n_correct, 1)
        self.assertEqual(ind.n_unaided_correct, 1)
        self.assertEqual(ind.unaided_rate, 1.0)

    def test_correct_after_escalation_is_not_unaided(self):
        tracker.record(self.student, "escalation", self.kp["A"], None, source="ask",
                       occurred_at="2026-03-01T09:00:00")
        tracker.record(self.student, "quiz", self.kp["A"], True, source="quiz",
                       occurred_at="2026-03-01T10:00:00")
        ind = verification.independence(self.student)
        self.assertEqual(ind.n_correct, 1)
        self.assertEqual(ind.n_unaided_correct, 0)
        self.assertEqual(ind.n_escalated_kps, 1)

    def test_correct_before_escalation_still_counts_as_unaided(self):
        tracker.record(self.student, "quiz", self.kp["A"], True, source="quiz",
                       occurred_at="2026-03-01T08:00:00")
        tracker.record(self.student, "escalation", self.kp["A"], None, source="ask",
                       occurred_at="2026-03-01T09:00:00")
        self.assertEqual(verification.independence(self.student).n_unaided_correct, 1)

    def test_metric_is_documented_as_non_incentive(self):
        """这条不是形式主义：指标一旦被当成目标，就会被优化，然后失去意义。"""
        note = verification.independence(self.student).note
        self.assertIn("禁止", note)
        self.assertIn("激励", note)


class TestStyleAdaptation(DBTestCase):
    seed_course = True

    def test_effectiveness_computed_from_events(self):
        from packages.agents.asking import AskingAgent

        agent = AskingAgent()
        out = agent.start(self.student, self.kp["C"])
        agent.reply(out.plan["session_id"],
                    "因为链式法则要逐层相乘，所以我先对最外层求导，然后代入中间变量得到结果")
        eff = verification.style_effectiveness(self.student)
        self.assertTrue(eff)
        self.assertTrue(any(v["tried"] > 0 for v in eff.values()))

    def test_no_preference_without_enough_evidence(self):
        """证据不足时不猜——这是全系统一以贯之的规矩。"""
        self.assertEqual(verification.preferred_styles(self.student), [])


class TestClassView(DBTestCase):
    seed_course = True

    def test_class_verification_aggregates(self):
        other = repo.upsert_student("S002", "同学乙", "2026级", "实验班A")
        for sid in (self.student, other):
            tracker.record(sid, "quiz", self.kp["A"], True, source="exam",
                           occurred_at="2026-03-01T10:00:00")
            tracker.record(sid, "quiz", self.kp["A"], True, source="exam",
                           occurred_at="2026-03-20T10:00:00")
        c = verification.class_verification(self.course_id, "实验班A")
        self.assertEqual(c["n_students"], 2)
        self.assertGreater(c["n_validated"], 0)
        self.assertIn("note", c)
