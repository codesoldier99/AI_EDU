"""追问引擎：不直接给答案、降级必须留痕、降级由状态触发而非模型自由发挥。"""
from __future__ import annotations

from base import DBTestCase

from packages.agents.asking import AskingAgent, AskingStrategy
from packages.core.config import CONFIG
from packages.state import repo, tracker


class TestAsking(DBTestCase):
    seed_course = True

    def test_entry_is_nearest_mastered_prerequisite(self):
        for _ in range(12):
            tracker.record(self.student, "quiz", self.kp["A"], True, source="exam")
        plan = AskingStrategy().decide(self.student, self.kp["C"])
        self.assertEqual(plan.entry_kp_id, self.kp["A"])

    def test_depth_grows_as_mastery_drops(self):
        low = AskingStrategy().decide(self.student, self.kp["C"])
        for _ in range(12):
            tracker.record(self.student, "quiz", self.kp["C"], True, source="exam")
        high = AskingStrategy().decide(self.student, self.kp["C"])
        self.assertGreater(low.depth, high.depth)

    def test_no_answer_before_escalation(self):
        out = AskingAgent().start(self.student, self.kp["C"])
        self.assertEqual(out.plan["escalation_level"], 0)
        self.assertFalse(out.plan["gives_answer"])

    def test_escalation_is_logged_as_event(self):
        agent = AskingAgent()
        out = agent.start(self.student, self.kp["C"])
        sid = out.plan["session_id"]
        for _ in range(CONFIG.teaching.escalation_attempts + 1):
            out = agent.reply(sid, "不会")
        self.assertGreater(out.plan["escalation_level"], 0)
        events = repo.list_events(student_id=self.student, event_type="escalation")
        self.assertTrue(events)
        self.assertEqual(events[0]["kp_id"], self.kp["C"])
        self.assertIn("level", events[0]["payload"])

    def test_escalation_event_itself_carries_no_verdict(self):
        """降级事件是行为记录，不是对错判定：它不得携带 is_correct，也不得成为掌握度来源。

        （学生那句"不会"本身是有效证据，由 ask_turn 事件以 0.5 权重记录，这是另一回事。）
        """
        agent = AskingAgent()
        out = agent.start(self.student, self.kp["C"])
        sid = out.plan["session_id"]
        for _ in range(CONFIG.teaching.escalation_attempts + 1):
            agent.reply(sid, "不会")
        esc = repo.list_events(student_id=self.student, event_type="escalation")
        self.assertTrue(esc)
        self.assertTrue(all(e["is_correct"] is None for e in esc))
        m = repo.get_mastery(self.student, self.kp["C"])
        if m:
            src = repo.get_event(m["last_event_id"])
            self.assertNotEqual(src["event_type"], "escalation")

    def test_style_rotates(self):
        agent = AskingAgent()
        out = agent.start(self.student, self.kp["C"])
        first = out.plan["style"]
        out2 = agent.reply(out.plan["session_id"], "我觉得是因为链式法则里少乘了一项")
        self.assertNotEqual(first, out2.plan["style"])

    def test_progress_detection_is_conservative(self):
        self.assertIsNone(AskingAgent._detect_progress("嗯"))
        self.assertFalse(AskingAgent._detect_progress("不知道"))
        self.assertTrue(AskingAgent._detect_progress(
            "因为链式法则要逐层相乘，所以我先对最外层求导，然后代入中间变量得到结果"))
