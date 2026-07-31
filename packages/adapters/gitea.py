"""Git 仓库适配器：项目信号采集的主要入口。

本地演示时直接读本机 git 仓库的提交历史（`git log`），
接校内 Gitea 时把 `_read_commits` 换成 API 调用即可，其余逻辑不动。
"""
from __future__ import annotations

import subprocess
from datetime import datetime
from pathlib import Path

from packages.core.timeutil import now_str, to_str

from .base import ProjectSignal
from .registry import register


@register("gitea")
class GiteaAdapter:
    project_type = "any"

    def __init__(self, repo_path: str | Path | None = None,
                 email_to_sid: dict[str, str] | None = None) -> None:
        self.repo_path = Path(repo_path) if repo_path else None
        self.email_to_sid = email_to_sid or {}

    def _read_commits(self, since: str | None) -> list[dict]:
        if not self.repo_path or not (self.repo_path / ".git").exists():
            return []
        cmd = ["git", "-C", str(self.repo_path), "log", "--pretty=%H|%ae|%aI", "--numstat"]
        if since:
            cmd.insert(4, f"--since={since}")
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=30).stdout
        except Exception:
            return []
        commits, cur = [], None
        for line in out.splitlines():
            if "|" in line and line.count("|") == 2:
                if cur:
                    commits.append(cur)
                h, email, ts = line.split("|")
                cur = {"sha": h, "email": email, "at": ts, "churn": 0, "files": 0}
            elif line.strip() and cur:
                parts = line.split("\t")
                if len(parts) == 3:
                    add = int(parts[0]) if parts[0].isdigit() else 0
                    dele = int(parts[1]) if parts[1].isdigit() else 0
                    cur["churn"] += add + dele
                    cur["files"] += 1
        if cur:
            commits.append(cur)
        return commits

    def collect(self, since: datetime | str | None = None) -> list[ProjectSignal]:
        s = to_str(since) if isinstance(since, datetime) else since
        out: list[ProjectSignal] = []
        for c in self._read_commits(s):
            sid = self.email_to_sid.get(c["email"], c["email"].split("@")[0])
            out.append(ProjectSignal(
                student_sid=sid, signal_class="code_commit", metric="churn",
                value=float(c["churn"]), occurred_at=c["at"][:19], raw_ref=c["sha"][:12]))
            out.append(ProjectSignal(
                student_sid=sid, signal_class="code_commit", metric="files_touched",
                value=float(c["files"]), occurred_at=c["at"][:19], raw_ref=c["sha"][:12]))
        return out
