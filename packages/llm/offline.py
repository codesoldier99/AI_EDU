"""离线降级表达器：无 API Key 时的确定性替身。

存在意义有二：
1. 本地演示与 CI 不依赖任何外部服务，随时可跑通全流程；
2. 它反证了架构的正确性——把"脑"（状态与策略）与"嘴"（表达）分开之后，
   换掉嘴，系统的判断不会改变，只是话说得没那么漂亮。

因此本类只做模板化表达，绝不产生任何新的事实判断。
"""
from __future__ import annotations

import hashlib
import math
import re
import time

from packages.core.config import CONFIG

from .base import LLMResponse, Vector, prompt_hash, rough_tokens


class OfflineClient:
    name = "offline-template"
    model_version = "v1"

    # ---- 生成 ----
    def complete_sync(self, messages: list[dict], *, model_hint: str = "",
                      max_tokens: int = 800) -> LLMResponse:
        t0 = time.time()
        sysmsg = "\n".join(m["content"] for m in messages if m.get("role") == "system")
        user = "\n".join(m["content"] for m in messages if m.get("role") == "user")
        text = self._render(sysmsg, user)
        return LLMResponse(
            text=text,
            model_name=self.name,
            model_version=self.model_version,
            prompt_hash=prompt_hash(messages),
            tokens_in=rough_tokens(sysmsg + user),
            tokens_out=rough_tokens(text),
            latency_ms=int((time.time() - t0) * 1000),
            degraded=True,
        )

    async def complete(self, messages: list[dict], *, model_hint: str = "",
                       max_tokens: int = 800) -> LLMResponse:
        return self.complete_sync(messages, model_hint=model_hint, max_tokens=max_tokens)

    def _render(self, sysmsg: str, user: str) -> str:
        """依据 prompt 中的结构化字段拼装话术，不做任何推断。"""
        fields = dict(re.findall(r"^-\s*([一-鿿A-Za-z_]+)\s*[:：]\s*(.+)$",
                                 user, flags=re.M))
        intent = fields.get("意图", "")
        if intent == "苏格拉底追问":
            return self._ask(fields)
        if intent == "班级诊断":
            return self._diagnosis(fields)
        if intent == "错误归因":
            return self._error(fields)
        if intent == "代码评审":
            return self._review(fields)
        if intent == "周期简报":
            return self._brief(fields)
        if intent == "学习副驾驶":
            return self._copilot(fields)
        if intent == "拉取式学习建议":
            return self._pull(fields)
        if intent == "学分映射建议":
            return self._credit(fields)
        if intent == "教师简报":
            return self._teacher_brief(fields)
        if intent == "教学大纲章节说明":
            return self._syllabus_chapter(fields)
        if intent == "授课计划说明":
            return self._teaching_plan_session(fields)
        if intent == "课件要点":
            return self._deck_bullets(fields)
        if intent == "课件概念讲解":
            return self._deck_concept(fields)
        if intent == "教学大纲章节说明修订":
            return self._revise(fields, "现有说明")
        if intent == "授课计划说明修订":
            return self._revise(fields, "现有说明")
        if intent == "课件要点修订":
            return self._revise(fields, "现有要点")
        return "（离线表达器）已按结构化输入生成，如下：\n" + user.strip()[:600]

    def _ask(self, f: dict) -> str:
        target = f.get("目标知识点", "该知识点")
        entry = f.get("切入点", "")
        style = f.get("追问方式", "概念澄清")
        ctx = f.get("教材依据", "")
        lead = f"我们先不急着写答案。你已经掌握了「{entry}」，就从这里出发——" if entry else \
               "我们先把问题拆开看——"
        by_style = {
            "概念澄清": f"用你自己的话说说，「{target}」到底在解决什么问题？它和你已知的那一步差在哪儿？",
            "反例质疑": f"如果我把条件改成一个极端情形，「{target}」的结论还成立吗？举一个会失败的例子。",
            "类比迁移": f"你之前在「{entry or '相近问题'}」里用过类似的思路，能把那套做法搬到「{target}」上吗？哪一步搬不动？",
            "条件变更": f"把「{target}」里的一个前提去掉，结果会怎么变？先预测，再验证。",
        }
        q = by_style.get(style, by_style["概念澄清"])
        tail = f"\n\n（教材里相关的一句：{first_sentence(ctx)[:80]}）" if ctx else ""
        return f"{lead}\n\n{q}\n\n想到哪一步就说到哪一步，卡住也直接说卡在哪。{tail}"

    def _diagnosis(self, f: dict) -> str:
        return (
            f"本次诊断覆盖 {f.get('学生数', '?')} 名学生、{f.get('知识点数', '?')} 个知识点。\n"
            f"最需要处理的是：{f.get('待攻克点', '（无）')}。\n"
            f"根因指向：{f.get('根因', '（无）')}——建议下次课先补这一环，再回到原题。\n"
            f"典型错误：{f.get('典型错误', '（暂无沉淀）')}。\n"
            f"{f.get('数据充分性', '')}"
        )

    def _error(self, f: dict) -> str:
        return (
            f"该作答的问题集中在「{f.get('知识点', '?')}」。\n"
            f"表层现象：{f.get('现象', '?')}\n"
            f"可能归因：概念边界不清或前置步骤缺失，建议核对「{f.get('候选根因', '前置知识点')}」。\n"
            f"（离线归因为规则模板，需教师确认后方可写入错误模式库）"
        )

    def _review(self, f: dict) -> str:
        return (
            f"静态检查已给出 {f.get('静态问题数', '0')} 条确定性问题。\n"
            f"就设计层面看：{f.get('关注点', '模块职责与边界')} 值得再推敲。\n"
            f"建议：{f.get('建议', '先补齐测试，再重构接口')}。\n"
            f"（离线评审不产生知识点判定，知识点关联只取静态规则映射）"
        )

    def _brief(self, f: dict) -> str:
        return (
            f"本周你保持了 {f.get('连续天数', '0')} 天连续投入，"
            f"{f.get('本周破关', '暂无破关记录')}。\n"
            f"下一步建议：{f.get('下一步', '回到当前任务的缺口知识点')}。\n"
            f"记住，卡住是正常的，回来继续才是关键。"
        )

    def _teacher_brief(self, f: dict) -> str:
        return (
            f"本周全班活跃 {f.get('活跃情况', '-')}。\n"
            f"需要留意的是：{f.get('需介入', '暂无')}。\n"
            f"降级热点：{f.get('降级热点', '暂无')}。\n"
            f"建议动作：{f.get('下一步', '优先联系名单内学生')}。\n"
            "（本简报只描述学情与系统状态，不生成任何针对教师的评价性数据）"
        )

    def _syllabus_chapter(self, f: dict) -> str:
        kps = f.get("知识点", "")
        return (
            f"本章围绕「{f.get('章节', '本章')}」展开，覆盖知识点：{kps or '（无）'}。\n"
            f"学完本章应能把这些知识点用于后续章节与项目任务，建议按知识点列出的顺序学习。\n"
            "（离线模板：未接入大模型时的结构化占位文案，不影响章节结构本身的正确性）"
        )

    def _teaching_plan_session(self, f: dict) -> str:
        return (
            f"本次课主题「{f.get('本次课主题', '')}」，课时 {f.get('课时', '90 分钟')}。\n"
            f"知识点：{f.get('知识点', '（无）')}。\n"
            "建议环节：导入（回顾前置知识）→ 讲授（本次课知识点）→ 练习（结合项目任务）→ 小结。"
        )

    def _deck_bullets(self, f: dict) -> str:
        kp = f.get("知识点", "该知识点")
        mat = (f.get("教材依据") or "").strip()
        lines = [f"「{kp}」的定义与适用场景"]
        if mat:
            lines.append(first_sentence(mat)[:60])
        lines += [f"「{kp}」的常见误区", f"「{kp}」与前置知识点的联系", "课堂练习/思考题"]
        return "；".join(lines)

    def _deck_concept(self, f: dict) -> str:
        """离线模式下的课件概念讲解：同样按 `- 字段：值` 格式回复，保证在线/离线
        走的是同一条解析路径——只是内容是模板，不是真的类比/易错点。"""
        kp = f.get("知识点", "该知识点")
        mat = (f.get("教材依据") or "").strip()
        analogy = (f"可以把「{kp}」类比成一个你熟悉的日常流程——"
                   "具体怎么类比，离线模式给不出真实答案，接入大模型后重新生成即可获得。")
        points = [f"「{kp}」的定义与适用场景", f"「{kp}」与前置知识点的联系"]
        if mat:
            points.append(first_sentence(mat)[:60])
        return (
            f"- 一句话类比：{analogy}\n"
            f"- 讲解要点：{'；'.join(points)}\n"
            f"- 应用例子：结合项目任务实践「{kp}」\n"
            "- 易错点建议："
        )

    def _revise(self, f: dict, existing_key: str) -> str:
        """离线模式下的"对话式修订"：不真正理解指令，只做确定性的可见反馈，
        提醒教师这不是真改写——避免看起来像是模型认真改了、实际没有。"""
        existing = f.get(existing_key, "")
        instr = f.get("教师指令", "")
        return (
            f"{existing}\n\n"
            f"（离线模式下无法真正按「{instr}」重新组织文字，以上原样保留；"
            "接入大模型后重新发起这次修订即可获得真实改写效果。）"
        )

    def _copilot(self, f: dict) -> str:
        mat = (f.get("项目资料") or "").strip()
        lines = [ln.strip("- ").strip() for ln in mat.splitlines()
                 if ln.strip() and not ln.startswith("#")][:4]
        out = [f"就「{f.get('问题', '这个问题')}」，先按下面的顺序自查——", ""]
        out.append("1. 复核输入侧：训练与线上的预处理是否完全一致（最常见的一类问题）。")
        out.append("2. 复核数据侧：线上分布是否已经漂移，采样条件与训练集是否可比。")
        out.append("3. 复核评估侧：离线指标口径与线上口径是否是同一件事。")
        if lines:
            out += ["", "项目资料里与此相关的记录：", *[f"· {ln}" for ln in lines]]
        if f.get("薄弱能力"):
            out += ["", f"结合你的能力档案（{f['薄弱能力']} 偏弱），建议从第 1 条开始动手。"]
        if f.get("当前缺口"):
            out.append(f"当前任务的缺口知识点是「{f['当前缺口']}」，排查时顺带补上。")
        out += ["", "先自己跑一遍这三步，把结果发我，我们再往下定位。"]
        return "\n".join(out)

    def _pull(self, f: dict) -> str:
        return (
            f"当前任务「{f.get('当前任务', '')}」需要 {f.get('所需知识点数', '?')} 个知识点，"
            f"你已掌握 {f.get('已掌握', '0')} 个。\n"
            f"此刻最挡路的是：{f.get('此刻最挡路', '（无）')}。\n"
            f"{f.get('理由', '')}\n"
            "先攻这一两个，学完立刻回到任务里用一次——这比按课程顺序往下推有效得多。"
        )

    def _credit(self, f: dict) -> str:
        return (
            f"依据项目描述与能力图谱的语义匹配，建议关注：{f.get('建议课程', '（无匹配）')}。\n"
            f"{f.get('说明', '')}\n"
            "所有映射均可追溯到具体知识点节点，规则校验已叠加。"
        )

    # ---- 向量 ----
    def embed(self, texts: list[str]) -> list[Vector]:
        return [
            Vector(values=hashed_embedding(t), model=CONFIG.embed_model,
                   version=CONFIG.embed_version)
            for t in texts
        ]


def first_sentence(text: str, min_len: int = 12) -> str:
    """从一段（可能已被压平的）材料里取第一句可读的话，去掉 Markdown 标记。"""
    clean = re.sub(r"[#*`>|\-]{1,}", " ", text)
    for seg in re.split(r"[。；;\n]", clean):
        seg = seg.strip()
        if len(seg) >= min_len:
            return seg
    return clean.strip()[:80]


def _tokens(text: str) -> list[str]:
    """中文按字符 bigram，英文数字按词。无需分词器，确定性、可离线。"""
    text = text.lower()
    words = re.findall(r"[a-z0-9_]+", text)
    zh = re.sub(r"[^一-鿿]", "", text)
    grams = [zh[i: i + 2] for i in range(len(zh) - 1)] + list(zh)
    return words + grams


def hashed_embedding(text: str, dim: int | None = None) -> list[float]:
    """哈希技巧 + TF 加权的确定性嵌入。

    它不如神经嵌入准，但满足三条要求：零依赖、完全确定、可解释。
    切换到真实 embedding 模型时只改 CONFIG.embed_model 并重算全库
    （doc_chunk 记录了 embed_model/embed_version，正是为此）。
    """
    dim = dim or CONFIG.embed_dim
    vec = [0.0] * dim
    toks = _tokens(text)
    if not toks:
        return vec
    for t in toks:
        h = int(hashlib.md5(t.encode("utf-8")).hexdigest()[:8], 16)
        idx = h % dim
        sign = 1.0 if (h >> 31) & 1 == 0 else -1.0
        vec[idx] += sign
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [round(v / norm, 6) for v in vec]
