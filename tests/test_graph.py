"""图算法：环检测、拓扑序、根因回溯、挡路度、最近已掌握前置点。"""
from __future__ import annotations

from base import DBTestCase

from packages.graph import algo, repo


class TestGraph(DBTestCase):
    seed_course = True

    def test_no_cycles_in_seed(self):
        self.assertEqual(algo.detect_cycles(), [])

    def test_self_loop_rejected(self):
        with self.assertRaises(ValueError):
            repo.add_edge(self.kp["A"], self.kp["A"])

    def test_cycle_detected(self):
        repo.add_edge(self.kp["C"], self.kp["A"])
        self.assertTrue(algo.detect_cycles())

    def test_topological_order(self):
        order = algo.topological_sort([self.kp["C"], self.kp["A"], self.kp["B"]])
        self.assertLess(order.index(self.kp["A"]), order.index(self.kp["B"]))
        self.assertLess(order.index(self.kp["B"]), order.index(self.kp["C"]))

    def test_root_cause_traces_to_deepest_weak_ancestor(self):
        """反向传播出错 → 回溯到链式法则。"""
        mastery = {self.kp["A"]: 0.2, self.kp["B"]: 0.3, self.kp["C"]: 0.3}
        t = algo.trace_root_cause(self.kp["C"], mastery, 0.75)
        self.assertEqual(t["root_kp_id"], self.kp["A"])
        self.assertEqual(t["depth"], 2)

    def test_root_cause_stops_at_mastered_ancestor(self):
        mastery = {self.kp["A"]: 0.9, self.kp["B"]: 0.3, self.kp["C"]: 0.3}
        t = algo.trace_root_cause(self.kp["C"], mastery, 0.75)
        self.assertEqual(t["root_kp_id"], self.kp["B"])

    def test_blocking_severity_counts_downstream(self):
        self.assertGreater(
            algo.blocking_severity(self.kp["A"]), algo.blocking_severity(self.kp["C"])
        )

    def test_nearest_mastered_prerequisite(self):
        mastery = {self.kp["A"]: 0.9, self.kp["B"]: 0.2}
        self.assertEqual(
            algo.nearest_mastered_prerequisite(self.kp["C"], mastery, 0.75), self.kp["A"]
        )

    def test_nearest_returns_none_when_nothing_mastered(self):
        self.assertIsNone(
            algo.nearest_mastered_prerequisite(self.kp["C"], {}, 0.75)
        )
