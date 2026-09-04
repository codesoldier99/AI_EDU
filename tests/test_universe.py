"""知识宇宙：视图投影。

这个文件存在的直接原因是一次线上事故：Phase 4.2 把 ML 合并进 G18Z21022 之后，
前端里写死的 /api/universe/ML 集体 404，服务器上的知识宇宙直接打不开；
而更隐蔽的一半是——就算把课程代码改对，归属移交也让 13% 的依赖边变成了跨课程边，
单课程视图会把它们全部滤掉，"根因在另一门课里"这件事在图上再也看不见。

所以这里钉两件事：旧课程代码必须还能指回来；跨课程视图必须真的跨课程。
"""
from __future__ import annotations

import unittest

from base import DBTestCase

from packages.agents import universe as U
from packages.graph import repo as g


class TestUniverseCourseResolution(DBTestCase):
    def _two_courses(self):
        """两门课，一条跨课程依赖：数学的「链式法则」挡着深度学习的「反向传播」。"""
        math = g.upsert_course("C-MATH", "高等数学B（上）", 4.5, "1")
        dl = g.upsert_course("C-DL", "深度学习", 3.0, "4")
        chain = g.upsert_kp(math, "ML-02-08", "链式法则", unit="微分学")
        fwd = g.upsert_kp(dl, "ML-10-04", "前向传播", unit="第10章 神经网络")
        bp = g.upsert_kp(dl, "ML-10-05", "反向传播算法", unit="第10章 神经网络")
        g.add_edge(chain, bp)            # 跨课程
        g.add_edge(fwd, bp)              # 课内
        return math, dl, chain, fwd, bp

    def test_unknown_course_code_returns_none(self):
        self.assertIsNone(U.build_universe("NOPE"))

    def test_merged_course_code_still_resolves(self):
        """老师收藏的 /api/universe/ML 这类深链，合并之后必须还能打开。"""
        self._two_courses()
        new_id = g.upsert_course("G18Z21022", "机器学习", 3.0, "2")
        g.upsert_course("ML", "机器学习原理与应用", 4.0, "3")
        g.merge_course("ML", "G18Z21022")

        d = U.build_universe("ML")
        self.assertIsNotNone(d, "旧课程代码打不开了——这正是服务器上那次故障")
        self.assertEqual(d["course"]["code"], "G18Z21022")
        self.assertEqual(g.get_course("ML")["id"], new_id)

    def test_single_course_view_drops_cross_course_edges(self):
        """不是 bug，是单课程视图的定义。写成用例是为了让下一个人知道它是有意的。"""
        _math, _dl, _chain, _fwd, bp = self._two_courses()
        d = U.build_universe("C-DL")
        self.assertFalse(d["cross_course"])
        ids = {n["id"] for n in d["nodes"]}
        self.assertIn(bp, ids)
        self.assertEqual(len(d["edges"]), 1, "课内那条边应当在，跨课程那条不该在")

    def test_cross_course_view_keeps_them(self):
        math, _dl, chain, _fwd, bp = self._two_courses()
        d = U.build_universe(U.ALL_COURSES)
        self.assertTrue(d["cross_course"])
        self.assertEqual(len(d["edges"]), 2)
        self.assertEqual(d["cross_edges"], 1)
        self.assertIn([chain, bp], d["edges"])
        # 星系按课程分，不按章节——一团星系就是一门课
        self.assertEqual(sorted(d["units"]), sorted(["高等数学B（上）", "深度学习"]))
        node = next(n for n in d["nodes"] if n["id"] == chain)
        self.assertEqual(node["unit"], "高等数学B（上）")
        self.assertEqual(node["course_code"], "C-MATH")
        self.assertEqual(node["chapter"], "微分学", "章节信息不该在分组时丢掉")
        self.assertEqual(g.get_course_by_id(math)["code"], "C-MATH")

    def test_star_is_accepted_as_an_alias_for_all(self):
        self._two_courses()
        self.assertEqual(U.build_universe("*")["course"]["code"], U.ALL_COURSES)

    def test_a_real_course_named_all_wins_over_the_sentinel(self):
        """哨兵值不该抢走真实课程代码。"""
        self._two_courses()
        g.upsert_course("ALL", "某门真的叫 ALL 的课", 1.0, "1")
        d = U.build_universe("ALL")
        self.assertFalse(d["cross_course"])
        self.assertEqual(d["course"]["name"], "某门真的叫 ALL 的课")


if __name__ == "__main__":
    unittest.main()
