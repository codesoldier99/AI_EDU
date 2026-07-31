"""代码审查：静态规则、知识点映射的"宁缺毋滥"、教师采纳后才回写 L2。"""
from __future__ import annotations

from base import DBTestCase

from packages.agents.review import ReviewAgent, StaticAnalyzer
from packages.graph import repo as g
from packages.state import repo as s

LEAKY = '''
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import train_test_split

API_KEY = "sk-abcdefghijklmnop"

def prep(path, cache={}):
    scaler = StandardScaler()
    X = scaler.fit_transform(load(path))
    X_tr, X_te, y_tr, y_te = train_test_split(X, y)
    best_threshold = pick(model, X_te, y_te)
    try:
        pass
    except:
        pass
    return X
'''


class TestStatic(DBTestCase):
    def test_detects_leakage_and_secret(self):
        f = StaticAnalyzer().analyze_source("a.py", LEAKY)
        rules = {x.rule for x in f}
        self.assertIn("D001", rules)   # 划分前 fit
        self.assertIn("D002", rules)   # 测试集调阈值
        self.assertIn("D004", rules)   # 未固定随机种子
        self.assertIn("E008", rules)   # 硬编码密钥
        self.assertIn("E001", rules)   # 裸 except
        self.assertIn("E002", rules)   # 可变默认参数

    def test_syntax_error_reported_once(self):
        f = StaticAnalyzer().analyze_source("a.py", "def (:")
        self.assertEqual(len(f), 1)
        self.assertEqual(f[0].rule, "E000")


class TestWriteback(DBTestCase):
    seed_course = True

    def setUp(self):
        super().setUp()
        # 让 D001 的映射目标存在于本测试图谱中
        import packages.agents.review as rv

        rv._RULE_MAP_CACHE = {
            "D001": {"kp_codes": ["A"], "reliable": True},
            "E006": {"kp_codes": [], "reliable": False},
        }
        self.agent = ReviewAgent()

    def tearDown(self):
        import packages.agents.review as rv

        rv._RULE_MAP_CACHE = None
        super().tearDown()

    def test_unreliable_mapping_never_writes_back(self):
        out = self.agent.review_source(self.student, "x.py", "def f():\n    return 1\n",
                                       use_llm=False)
        for fid in out.plan["finding_ids"]:
            r = self.agent.teacher_action(fid, "accepted")
            self.assertEqual(r["wrote_back_kp_ids"], [])

    def test_reliable_mapping_writes_back_only_after_acceptance(self):
        out = self.agent.review_source(self.student, "x.py", LEAKY, use_llm=False)
        d001 = [i for i, f in zip(out.plan["finding_ids"], out.plan["findings"])
                if f["rule"] == "D001"]
        self.assertTrue(d001)
        # 采纳之前不得有任何掌握度证据
        self.assertIsNone(s.get_mastery(self.student, self.kp["A"]))
        r = self.agent.teacher_action(d001[0], "accepted")
        self.assertEqual(r["wrote_back_kp_ids"], [self.kp["A"]])
        m = s.get_mastery(self.student, self.kp["A"])
        self.assertIsNotNone(m)
        # 回写走的是 tracker，因此一定有对应事件
        ev = s.list_events(student_id=self.student, event_type="review_finding")
        self.assertTrue(ev)

    def test_rejected_finding_does_not_write_back(self):
        out = self.agent.review_source(self.student, "x.py", LEAKY, use_llm=False)
        d001 = [i for i, f in zip(out.plan["finding_ids"], out.plan["findings"])
                if f["rule"] == "D001"][0]
        r = self.agent.teacher_action(d001, "rejected")
        self.assertEqual(r["wrote_back_kp_ids"], [])
        self.assertIsNone(s.get_mastery(self.student, self.kp["A"]))
