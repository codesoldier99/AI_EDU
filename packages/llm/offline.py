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
        if intent == "命题出卷":
            return self._quiz(fields)
        if intent == "练习安排":
            return self._practice(fields)
        if intent == "分步解题骨架":
            return self._solve_skeleton(fields)
        if intent == "分步解题":
            return self._solve_turn(fields)
        if intent == "限域调研":
            return self._research(fields)
        if intent == "图表解读":
            return self._figure(fields)
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

    # ---- 学习工作台（吸收自 DeepTutor 的能力面）----
    # 这几个模板必须产出**能被确定性解析器接受**的格式。
    # 它们的存在证明了一件事：出题、解题、调研的骨架是结构决定的，
    # 换掉"嘴"之后流程依然跑得通，只是话没那么漂亮。
    def _quiz(self, f: dict) -> str:
        kp = f.get("知识点", "该知识点")
        sents = _sentences(f.get("教材依据", ""))
        n = max(1, min(_int(f.get("题量"), 2), len(sents) or 1))
        qtype = f.get("题型", "单选题")
        if not sents:
            return "（离线表达器）未获得教材依据，未生成题目。"
        out = []
        for i in range(n):
            s = sents[i % len(sents)]
            if "数值" in qtype or "简答" in qtype or "代码" in qtype:
                kws = "、".join(_keywords_of(s, kp)[:3])
                out += [f"[题目] 结合教材对「{kp}」的表述，说明其要点与适用条件。（第 {i + 1} 题）",
                        f"[答案] {s}",
                        f"[关键词] {kws}",
                        f"[解析] 教材依据写明：{s}。", ""]
                continue
            wrong = _negate(s)
            pos = i % 3            # 正确项位置轮换，避免"答案永远是 A"
            opts = [wrong, f"{kp}与该表述无关", "以上说法都成立"]
            opts.insert(pos, s)
            letters = "ABCD"
            body = " | ".join(f"{letters[j]}. {o[:60]}" for j, o in enumerate(opts))
            out += [f"[题目] 关于「{kp}」，以下哪一项与教材依据一致？（第 {i + 1} 题）",
                    f"[选项] {body}",
                    f"[答案] {letters[pos]}",
                    f"[解析] 教材依据写明：{s}。其余选项与该表述不符。", ""]
        return "\n".join(out)

    def _practice(self, f: dict) -> str:
        gap = f.get("题库缺口", "")
        return (
            f"这次给你 {f.get('题量', '0')} 道题，目标是：{f.get('练习目标', '（暂无）')}。\n"
            f"为什么是现在：{f.get('选中理由', '按复检与缺口排序得出')}。\n"
            "做错很正常，重要的是做完之后回到项目里用一次。"
            + (f"\n（题库在这些点上还没题，已记下：{gap}）" if gap else "")
        )

    def _solve_skeleton(self, f: dict) -> str:
        kp = (f.get("相关知识点", "") or "").split("、")[0]
        sents = _sentences(f.get("教材依据", ""))
        plan = [
            ("把题目给的已知量与要求的目标量分别列出来", "text",
             "已知量与目标量各列一行，不遗漏单位"),
            (f"确定这一步该用哪个方法或公式，并说明为什么适用于「{kp or '本题'}」", "text",
             sents[0] if sents else f"选用与{kp or '本题'}对应的方法并说明适用条件"),
            ("把已知量代入，写出可计算的表达式", "text", "写出完整表达式，暂不求值"),
            ("求值并检查量纲与数量级是否合理", "text", "给出数值结果并说明量纲是否自洽"),
        ]
        out = []
        for ask, kind, concl in plan:
            out += [f"[步骤] {ask}", f"[知识点] {kp}", f"[校验] {kind}",
                    f"[结论] {concl}", ""]
        return "\n".join(out)

    def _solve_turn(self, f: dict) -> str:
        fb = f.get("判定反馈", "")
        head = f"第 {f.get('第几步', '1/1')} 步。"
        body = f.get("当前步骤", "")
        tail = {
            "只复述当前这一步要做什么，绝不给出这一步的结果":
                "先自己写出这一步的结果，写多少算多少。",
            "可给一个关键提示，仍不得给出这一步的结果":
                "提示：回到题目条件里找还没用上的那一个。",
            "可提示这一步用到的方法名称与代入顺序，不得代替学生完成计算":
                "框架给你：先定方法，再定代入顺序，最后才动手算。",
            "可给出这一步的结论，并说明该知识点已标记需教师介入":
                "这一步的结论已经交给你了，该知识点已标记需要教师介入。",
        }.get(f.get("硬约束", ""), "")
        return "\n".join(x for x in [head + body, (f"判定：{fb}" if fb else ""), tail] if x)

    def _research(self, f: dict) -> str:
        sents = _sentences(f.get("材料原文", ""))
        if not sents:
            return "该来源下未检索到可引用的材料。"
        head = f"就「{f.get('调研主题', '本主题')}」，这一来源里可查到的表述如下。"
        return head + "".join(f"{s}。" for s in sents[:4])

    def _figure(self, f: dict) -> str:
        items = [f"{k}：{v}" for k, v in f.items() if k not in ("意图", "图形")]
        return (f"这张「{f.get('图形', '图')}」呈现的是——" + "；".join(items[:4]) + "。\n"
                "图中的数字全部来自事件流折叠出的状态，可以逐条回溯到具体作答。")

    # ---- 向量 ----
    def embed(self, texts: list[str]) -> list[Vector]:
        return [
            Vector(values=hashed_embedding(t), model=CONFIG.embed_model,
                   version=CONFIG.embed_version)
            for t in texts
        ]


def _sentences(text: str, min_len: int = 10) -> list[str]:
    """把（已被压平成一行的）材料切成可引用的句子。"""
    clean = re.sub(r"[#*`>|]", " ", text or "")
    out = []
    for seg in re.split(r"[。；;\n]", clean):
        seg = seg.strip(" -\t")
        if len(seg) >= min_len:
            out.append(seg[:110])
    return out


def _negate(sentence: str) -> str:
    """把一句陈述改成一个明显不同的说法，用作离线草案的干扰项。"""
    for a, b in (("必须", "不必"), ("不能", "可以"), ("是", "不是"),
                 ("需要", "无需"), ("增大", "减小"), ("提高", "降低")):
        if a in sentence:
            return sentence.replace(a, b, 1)
    return "与上述表述相反的说法"


def _keywords_of(sentence: str, kp: str) -> list[str]:
    parts = [p for p in re.split(r"[，,、（）()\s]+", sentence) if len(p) >= 2]
    out = [kp] if kp else []
    for p in parts:
        if p not in out:
            out.append(p)
    return out


def _int(v, default: int) -> int:
    try:
        return int(float(str(v).strip()))
    except (TypeError, ValueError):
        return default


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
