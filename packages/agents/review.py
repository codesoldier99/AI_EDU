"""模块四：代码与文档智能审查（两级流水线）。

    第一级 静态分析（确定性、成本低）→ 结果作为上下文
    第二级 大模型评审（逻辑缺陷、设计合理性、文档完整性）

`related_kp_ids` 是把工程问题翻回知识判断的**唯一通道**，也是项目与课程不再是
两张皮的关键。原则：**若某类缺陷无法可靠映射到知识点，则不回写，宁缺毋滥。**
只有 rule_kp_map 中标注 reliable=true 的规则才允许写回 L2，且证据权重仅 0.5。
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path

from packages.core.config import ROOT
from packages.core.db import dumps, get_db, loads
from packages.core.models import Message
from packages.core.timeutil import now_str
from packages.graph import repo as graph_repo
from packages.rag import retriever
from packages.state import tracker

from .base import Agent, AgentOutput

CATEGORIES = ("style", "logic", "design", "doc", "security")
SEVERITIES = ("info", "warn", "error")


@dataclass
class ReviewFinding(Message):
    file: str = ""
    line: int = 0
    category: str = "style"
    severity: str = "info"
    rule: str = ""
    message: str = ""
    suggestion: str = ""
    related_kp_ids: list = field(default_factory=list)
    produced_by: str = "static"
    reliable_mapping: bool = False


# ---------------------------------------------------------------- 静态分析
class StaticAnalyzer:
    """零依赖静态检查。确定性规则，结果可靠、成本低，是第二级的输入。"""

    def analyze_source(self, path: str, src: str) -> list[ReviewFinding]:
        out: list[ReviewFinding] = []
        try:
            tree = ast.parse(src)
        except SyntaxError as e:
            return [
                ReviewFinding(file=path, line=e.lineno or 0, category="logic", severity="error",
                              rule="E000", message=f"语法错误：{e.msg}",
                              suggestion="先修复语法再提交")
            ]
        lines = src.splitlines()
        out += self._scan_ast(path, tree)
        out += self._scan_text(path, lines)
        out += self._scan_data_science(path, src, lines)
        return out

    # ---- 面向数据科学脚本的专用规则（D 系列，映射可靠性高，允许回写 L2） ----
    def _scan_data_science(self, path: str, src: str, lines: list[str]) -> list[ReviewFinding]:
        out: list[ReviewFinding] = []
        split_line = next(
            (i for i, ln in enumerate(lines, 1) if re.search(r"train_test_split\s*\(", ln)), None
        )
        # D001 划分之前就 fit 了预处理器 —— 典型数据泄漏
        for i, ln in enumerate(lines, 1):
            if re.search(r"\.(fit|fit_transform)\s*\(", ln) and \
                    re.search(r"(scaler|encoder|imputer|vectorizer|pca|normalizer)", ln, re.I):
                if split_line and i < split_line:
                    out.append(ReviewFinding(
                        file=path, line=i, category="logic", severity="error", rule="D001",
                        message="在划分数据集之前对全量数据做了 fit，测试集信息已泄漏到训练过程",
                        suggestion="先 train_test_split，再仅用训练集 fit，对测试集只 transform"))
        # D002 用测试集调阈值或选模型
        for i, ln in enumerate(lines, 1):
            if re.search(r"(threshold|best_|grid|search|select)", ln, re.I) and \
                    re.search(r"\b(y_test|X_test|y_te|X_te|test_x|test_y)\b", ln):
                out.append(ReviewFinding(
                    file=path, line=i, category="logic", severity="error", rule="D002",
                    message="在测试集上调阈值或选模型，评估结果会系统性偏乐观",
                    suggestion="用验证集或交叉验证做选择，测试集只在最后用一次"))
        # D003 不平衡数据只报准确率
        has_acc = re.search(r"accuracy_score|\.score\s*\(", src)
        has_other = re.search(r"(f1_score|recall_score|precision_score|roc_auc|confusion_matrix"
                              r"|classification_report|average_precision)", src)
        hints_imbalance = re.search(r"(imbalance|不平衡|class_weight|SMOTE|少数类|正例稀少)",
                                    src, re.I)
        if has_acc and not has_other and hints_imbalance:
            out.append(ReviewFinding(
                file=path, line=has_acc.start() and src[: has_acc.start()].count("\n") + 1,
                category="logic", severity="warn", rule="D003",
                message="数据存在不平衡迹象却只报告了准确率",
                suggestion="补充精确率/召回率/F1 或 PR-AUC，并说明业务上更怕漏检还是误检"))
        # D004 未固定随机性
        uses_random = re.search(r"(train_test_split|np\.random|torch\.|shuffle\s*=\s*True)", src)
        fixed = re.search(r"(random_state\s*=|manual_seed|np\.random\.seed|set_seed)", src)
        if uses_random and not fixed:
            out.append(ReviewFinding(
                file=path, line=1, category="design", severity="warn", rule="D004",
                message="存在随机性但未固定随机种子，实验结果无法复现",
                suggestion="显式设置 random_state / manual_seed，并在报告中记录"))
        return out

    def _scan_ast(self, path: str, tree: ast.AST) -> list[ReviewFinding]:
        out: list[ReviewFinding] = []
        imported: dict[str, int] = {}
        used: set[str] = set()

        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler) and node.type is None:
                out.append(ReviewFinding(
                    file=path, line=node.lineno, category="logic", severity="warn", rule="E001",
                    message="裸 except 会吞掉所有异常，包括 KeyboardInterrupt",
                    suggestion="捕获具体异常类型，或至少 except Exception 并记录日志"))
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                out += self._check_func(path, node)
            if isinstance(node, ast.Compare):
                for op, cmp in zip(node.ops, node.comparators):
                    if isinstance(op, (ast.Eq, ast.NotEq)) and isinstance(cmp, ast.Constant) \
                            and cmp.value is None:
                        out.append(ReviewFinding(
                            file=path, line=node.lineno, category="style", severity="info",
                            rule="E003", message="与 None 比较应使用 is / is not",
                            suggestion="改为 `x is None`"))
            if isinstance(node, ast.Import):
                for a in node.names:
                    imported[(a.asname or a.name).split(".")[0]] = node.lineno
            elif isinstance(node, ast.ImportFrom):
                for a in node.names:
                    imported[a.asname or a.name] = node.lineno
            elif isinstance(node, ast.Name):
                used.add(node.id)
            elif isinstance(node, ast.Attribute):
                base = node
                while isinstance(base, ast.Attribute):
                    base = base.value
                if isinstance(base, ast.Name):
                    used.add(base.id)

        for name, line in imported.items():
            if name not in used and name != "*":
                out.append(ReviewFinding(
                    file=path, line=line, category="style", severity="info", rule="E007",
                    message=f"导入未使用：{name}", suggestion="删除无用导入"))
        return out

    def _check_func(self, path: str, node: ast.FunctionDef) -> list[ReviewFinding]:
        out: list[ReviewFinding] = []
        # 可变默认参数
        for d in node.args.defaults + [d for d in node.args.kw_defaults if d]:
            if isinstance(d, (ast.List, ast.Dict, ast.Set)):
                out.append(ReviewFinding(
                    file=path, line=d.lineno, category="logic", severity="error", rule="E002",
                    message=f"函数 {node.name} 使用可变对象作默认参数，会跨调用共享状态",
                    suggestion="默认值改为 None，在函数体内初始化"))
        # 函数长度
        end = getattr(node, "end_lineno", node.lineno)
        if end - node.lineno > 60:
            out.append(ReviewFinding(
                file=path, line=node.lineno, category="design", severity="warn", rule="E004",
                message=f"函数 {node.name} 长度 {end - node.lineno} 行，职责可能过多",
                suggestion="按职责拆分为若干小函数"))
        # 圈复杂度
        cx = 1 + sum(
            1 for n in ast.walk(node)
            if isinstance(n, (ast.If, ast.For, ast.While, ast.And, ast.Or,
                              ast.ExceptHandler, ast.BoolOp))
        )
        if cx > 12:
            out.append(ReviewFinding(
                file=path, line=node.lineno, category="design", severity="warn", rule="E005",
                message=f"函数 {node.name} 圈复杂度约 {cx}，分支过密",
                suggestion="提取条件为独立函数，或用查表替代长分支"))
        # 缺文档
        if not ast.get_docstring(node) and not node.name.startswith("_"):
            out.append(ReviewFinding(
                file=path, line=node.lineno, category="doc", severity="info", rule="E006",
                message=f"公开函数 {node.name} 缺少 docstring",
                suggestion="补一句话说明用途、输入与返回"))
        # 嵌套循环深度
        depth = self._loop_depth(node)
        if depth >= 3:
            out.append(ReviewFinding(
                file=path, line=node.lineno, category="design", severity="warn", rule="E012",
                message=f"函数 {node.name} 存在 {depth} 层嵌套循环，复杂度可能失控",
                suggestion="评估时间复杂度，考虑向量化或换数据结构"))
        return out

    def _loop_depth(self, node: ast.AST, cur: int = 0) -> int:
        best = cur
        for ch in ast.iter_child_nodes(node):
            nxt = cur + 1 if isinstance(ch, (ast.For, ast.While)) else cur
            best = max(best, self._loop_depth(ch, nxt))
        return best

    def _scan_text(self, path: str, lines: list[str]) -> list[ReviewFinding]:
        out: list[ReviewFinding] = []
        secret = re.compile(
            r"(api[_-]?key|secret|password|token)\s*=\s*[\"'][^\"']{8,}[\"']", re.I
        )
        for i, ln in enumerate(lines, 1):
            if secret.search(ln):
                out.append(ReviewFinding(
                    file=path, line=i, category="security", severity="error", rule="E008",
                    message="疑似硬编码密钥", suggestion="移到环境变量或配置文件，并加入 .gitignore"))
            if len(ln) > 120:
                out.append(ReviewFinding(
                    file=path, line=i, category="style", severity="info", rule="E010",
                    message="单行超过 120 字符", suggestion="换行或提取中间变量"))
        return out


# ---------------------------------------------------------------- 规则→知识点映射
_RULE_MAP_CACHE: dict | None = None


def rule_kp_map() -> dict:
    """规则→知识点映射表。reliable=true 的才允许回写 L2。"""
    global _RULE_MAP_CACHE
    if _RULE_MAP_CACHE is not None:
        return _RULE_MAP_CACHE
    path = ROOT / "data" / "seed" / "rule_kp_map.yaml"
    data: dict = {}
    if path.exists():
        try:
            import yaml

            data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except Exception:
            data = {}
    _RULE_MAP_CACHE = data.get("rules", {})
    return _RULE_MAP_CACHE


def map_rule_to_kps(rule: str) -> tuple[list[int], bool]:
    m = rule_kp_map().get(rule)
    if not m:
        return [], False
    ids = []
    for code in m.get("kp_codes", []):
        kp = graph_repo.get_kp_by_code(code)
        if kp:
            ids.append(kp.id)
    return ids, bool(m.get("reliable")) and bool(ids)


# ---------------------------------------------------------------- 审查智能体
class ReviewAgent(Agent):
    name = "review"
    system_prompt = (
        "你是代码评审助教。硬约束：\n"
        "1) 静态检查结论是确定的，不要复述也不要推翻；\n"
        "2) 只就设计与逻辑给出建议，不得给出成绩或掌握度判断；\n"
        "3) 不确定的地方明说不确定。"
    )
    analyzer = StaticAnalyzer()

    def review_source(self, student_id: int, path: str, src: str,
                      project_id: int | None = None, use_llm: bool = True) -> AgentOutput:
        findings = self.analyzer.analyze_source(path, src)
        for f in findings:
            f.related_kp_ids, f.reliable_mapping = map_rule_to_kps(f.rule)

        ctx = retriever.grounded_context(
            f"{path} 代码评审 设计规范", intent="review", top_k=2
        )
        narrative, degraded = ("", False)
        if use_llm:
            narrative, degraded = self.express(
                {
                    "意图": "代码评审",
                    "文件": path,
                    "静态问题数": len(findings),
                    "问题摘要": "；".join(f"{f.rule} {f.message}" for f in findings[:6]),
                    "关注点": "模块职责、错误处理、可测试性",
                    "项目资料": ctx.context_text(1)[:500],
                    "建议": "先补齐边界条件的测试，再动结构",
                },
                max_tokens=500,
            )
        ids = self._persist(student_id, project_id, findings)
        return AgentOutput(
            agent=self.name,
            narrative=narrative,
            plan={
                "file": path,
                "findings": [f.to_dict() for f in findings],
                "finding_ids": ids,
                "n_reliable_mappings": sum(1 for f in findings if f.reliable_mapping),
                "writeback_policy": "仅 reliable 映射在教师采纳后回写 L2，宁缺毋滥",
            },
            citations=[c.to_dict() for c in ctx.citations],
            confidence=0.9 if findings else 0.5,
            evidence_count=len(findings),
            degraded=degraded,
        )

    def review_path(self, student_id: int, target: Path, project_id: int | None = None,
                    use_llm: bool = False) -> AgentOutput:
        files = [target] if target.is_file() else sorted(target.rglob("*.py"))
        all_findings, ids = [], []
        for f in files[:40]:
            try:
                src = f.read_text(encoding="utf-8")
            except Exception:
                continue
            out = self.review_source(student_id, str(f), src, project_id, use_llm=use_llm)
            all_findings += out.plan["findings"]
            ids += out.plan["finding_ids"]
        return AgentOutput(
            agent=self.name,
            plan={"files": len(files), "findings": all_findings, "finding_ids": ids},
            evidence_count=len(all_findings), confidence=0.9 if all_findings else 0.5,
        )

    def _persist(self, student_id: int, project_id: int | None,
                 findings: list[ReviewFinding]) -> list[int]:
        db, ids, now = get_db(), [], now_str()
        for f in findings:
            ids.append(db.execute(
                "INSERT INTO review_finding(student_id, project_id, file, line, category,"
                " severity, rule, message, suggestion, related_kp_ids, produced_by,"
                " teacher_action, wrote_back, created_at)"
                " VALUES(?,?,?,?,?,?,?,?,?,?,?,'pending',0,?)",
                (student_id, project_id, f.file, f.line, f.category, f.severity, f.rule,
                 f.message, f.suggestion, dumps(f.related_kp_ids), f.produced_by, now),
            ))
        return ids

    # ---------- 教师协同 ----------
    def teacher_action(self, finding_id: int, action: str, note: str = "") -> dict:
        """审查结果为建议：导师可采纳 / 修改 / 否决。否决记录用于优化规则。

        **只有被采纳、且规则映射可靠时，才通过 tracker 回写 L2。**
        """
        if action not in ("accepted", "modified", "rejected"):
            raise ValueError("非法的教师动作")
        db = get_db()
        r = db.query_one("SELECT * FROM review_finding WHERE id=?", (finding_id,))
        if not r:
            return {"error": "finding 不存在"}
        db.execute(
            "UPDATE review_finding SET teacher_action=? WHERE id=?", (action, finding_id)
        )
        wrote = []
        if action in ("accepted", "modified") and not r["wrote_back"]:
            kp_ids = loads(r["related_kp_ids"], [])
            _ids, reliable = map_rule_to_kps(r["rule"])
            if reliable and kp_ids:
                for kp in kp_ids:
                    tracker.record(
                        student_id=r["student_id"], event_type="review_finding", kp_id=kp,
                        is_correct=False, source="review",
                        source_ref=f"finding:{finding_id}",
                        payload={"rule": r["rule"], "message": r["message"],
                                 "teacher_action": action, "note": note},
                    )
                    wrote.append(kp)
                db.execute("UPDATE review_finding SET wrote_back=1 WHERE id=?", (finding_id,))
        return {
            "finding_id": finding_id, "action": action, "wrote_back_kp_ids": wrote,
            "note": "该规则未标注可靠映射，按'宁缺毋滥'不回写" if not wrote else "",
        }

    def list_findings(self, student_id: int | None = None, limit: int = 100) -> list[dict]:
        sql = "SELECT * FROM review_finding WHERE 1=1"
        params: list = []
        if student_id:
            sql += " AND student_id=?"
            params.append(student_id)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        rows = get_db().query(sql, params)
        for r in rows:
            r["related_kp_ids"] = loads(r["related_kp_ids"], [])
        return rows

    def rejection_stats(self) -> list[dict]:
        """否决记录用于优化规则——哪些规则最常被否决，就该改或该删。"""
        return get_db().query(
            "SELECT rule, COUNT(*) AS n,"
            " SUM(CASE WHEN teacher_action='rejected' THEN 1 ELSE 0 END) AS rejected"
            " FROM review_finding GROUP BY rule ORDER BY rejected DESC, n DESC"
        )
