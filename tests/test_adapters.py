"""适配器：五类标准信号、新项目接入不改核心代码。

验收标准（技术方案 §5.2）：接第 5 个和第 10 个适配器的成本基本相同。
本测试用"新增一个适配器时，核心包的文件内容一个字节都不用改"来检验这一点。
"""
from __future__ import annotations

import hashlib
from datetime import datetime

from base import ROOT, DBTestCase

from packages.adapters import registry
from packages.adapters.base import SIGNAL_CLASSES, ProjectSignal, persist_signals
from packages.graph import repo as g


@registry.register("__test_new_project__")
class BrandNewAdapter:
    """演示"接入第 N 个项目"：只实现 collect，不碰核心代码。"""

    project_type = "data"

    def collect(self, since: datetime | str | None = None) -> list[ProjectSignal]:
        return [
            ProjectSignal(project_code="PNEW", student_sid="S001", signal_class="runtime",
                          metric="pipeline_uptime", value=0.97, occurred_at="2026-05-01T10:00:00"),
            ProjectSignal(project_code="PNEW", student_sid="S001", signal_class="collaboration",
                          metric="review_comments", value=4, occurred_at="2026-05-01T10:00:00"),
        ]


CORE_FILES = [
    "packages/adapters/base.py",
    "packages/adapters/registry.py",
    "packages/agents/task.py",
    "packages/state/tracker.py",
]


class TestAdapters(DBTestCase):
    seed_course = True

    def test_new_adapter_needs_no_core_change(self):
        before = {f: hashlib.md5((ROOT / f).read_bytes()).hexdigest() for f in CORE_FILES}
        g.upsert_project("PNEW", "新项目", "data", "__test_new_project__")
        n = persist_signals(registry.get_adapter("__test_new_project__").collect())
        self.assertEqual(n, 2)
        after = {f: hashlib.md5((ROOT / f).read_bytes()).hexdigest() for f in CORE_FILES}
        self.assertEqual(before, after, "接新项目不应改动核心代码")

    def test_signal_class_must_be_normalized(self):
        s = ProjectSignal(project_code="PNEW", student_sid="S001",
                          signal_class="随便一个类别", value=1)
        with self.assertRaises(ValueError):
            s.validate()

    def test_five_signal_classes(self):
        self.assertEqual(len(SIGNAL_CLASSES), 5)
        self.assertEqual(
            set(SIGNAL_CLASSES),
            {"code_commit", "build_test", "runtime", "doc_delivery", "collaboration"},
        )

    def test_signal_to_ability_is_config_driven(self):
        from packages.adapters.base import signal_to_ability

        w = signal_to_ability("runtime")
        self.assertTrue(w)
        self.assertTrue(all(k.startswith("M") for k in w))
