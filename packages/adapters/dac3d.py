"""DAC-3D 光学检测系统项目适配器。

这是第三个适配器，也是第一个接**真实在研产业项目**的适配器——
前两个（AGV / 视觉）读的是种子数据，这个直接读 dac-3d_system_v3.0 的 git 仓库、
测试产物与文档目录。核心代码为此改了 0 行，这正是 base.ProjectAdapter 要证明的事。

## 两个职责

1. `collect()` —— 把仓库里的异构痕迹归一化成五类标准信号。
2. `task_corpus()` —— 把项目自己的文档与源码，喂给知识点自动匹配当语料。

第二条是新增的可选能力（不在 ProjectAdapter 协议里，用 hasattr 探测）：
一个项目最懂自己的任务在讲什么，与其让核心代码去猜，不如让适配器交出来。

## 关于作者映射

git 里只有邮箱，系统里只有学号。映射表由导师维护（dac3d_authors.yaml），
适配器**不猜**：映射不到的作者直接跳过，宁可少记也不能记错人。
"""
from __future__ import annotations

import re
import subprocess
from datetime import datetime
from pathlib import Path

from packages.core.config import ROOT
from packages.core.timeutil import to_str

from .base import ProjectSignal
from .registry import register

PROJECT = "PRJ-DAC"
# 本机配置在前、模板在后。真实配置含 邮箱→学号 的身份关联，不入库
# （见 data/adapters/dac3d.example.yaml 里的说明与 .gitignore）。
# 服务器上通常只有模板：authors 为空 → 一条信号也不产出，而不是记错人。
_CONF = ROOT / "data" / "adapters" / "dac3d.yaml"
_CONF_EXAMPLE = ROOT / "data" / "adapters" / "dac3d.example.yaml"


def _load_conf() -> dict:
    import yaml

    for f in (_CONF, _CONF_EXAMPLE):
        if f.exists():
            return yaml.safe_load(f.read_text(encoding="utf-8")) or {}
    return {}


@register("dac3d")
class DAC3DAdapter:
    project_type = "optics_inspection"

    def __init__(self, repo_path: str | Path | None = None) -> None:
        conf = _load_conf()
        raw = repo_path or conf.get("repo_path") or ""
        self.repo = Path(str(raw)).expanduser() if raw else None
        self.authors: dict[str, str] = conf.get("authors") or {}
        self.ignore: set[str] = set(conf.get("ignore") or [])

    # ------------------------------------------------------------ 可用性
    @property
    def available(self) -> bool:
        return bool(self.repo and (self.repo / ".git").exists())

    def _git(self, *args: str, timeout: int = 30) -> str:
        if not self.available:
            return ""
        try:
            r = subprocess.run(["git", "-C", str(self.repo), *args],
                               capture_output=True, text=True, timeout=timeout)
            return r.stdout
        except Exception:
            return ""

    def _sid(self, email: str) -> str | None:
        """邮箱 → 学号。映射不到就返回 None，调用方必须跳过。"""
        if email in self.ignore:
            return None
        return self.authors.get(email)

    # ------------------------------------------------------------ 五类信号
    def _commits(self, since: str | None) -> list[dict]:
        args = ["log", "--pretty=%H|%ae|%aI|%s", "--numstat"]
        if since:
            args.insert(1, f"--since={since}")
        out, commits, cur = self._git(*args), [], None
        for line in out.splitlines():
            if line.count("|") >= 3:
                if cur:
                    commits.append(cur)
                sha, email, ts, subj = line.split("|", 3)
                cur = {"sha": sha, "email": email, "at": ts, "subject": subj,
                       "churn": 0, "files": 0}
            elif line.strip() and cur:
                parts = line.split("\t")
                if len(parts) == 3:
                    cur["churn"] += (int(parts[0]) if parts[0].isdigit() else 0) \
                        + (int(parts[1]) if parts[1].isdigit() else 0)
                    cur["files"] += 1
        if cur:
            commits.append(cur)
        return commits

    def collect(self, since: datetime | str | None = None) -> list[ProjectSignal]:
        s = to_str(since) if isinstance(since, datetime) else since
        out: list[ProjectSignal] = []
        if not self.available:
            return out

        merge_shas = {ln.strip() for ln in self._git("log", "--merges",
                                                     "--pretty=%H").splitlines() if ln.strip()}
        for c in self._commits(s):
            sid = self._sid(c["email"])
            if not sid:
                continue
            ref, at = c["sha"][:12], c["at"]

            # code_commit：提交行为本身 + 变更量。粒度信息比数量更有意义，
            # 所以 churn 单独记一条，让"一次提交改了 3000 行"能被看见。
            out.append(ProjectSignal(
                project_code=PROJECT, student_sid=sid, signal_class="code_commit",
                metric="commit", value=1.0, occurred_at=at, raw_ref=ref))
            out.append(ProjectSignal(
                project_code=PROJECT, student_sid=sid, signal_class="code_commit",
                metric="churn_lines", value=float(c["churn"]), occurred_at=at, raw_ref=ref))

            # doc_delivery：本次提交是否交付了文档
            if re.match(r"^docs?[:(]", c["subject"]) or "docs/" in self._git(
                    "show", "--name-only", "--pretty=", c["sha"]):
                out.append(ProjectSignal(
                    project_code=PROJECT, student_sid=sid, signal_class="doc_delivery",
                    metric="doc_commit", value=1.0, occurred_at=at, raw_ref=ref))

            # build_test：改动是否带上了测试。这是"工程质量"最便宜也最诚实的代理指标，
            # 但它只说明"写了测试"，不说明"测试测对了"——后者是代码审查的事。
            touched = self._git("show", "--name-only", "--pretty=", c["sha"])
            if "tests/" in touched or "test_" in touched:
                out.append(ProjectSignal(
                    project_code=PROJECT, student_sid=sid, signal_class="build_test",
                    metric="test_touched", value=1.0, occurred_at=at, raw_ref=ref))

            # collaboration：合并提交代表一次被评审并接受的协作
            if c["sha"] in merge_shas:
                out.append(ProjectSignal(
                    project_code=PROJECT, student_sid=sid, signal_class="collaboration",
                    metric="merge_accepted", value=1.0, occurred_at=at, raw_ref=ref))

        out.extend(self._runtime_signals())
        return out

    def _runtime_signals(self) -> list[ProjectSignal]:
        """runtime：软件在环运行日志里的真实跑批结果。

        日志的作者身份取不到（日志里没有人），所以只有当仓库里存在
        由学生署名的运行记录时才产出信号。取不到就不产出——
        宁可这一类是空的，也不把跑批结果记到随便一个人头上。
        """
        out: list[ProjectSignal] = []
        if not self.available:
            return out
        log = self.repo / "dac3d_sil.log"
        if not log.exists():
            return out
        try:
            text = log.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            return out
        m = re.search(r"检测完成[^0-9]*(\d+)\s*/\s*(\d+)", text)
        if not m:
            return out
        done, total = int(m.group(1)), int(m.group(2))
        owner = self.authors.get("__sil_operator__")
        if not owner or total <= 0:
            return out
        out.append(ProjectSignal(
            project_code=PROJECT, student_sid=owner, signal_class="runtime",
            metric="wells_completion_rate", value=done / total,
            occurred_at=to_str(datetime.fromtimestamp(log.stat().st_mtime)),
            raw_ref="dac3d_sil.log"))
        return out

    # ------------------------------------------------------------ 任务语料
    def task_corpus(self) -> dict[str, str]:
        """任务代码 → 供知识点匹配使用的额外语料。

        语料有两个来源，都是项目里本来就有的东西，不额外维护：
          1. 半年实践计划里每一关的验收清单（学生真正要做到的事，写得最具体）
          2. 该关"对应模块"点名的源码文件的模块文档字符串

        子任务与清单小节的对齐，复用的是同一个 lexmatch——
        用哪把尺子量任务，就用哪把尺子对齐语料，免得两处判断打架。
        """
        sections = self._curriculum_sections() if self.available else {}
        if not sections:
            # 活仓库不在（教学服务器上通常如此）→ 回落到快照。
            # 快照由 scripts/snapshot_corpus.py 生成并入库，只含教师写的验收清单，
            # 不含代码与图纸。没有它，服务器上的匹配会掉回"只用任务名"的水平。
            return self._corpus_snapshot()

        from packages.graph import repo as graph_repo
        from packages.tools.lexmatch import Doc, Index

        proj = graph_repo.get_project(PROJECT)
        if not proj:
            return {}
        corpus: dict[str, str] = {}
        tasks = graph_repo.list_tasks(proj["id"])
        by_code = {t.code: t for t in tasks}

        for level, sec in sections.items():
            top = f"T-DAC-{level}"
            if top not in by_code:
                continue
            corpus[top] = sec["full"] + " " + self._module_docstrings(sec["modules"])
            groups = sec["groups"]
            if not groups:
                continue
            gi = Index([Doc(i, g["text"]) for i, g in enumerate(groups)])
            for t in tasks:
                if not t.code.startswith(top + "-"):
                    continue
                hits = gi.search(t.name, top_k=1)
                # 对不上就退回整关语料：宁可粗一点，也不能因为对齐失败就没有语料
                corpus[t.code] = groups[hits[0].key]["text"] if hits else sec["full"]
        return corpus

    def _corpus_snapshot(self) -> dict[str, str]:
        f = ROOT / "data" / "adapters" / "dac3d_corpus.json"
        if not f.exists():
            return {}
        import json

        try:
            return json.loads(f.read_text(encoding="utf-8")).get("corpus") or {}
        except Exception:
            return {}

    def _curriculum_sections(self) -> dict[str, dict]:
        """解析 docs/dac3d-lab-curriculum.md 的六道关卡。"""
        f = self.repo / "docs" / "dac3d-lab-curriculum.md"
        if not f.exists():
            return {}
        text = f.read_text(encoding="utf-8", errors="ignore")
        out: dict[str, dict] = {}
        # 「## 关卡 3 · 光路之心」——关卡号就是任务号，这不是巧合，是任务树照着它建的
        parts = re.split(r"\n##\s+关卡\s*(\d+)\s*[·•]\s*", text)
        for i in range(1, len(parts) - 1, 2):
            level, body = parts[i], parts[i + 1]
            mods = re.findall(r"[`]([^`]+\.(?:py|v|yaml|md))[`]", body)
            groups = []
            for g in re.split(r"\n###\s+", body)[1:]:
                lines = [ln.strip("-☐☑ \t") for ln in g.splitlines() if "☐" in ln or "☑" in ln]
                if lines:
                    groups.append({"title": g.splitlines()[0].strip(),
                                   "text": g.splitlines()[0] + " " + " ".join(lines)})
            out[level] = {"full": re.sub(r"\s+", " ", body)[:2000],
                          "groups": groups, "modules": mods}
        return out

    def _module_docstrings(self, rel_paths: list[str]) -> str:
        """取源码文件开头的说明文字。代码里的话往往比文档更准。"""
        chunks = []
        for rel in rel_paths[:8]:
            p = self.repo / rel
            if not p.exists() or p.is_dir():
                continue
            try:
                head = p.read_text(encoding="utf-8", errors="ignore")[:1200]
            except Exception:
                continue
            m = re.search(r'"""(.+?)"""', head, re.S) or re.search(r"//\s*(.+)", head)
            if m:
                chunks.append(re.sub(r"\s+", " ", m.group(1))[:300])
        return " ".join(chunks)
