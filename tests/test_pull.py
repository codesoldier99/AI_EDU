"""拉取式调度：缺口计算、只推 1–2 个、按挡路度排序、不按拓扑序排全员进度。"""
from __future__ import annotations

from base import DBTestCase

from packages.agents.task import compute_gap
from packages.core.config import CONFIG
from packages.graph import repo as g
from packages.state import tracker


class TestPull(DBTestCase):
    seed_course = True

    def setUp(self):
        super().setUp()
        pid = g.upsert_project("P1", "测试项目", "robot", "demo")
        self.task = g.upsert_task(pid, "T1", "训练一个网络")
        for code in ("A", "B", "C"):
            g.link_task_kp(self.task, self.kp[code], "required")
        g.link_task_kp(self.task, self.kp["D"], "helpful")

    def test_gap_excludes_mastered(self):
        for _ in range(12):
            tracker.record(self.student, "quiz", self.kp["A"], True, source="exam")
        gap = compute_gap(self.student, self.task)
        names = [x["kp_id"] for x in gap.gap]
        self.assertNotIn(self.kp["A"], names)
        self.assertEqual(gap.mastered, 1)

    def test_helpful_not_required_by_default(self):
        gap = compute_gap(self.student, self.task)
        self.assertEqual(gap.required_total, 3)
        self.assertNotIn(self.kp["D"], [x["kp_id"] for x in gap.gap])

    def test_push_limited(self):
        gap = compute_gap(self.student, self.task)
        self.assertLessEqual(len(gap.push), CONFIG.teaching.gap_push_limit)

    def test_push_prefers_unblocked_then_severity(self):
        """缺口内部没有前置卡点的优先——能立刻学的先学。"""
        gap = compute_gap(self.student, self.task)
        first = gap.push[0]
        self.assertEqual(first["kp_id"], self.kp["A"])
        self.assertEqual(first["prereq_gap"], [])

    def test_no_course_progress_percentage(self):
        """产品硬约束：不得出现课程进度百分比。"""
        from packages.agents.task import TaskAgent

        p = TaskAgent().project_progress(
            g.get_task(self.task).project_id, self.student
        )
        self.assertNotIn("course_progress", p)
        self.assertIn("task_progress", p)
