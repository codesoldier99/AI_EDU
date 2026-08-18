"""测试基类：每个用例一个临时库，互不干扰。"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from packages.core.db import Database, set_db  # noqa: E402


class OfflineLLMMixin:
    """把"嘴"钉在离线表达器上。

    两个理由：结构性断言（解析器收不收、格式对不对）必须可复现；
    以及跑一次测试不该真的去打一遍模型接口。
    真实底座的可替换性由 tests/test_llm_switch.py 单独证明。
    """

    def setUp(self) -> None:
        super().setUp()
        from packages.llm import gateway
        from packages.llm.offline import OfflineClient

        self._prev_llm = gateway.get_client()
        gateway.set_client(OfflineClient())

    def tearDown(self) -> None:
        from packages.llm import gateway

        gateway.set_client(self._prev_llm)
        super().tearDown()


class DBTestCase(unittest.TestCase):
    seed_course = False

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        db = Database(f"sqlite:///{Path(self._tmp.name) / 'test.db'}")
        set_db(db)
        db.migrate(verbose=False)
        self.db = db
        if self.seed_course:
            self.build_mini_graph()

    def tearDown(self) -> None:
        self.db.close()
        self._tmp.cleanup()

    # 一张小图：A -> B -> C，另有 D 依赖 B
    def build_mini_graph(self) -> None:
        from packages.graph import repo as g

        cid = g.upsert_course("T", "测试课程", 3)
        self.course_id = cid
        self.kp = {}
        for code, name in [("A", "链式法则"), ("B", "反向传播"), ("C", "梯度消失"),
                           ("D", "批归一化")]:
            self.kp[code] = g.upsert_kp(cid, code, name, f"{name}的说明")
        g.add_edge(self.kp["A"], self.kp["B"])
        g.add_edge(self.kp["B"], self.kp["C"])
        g.add_edge(self.kp["B"], self.kp["D"])
        m = g.upsert_module("M1", "数学与建模基础")
        for c in self.kp.values():
            g.link_kp_module(c, m, 1.0)

        from packages.state import repo as s

        self.student = s.upsert_student("S001", "测试学生", "2026级", "实验班A")
