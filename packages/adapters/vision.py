"""机器视觉项目适配器。

runtime 信号来源：数据集迭代记录、模型精度曲线。
与 AGV 适配器的差别只在 `_read_raw` 与字段名——这正是"接第 5 个和第 10 个
适配器成本相同"的含义。
"""
from __future__ import annotations

import json
from datetime import datetime

from packages.core.config import ROOT

from .base import ProjectSignal
from .registry import register


@register("vision")
class VisionAdapter:
    project_type = "vision"
    source_dir = ROOT / "data" / "seed" / "signals"

    def _read_raw(self) -> list[dict]:
        f = self.source_dir / "vision_runs.jsonl"
        if not f.exists():
            return []
        return [json.loads(ln) for ln in f.read_text(encoding="utf-8").splitlines() if ln.strip()]

    def collect(self, since: datetime | str | None = None) -> list[ProjectSignal]:
        out: list[ProjectSignal] = []
        for r in self._read_raw():
            base = dict(project_code=r.get("project", "PRJ-VIS"), student_sid=r["sid"],
                        occurred_at=r["at"], raw_ref=r.get("exp_id", ""))
            out.append(ProjectSignal(signal_class="runtime", metric="map50",
                                     value=float(r.get("map50", 0)), **base))
            if r.get("dataset_version") is not None:
                out.append(ProjectSignal(signal_class="runtime", metric="dataset_iteration",
                                         value=float(r["dataset_version"]), **base))
            if r.get("doc_pages") is not None:
                out.append(ProjectSignal(signal_class="doc_delivery", metric="doc_pages",
                                         value=float(r["doc_pages"]), **base))
            if r.get("review_comments") is not None:
                out.append(ProjectSignal(signal_class="collaboration", metric="review_comments",
                                         value=float(r["review_comments"]), **base))
        return out
