"""确定性文本匹配：中英混排的 TF-IDF 相似度与证据抽取。

**这是知识点自动匹配的判定部分。** 铁律 3 要求凡涉及判定必须走确定性工具，
所以"这个任务像哪几个知识点"由本模块算，大模型只负责把结果说成人话。
换一个模型、断网、模型胡说，这里算出来的候选集与分数都不变。

为什么不用向量：向量分数解释不了。教师审候选时要看见"因为命中了'编码器/四倍频/PSO'
这几个词"，而不是"余弦相似度 0.83"。可解释性在这里比召回率重要——
教师不信任的候选，再准也不会被采纳。

中文分词用字符二元组（bigram），不引入 jieba：
零依赖是本项目的硬约束，且在知识点名称这种短文本上，bigram 的表现足够。
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

_EN = re.compile(r"[A-Za-z][A-Za-z0-9_+.-]{1,}")
_ZH = re.compile(r"[一-鿿]+")
# 停用词：这些字在知识点名称里到处都是，命中了也说明不了什么
_STOP = {
    "能力", "方法", "分析", "设计", "实现", "使用", "进行", "以及", "一个", "什么",
}
# 虚词：跨过它们切出来的二元组（"作与""的测"）是构词的边角料，不是证据。
# 教师看到"因为命中了'作与'"只会失去信任，而这类噪声正是字面匹配最惹人烦的地方。
_FUNC = set("的与和及或把被了着过而之并且")


def tokens(text: str) -> list[str]:
    """切成可比较的词元：英文按词，中文按字符二元组。"""
    out: list[str] = []
    for m in _EN.finditer(text):
        w = m.group(0).lower()
        if len(w) >= 2:
            out.append(w)
    for m in _ZH.finditer(text):
        seg = m.group(0)
        if len(seg) == 1:
            if seg not in _STOP:
                out.append(seg)
            continue
        for i in range(len(seg) - 1):
            g = seg[i:i + 2]
            if g in _STOP or g[0] in _FUNC or g[1] in _FUNC:
                continue
            out.append(g)
    return out


@dataclass
class Doc:
    """待匹配的一个候选项。key 是调用方的业务标识（这里是知识点 id）。"""
    key: object
    text: str
    tf: dict[str, int] = field(default_factory=dict)

    def build(self) -> "Doc":
        for t in tokens(self.text):
            self.tf[t] = self.tf.get(t, 0) + 1
        return self


@dataclass
class Hit:
    key: object
    score: float
    terms: list[str]           # 证据：命中的词元，按贡献排序
    matched_weight: float      # 命中词元占查询总权重的比例


class Index:
    """一次性建好的词表索引。知识点集合不常变，建一次可反复查。"""

    def __init__(self, docs: list[Doc]):
        self.docs = [d.build() if not d.tf else d for d in docs]
        n = max(1, len(self.docs))
        df: dict[str, int] = {}
        for d in self.docs:
            for t in d.tf:
                df[t] = df.get(t, 0) + 1
        # 平滑 IDF：只出现在一两个知识点里的词才是有区分度的证据
        self.idf = {t: math.log((n + 1) / (c + 0.5)) for t, c in df.items()}
        self.norm = {
            id(d): math.sqrt(sum((f * self.idf.get(t, 0.0)) ** 2 for t, f in d.tf.items())) or 1.0
            for d in self.docs
        }

    def search(self, query: str, top_k: int = 8, min_score: float = 0.0) -> list[Hit]:
        qtf: dict[str, int] = {}
        for t in tokens(query):
            qtf[t] = qtf.get(t, 0) + 1
        if not qtf:
            return []
        qw = {t: f * self.idf.get(t, 0.0) for t, f in qtf.items()}
        qnorm = math.sqrt(sum(v * v for v in qw.values())) or 1.0
        total_w = sum(qw.values()) or 1.0

        hits: list[Hit] = []
        for d in self.docs:
            dot, contrib = 0.0, {}
            for t, qv in qw.items():
                f = d.tf.get(t)
                if not f:
                    continue
                c = qv * f * self.idf.get(t, 0.0)
                dot += c
                contrib[t] = c
            if dot <= 0:
                continue
            score = dot / (qnorm * self.norm[id(d)])
            terms = sorted(contrib, key=lambda t: -contrib[t])[:6]
            mw = sum(qw[t] for t in contrib) / total_w
            if score >= min_score:
                hits.append(Hit(d.key, round(score, 4), terms, round(mw, 4)))
        hits.sort(key=lambda h: (-h.score, str(h.key)))
        return hits[:top_k]


def confidence(hits: list[Hit], i: int = 0) -> float:
    """把第 i 名的得分转成 0–1 置信度。

    三个来源，都必须是**随排名单调不增**的——待审队列按置信度排序，
    若第 4 名能比第 3 名更"有把握"，教师看到的顺序就自相矛盾了：

      绝对相似度  这段文字和这个知识点到底有多像
      相对强度    与第一名的比值，衡量"在这批候选里排第几档"
      命中覆盖    查询里有多大比重的权重被这个知识点接住了

    只看绝对分数会骗人——查询词一多，分数普遍偏低。所以还要看第一名是否
    断层领先：挤在一起说明这段文字本身就分不出来，该让教师看。
    这与铁律 7"判不了 ≠ 判错"是同一条思路。
    """
    if not hits or i >= len(hits):
        return 0.0
    h, top = hits[i], hits[0]
    rel = h.score / top.score if top.score > 0 else 0.0
    conf = (0.50 * min(1.0, h.score / 0.45)
            + 0.30 * rel
            + 0.20 * min(1.0, h.matched_weight / 0.5))
    if i == 0:
        # 第一名额外看断层：领先第二名越多，越敢说"就是它"
        nxt = hits[1].score if len(hits) > 1 else 0.0
        margin = (top.score - nxt) / top.score if top.score > 0 else 1.0
        conf = conf * 0.85 + 0.15 * min(1.0, margin / 0.35)
    return round(min(1.0, conf), 3)
