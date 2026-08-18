"""SKILL.md 的解析与装载。零依赖：frontmatter 用一个受限的 YAML 子集自解析。

受限是刻意的——教师要写的只是几个键值和一段说明，
引入完整 YAML 反而把"任意结构"带进来，而任意结构最后总会变成任意行为。

安全闸（对齐 DeepTutor 的 safety gate，砍掉与远程分发相关的部分）：
    · 只读 skills/ 目录下的 .md，且解析后的真实路径必须仍在该目录内（防目录穿越）
    · 单文件上限 64 KB
    · frontmatter 里的 `always:` 一律剥离——技能包不许申明"任何场景都必须加载我"
    · 记录 sha256 与 mtime 作为溯源信息，教师报告里可以看到"这次用了哪个包"
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

from packages.core.config import ROOT

SKILLS_DIR = ROOT / "skills"
MAX_BYTES = 64 * 1024
# 目录说明文件不是技能包
DOC_FILES = {"README.md", "readme.md"}


def _rel(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:      # 测试里把 SKILLS_DIR 指到了临时目录
        return str(path)

# 技能包能影响的三件事，逐条对应一个已存在的确定性策略点
KINDS = (
    "asking_style",   # 追问方式的表达要点 -> AskingStrategy 选中该 style 时注入
    "quiz_template",  # 出题要求           -> QuizAgent 起草时注入
    "review_note",    # 评审关注点         -> 教师侧参考，不参与判定
)

# frontmatter 中允许出现的键；其余一律忽略（含 always:）
ALLOWED_KEYS = {
    "name", "kind", "style", "title", "priority",
    "applies_to_kp_type", "applies_to_course", "qtype", "author", "description",
}


@dataclass
class SkillPack:
    name: str = ""
    kind: str = ""
    title: str = ""
    style: str = ""
    priority: int = 0
    applies_to_kp_type: list = field(default_factory=list)
    applies_to_course: list = field(default_factory=list)
    qtype: str = ""
    author: str = ""
    description: str = ""
    body: str = ""
    path: str = ""
    sha256: str = ""
    error: str = ""

    def to_dict(self) -> dict:
        d = dict(self.__dict__)
        d["body"] = self.body[:400]
        return d

    def guidance(self, limit: int = 600) -> str:
        """压成一行喂给表达层。技能包提供的是**说法**，不是**判断**。"""
        text = re.sub(r"^#.*$", "", self.body, flags=re.M)
        text = re.sub(r"\s+", " ", text).strip()
        return text[:limit]

    def matches(self, kp_type: str = "", course_code: str = "") -> bool:
        if self.applies_to_kp_type and kp_type and kp_type not in self.applies_to_kp_type:
            return False
        if self.applies_to_course and course_code and course_code not in self.applies_to_course:
            return False
        return True


# ---------------------------------------------------------------- frontmatter
_FM = re.compile(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", re.S)


def parse_frontmatter(text: str) -> tuple[dict, str]:
    m = _FM.match(text)
    if not m:
        return {}, text
    meta: dict = {}
    key = None
    for raw in m.group(1).splitlines():
        line = raw.rstrip()
        if not line.strip() or line.strip().startswith("#"):
            continue
        if re.match(r"^\s*-\s+", line) and key:
            meta.setdefault(key, [])
            if isinstance(meta[key], list):
                meta[key].append(_scalar(re.sub(r"^\s*-\s+", "", line)))
            continue
        m2 = re.match(r"^([A-Za-z_][\w-]*)\s*:\s*(.*)$", line)
        if not m2:
            continue
        key, val = m2.group(1), m2.group(2).strip()
        if val == "":
            meta[key] = []
        elif val.startswith("[") and val.endswith("]"):
            meta[key] = [_scalar(x) for x in val[1:-1].split(",") if x.strip()]
        else:
            meta[key] = _scalar(val)
    return meta, m.group(2)


def _scalar(v: str):
    v = v.strip().strip('"').strip("'")
    if re.fullmatch(r"-?\d+", v):
        return int(v)
    return v


# ---------------------------------------------------------------- 装载
_cache: dict[str, tuple[float, SkillPack]] = {}


def _read_pack(path: Path) -> SkillPack:
    raw = path.read_bytes()
    if len(raw) > MAX_BYTES:
        return SkillPack(name=path.stem, path=str(path), error="文件超过 64KB，已跳过")
    text = raw.decode("utf-8", errors="replace")
    meta, body = parse_frontmatter(text)
    meta = {k: v for k, v in meta.items() if k in ALLOWED_KEYS}   # 剥离 always: 等
    kind = str(meta.get("kind", ""))
    pack = SkillPack(
        name=str(meta.get("name") or path.stem),
        kind=kind,
        title=str(meta.get("title") or meta.get("name") or path.stem),
        style=str(meta.get("style", "")),
        priority=int(meta.get("priority", 0) or 0),
        applies_to_kp_type=_as_list(meta.get("applies_to_kp_type")),
        applies_to_course=_as_list(meta.get("applies_to_course")),
        qtype=str(meta.get("qtype", "")),
        author=str(meta.get("author", "")),
        description=str(meta.get("description", "")),
        body=body.strip(),
        path=_rel(path),
        sha256=hashlib.sha256(raw).hexdigest()[:16],
    )
    if kind not in KINDS:
        pack.error = f"未知 kind：{kind or '(缺失)'}；允许 {'/'.join(KINDS)}"
    elif not pack.body:
        pack.error = "正文为空"
    return pack


def _as_list(v) -> list:
    if v is None:
        return []
    return list(v) if isinstance(v, list) else [v]


def load_all(include_broken: bool = False) -> list[SkillPack]:
    """装载 skills/ 下全部技能包。按 mtime 缓存，改文件即生效，无需重启。"""
    if not SKILLS_DIR.exists():
        return []
    base = SKILLS_DIR.resolve()
    out: list[SkillPack] = []
    for path in sorted(SKILLS_DIR.rglob("*.md")):
        if path.name in DOC_FILES:
            continue
        try:
            real = path.resolve()
            real.relative_to(base)          # 目录穿越 / 符号链接外泄
        except (ValueError, OSError):
            continue
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        key = str(path)
        hit = _cache.get(key)
        pack = hit[1] if hit and hit[0] == mtime else _read_pack(path)
        _cache[key] = (mtime, pack)
        if pack.error and not include_broken:
            continue
        out.append(pack)
    out.sort(key=lambda p: (-p.priority, p.name))
    return out


def for_kind(kind: str, kp_type: str = "", course_code: str = "") -> list[SkillPack]:
    return [p for p in load_all() if p.kind == kind and p.matches(kp_type, course_code)]


def pick_asking_style(style: str, kp_type: str = "", course_code: str = "") -> SkillPack | None:
    """取该追问方式的技能包。

    注意方向：**先由 AskingStrategy 确定性地选出 style，再来找技能包**，
    而不是让技能包决定问什么。技能包只影响话怎么说。
    """
    packs = [p for p in for_kind("asking_style", kp_type, course_code)
             if not p.style or p.style == style]
    return packs[0] if packs else None


def quiz_guidance(qtype: str = "", kp_type: str = "", course_code: str = "") -> str:
    packs = [p for p in for_kind("quiz_template", kp_type, course_code)
             if not p.qtype or p.qtype == qtype]
    return "；".join(p.guidance(240) for p in packs[:2])


def stats() -> dict:
    packs = load_all(include_broken=True)
    ok = [p for p in packs if not p.error]
    return {
        "dir": _rel(SKILLS_DIR),
        "total": len(packs),
        "loaded": len(ok),
        "broken": [{"path": p.path, "error": p.error} for p in packs if p.error],
        "by_kind": {k: sum(1 for p in ok if p.kind == k) for k in KINDS},
        "items": [{"name": p.name, "kind": p.kind, "title": p.title, "style": p.style,
                   "priority": p.priority, "path": p.path, "sha256": p.sha256}
                  for p in ok],
    }
