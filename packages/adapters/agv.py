"""AGV / 移动机器人项目适配器。

runtime 信号来源：任务跑通率、传感器日志、定位精度。
演示环境无真机，读 data/seed/signals/agv_*.jsonl；接真机时把 `_read_raw` 换成
读 ROS bag 或平台 API 即可，核心系统不动。
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from packages.core.config import ROOT

from .base import ProjectSignal
from .registry import register


@register("agv")
class AGVAdapter:
    project_type = "robot"
    source_dir = ROOT / "data" / "seed" / "signals"

    def _read_raw(self) -> list[dict]:
        f = self.source_dir / "agv_runs.jsonl"
        if not f.exists():
            return []
        return [json.loads(ln) for ln in f.read_text(encoding="utf-8").splitlines() if ln.strip()]

    def collect(self, since: datetime | str | None = None) -> list[ProjectSignal]:
        out: list[ProjectSignal] = []
        for r in self._read_raw():
            sid, at = r["sid"], r["at"]
            out.append(ProjectSignal(
                project_code=r.get("project", "PRJ-AGV"), student_sid=sid,
                signal_class="runtime", metric="task_success_rate",
                value=float(r.get("success_rate", 0)), occurred_at=at,
                raw_ref=r.get("run_id", "")))
            if "localization_rmse_m" in r:
                out.append(ProjectSignal(
                    project_code=r.get("project", "PRJ-AGV"), student_sid=sid,
                    signal_class="runtime", metric="localization_rmse_m",
                    value=float(r["localization_rmse_m"]), occurred_at=at,
                    raw_ref=r.get("run_id", "")))
            if r.get("retries") is not None:
                out.append(ProjectSignal(
                    project_code=r.get("project", "PRJ-AGV"), student_sid=sid,
                    signal_class="runtime", metric="retries",
                    value=float(r["retries"]), occurred_at=at, raw_ref=r.get("run_id", "")))
            if r.get("ci_pass") is not None:
                out.append(ProjectSignal(
                    project_code=r.get("project", "PRJ-AGV"), student_sid=sid,
                    signal_class="build_test", metric="ci_pass",
                    value=float(r["ci_pass"]), occurred_at=at, raw_ref=r.get("run_id", "")))
        return out
