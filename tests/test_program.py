"""培养方案：归属移交、悬空需求队列、学分认定。

这一层最容易出的两类错都在这里钉住：
  1. 移交归属时把证据弄丢（事件流是最贵的东西，动它就等于动纵向研究基线）
  2. 图谱只建了三个点就敢下"覆盖 33%、可认定学分"的结论（假精度）
"""
from __future__ import annotations

import unittest

from base import DBTestCase

from packages.graph import repo as g
from packages.state import coverage, tracker


class TestProgramModel(DBTestCase):
    def _program(self):
        pid = g.upsert_program("P-TEST", "测试培养方案", "专升本", "实验班")
        c1 = g.upsert_course("C-MATH", "高等数学", 4.5, "1")
        c2 = g.upsert_course("C-ML", "机器学习", 3.0, "2")
        g.link_program_course(pid, c1, "学科平台", 1, 4.5, 72, "考试", 0)
        g.link_program_course(pid, c2, "专业核心", 2, 3.0, 48, "考试", 1)
        g.set_course_prefix("CALC", c1)
        g.set_course_prefix("ML", c2)
        return pid, c1, c2

    def test_course_can_appear_in_program_with_its_own_semester(self):
        pid, c1, _ = self._program()
        rows = g.program_courses("P-TEST")
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["semester"], 1)
        self.assertEqual(g.program_credit_total(pid), 7.5)

    def test_courses_with_no_kps_are_normal_not_missing(self):
        """绝大多数课程知识点数是 0 —— 这是拉取式建设的正常状态。"""
        self._program()
        rows = g.program_courses("P-TEST")
        self.assertTrue(all(r["kps"] == 0 for r in rows))

    def test_prefix_resolves_longest_match(self):
        """CD-ML 该归「机器学习课程设计」，不该被 ML 前缀抢走。"""
        pid, _c1, _c2 = self._program()
        cd = g.upsert_course("C-CD-ML", "机器学习课程设计", 1.0, "2")
        g.link_program_course(pid, cd, "集中实践", 2, 1.0, 0, "考查", 2)
        g.set_course_prefix("CD-ML", cd)
        self.assertEqual(g.course_for_code("CD-ML-01")["code"], "C-CD-ML")
        self.assertEqual(g.course_for_code("ML-07")["code"], "C-ML")


class TestCourseMerge(DBTestCase):
    """课程代码合并：换正式代码不该毁掉教师已有的产出。

    服务器上真踩过：ML 底下挂着一份教学大纲，直接 DELETE 撞外键——
    撞对了。那份大纲不该跟着旧代码一起消失。
    """

    def test_merge_moves_syllabus_instead_of_deleting(self):
        old_id = g.upsert_course("ML", "机器学习原理与应用", 4.0, "3")
        new_id = g.upsert_course("G18Z21022", "机器学习", 3.0, "2")
        self.db.execute(
            "INSERT INTO syllabus(course_id, version, status, content_json, created_by,"
            " created_at) VALUES(?,?,?,?,?,?)",
            (old_id, 1, "teacher_confirmed", "{}", "T001", "2026-08-01T00:00:00"))

        r = g.merge_course("ML", "G18Z21022")
        self.assertTrue(r["merged"])
        self.assertEqual(r["moved"].get("syllabus"), 1, "教学大纲没有被迁移")
        self.assertIsNone(g.get_course("ML"))
        self.assertEqual(
            self.db.scalar("SELECT COUNT(*) FROM syllabus WHERE course_id=?", (new_id,)), 1)

    def test_merge_refuses_while_kps_remain(self):
        """知识点还没移交完就合并，等于把它们连根删掉。"""
        old_id = g.upsert_course("ML", "机器学习原理与应用", 4.0, "3")
        g.upsert_course("G18Z21022", "机器学习", 3.0, "2")
        g.upsert_kp(old_id, "ML-01-01", "还没移交的知识点")
        r = g.merge_course("ML", "G18Z21022")
        self.assertFalse(r["merged"])
        self.assertEqual(r["kps_left"], 1)
        self.assertIsNotNone(g.get_course("ML"), "拒绝合并后源课程不该消失")


class TestReattributionKeepsEvidence(DBTestCase):
    """归属移交只改 course_id，不动知识点 id —— 因此证据一条不少。

    这条是整件事的安全底线：ML-02-* 的代码被选拔考卷引用着，
    库里还挂着几百条学习事件。改归属可以，改身份不行。
    """

    seed_course = True

    def test_moving_a_kp_to_another_course_preserves_events(self):
        kp_id = self.kp["A"]
        tracker.record(self.student, "quiz", kp_id, True, source="quiz")
        tracker.record(self.student, "quiz", kp_id, True, source="quiz")
        before = self.db.scalar("SELECT COUNT(*) FROM learning_event WHERE kp_id=?", (kp_id,))
        self.assertGreater(before, 0)

        old = g.get_kp(kp_id)
        new_course = g.upsert_course("C-LA", "线性代数A", 3.0, "1")
        moved = g.upsert_kp(new_course, old.code, old.name, old.description,
                            "atomic", old.kp_type, old.difficulty, "线性代数A · 矩阵")

        self.assertEqual(moved, kp_id, "移交不该产生新的知识点 id")
        self.assertEqual(g.get_kp(kp_id).course_id, new_course)
        self.assertEqual(
            self.db.scalar("SELECT COUNT(*) FROM learning_event WHERE kp_id=?", (kp_id,)),
            before, "移交归属把学习事件弄丢了")
        self.assertEqual(g.get_kp_by_code(old.code).id, kp_id, "知识点代码变了")


class TestDemandQueue(DBTestCase):
    """悬空需求队列：拉取式图谱建设的核心机制。"""

    def setUp(self):
        super().setUp()
        cid = g.upsert_course("C-PROB", "概率论与数理统计A", 3.5, "2")
        g.set_course_prefix("PROB", cid)

    def test_demand_is_recorded_with_inferred_course(self):
        self.assertTrue(g.add_demand("PROB-ERR", "task:T-DAC-3-5", "PRJ-DAC"))
        q = g.demand_queue()
        self.assertEqual(len(q), 1)
        self.assertEqual(q[0]["code"], "PROB-ERR")
        self.assertEqual(q[0]["course_code"], "C-PROB", "没有按前缀归到概率论名下")

    def test_same_source_counted_once(self):
        g.add_demand("PROB-ERR", "task:T-DAC-3-5", "PRJ-DAC")
        self.assertFalse(g.add_demand("PROB-ERR", "task:T-DAC-3-5", "PRJ-DAC"))
        self.assertEqual(g.demand_queue()[0]["demands"], 1)

    def test_ranked_by_how_many_places_need_it(self):
        """被 3 处需要的知识点排在被 1 处需要的前面 —— 这就是建设优先级。"""
        for src in ("task:A", "task:B", "task:C"):
            g.add_demand("PROB-ERR", src, "PRJ-DAC")
        g.add_demand("PROB-CI", "task:A", "PRJ-DAC")
        q = g.demand_queue()
        self.assertEqual(q[0]["code"], "PROB-ERR")
        self.assertEqual(q[0]["demands"], 3)

    def test_demand_closes_itself_once_the_kp_is_built(self):
        """建出来之后队列自己变短，不需要人去清。"""
        g.add_demand("PROB-ERR", "task:T-DAC-3-5", "PRJ-DAC")
        self.assertEqual(len(g.demand_queue()), 1)

        cid = g.get_course("C-PROB")["id"]
        g.upsert_kp(cid, "PROB-ERR", "测量误差的正态性", "能说明重复测量误差为何近似正态")
        g.close_built_demands()

        self.assertEqual(g.demand_queue(), [], "知识点已建出来，需求却还挂在队列上")

    def test_unknown_prefix_is_kept_not_dropped(self):
        """前缀不认识也要登记 —— 丢掉需求比归错类更糟。"""
        g.add_demand("WHATEVER-01", "task:X", "PRJ-DAC")
        q = g.demand_queue()
        self.assertEqual(len(q), 1)
        self.assertEqual(q[0]["course_code"], "")


class TestCreditRecognition(DBTestCase):
    """学分认定：两道闸，且拿不准时明说"判不了"。"""

    def _course_with(self, n_kps: int, code: str = "C-X") -> int:
        pid = g.upsert_program("P-TEST", "测试培养方案")
        cid = g.upsert_course(code, "某课程", 3.0, "1")
        g.link_program_course(pid, cid, "专业核心", 1, 3.0, 48, "考试", 0)
        for i in range(n_kps):
            g.upsert_kp(cid, f"{code}-{i:02d}", f"知识点{i}")
        return cid

    def test_thin_graph_yields_no_verdict_not_a_pass(self):
        """图谱只建了 3 个点时说"覆盖 33%"是假精度 —— 该判不了，不该给 ✓。"""
        self._course_with(3)
        s = self.db.execute(
            "INSERT INTO student(sid,name,cohort,klass,enrolled_at)"
            " VALUES('S1','甲','2026','实验班A','2026-08-01T00:00:00')")
        rows = coverage.program_coverage("P-TEST", s)
        row = rows[0]
        self.assertFalse(row.credit_eligible)
        self.assertIn("判不了", row.caveat)

    def test_empty_graph_says_so_explicitly(self):
        self._course_with(0)
        s = self.db.execute(
            "INSERT INTO student(sid,name,cohort,klass,enrolled_at)"
            " VALUES('S2','乙','2026','实验班A','2026-08-01T00:00:00')")
        row = coverage.program_coverage("P-TEST", s)[0]
        self.assertEqual(row.kps_built, 0)
        self.assertFalse(row.credit_eligible)
        self.assertIn("尚未建设", row.caveat)

    def test_coverage_counts_only_owned_kps(self):
        """只算这门课**拥有**的知识点。一松，同一个点会在三门课里各算一次。"""
        pid = g.upsert_program("P2", "方案二")
        c_la = g.upsert_course("C-LA2", "线性代数", 3.0, "1")
        c_ml = g.upsert_course("C-ML2", "机器学习", 3.0, "2")
        g.link_program_course(pid, c_la, "学科平台", 1, 3.0, 48, "考试", 0)
        g.link_program_course(pid, c_ml, "专业核心", 2, 3.0, 48, "考试", 1)
        g.upsert_kp(c_la, "LA2-01", "矩阵运算")
        s = self.db.execute(
            "INSERT INTO student(sid,name,cohort,klass,enrolled_at)"
            " VALUES('S3','丙','2026','实验班A','2026-08-01T00:00:00')")
        rows = {r.course_code: r for r in coverage.program_coverage("P2", s)}
        self.assertEqual(rows["C-LA2"].kps_built, 1)
        self.assertEqual(rows["C-ML2"].kps_built, 0, "线性代数的知识点被算进了机器学习")


if __name__ == "__main__":
    unittest.main()
