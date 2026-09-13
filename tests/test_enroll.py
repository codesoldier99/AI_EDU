"""学生报名实验班：入口要足够低（只填姓名+手机号），出口不能漏名单。

跟 test_signup.py 是同一种关切，但这里多测一条手机号格式——
名单是要真拿去联系人的，格式不对的号码混进去等于白报名。
"""
from __future__ import annotations

import unittest

from base import DBTestCase

from packages.enroll import repo


class TestEnrollValidation(DBTestCase):
    def test_records_name_and_phone(self):
        eid = repo.create("张三", "138 0000 0000", source="qr-hall")
        row = next(r for r in repo.list_all() if r["id"] == eid)
        self.assertEqual(row["name"], "张三")
        self.assertEqual(row["phone"], "13800000000")   # 清洗掉空格后落库
        self.assertEqual(row["source"], "qr-hall")

    def test_name_is_required(self):
        with self.assertRaises(repo.EnrollError):
            repo.create("   ", "13800000000")

    def test_phone_is_required(self):
        with self.assertRaises(repo.EnrollError):
            repo.create("张三", "")

    def test_phone_must_look_like_a_mobile_number(self):
        for bad in ("123", "12345678901", "abcdefghijk", "10086000000"):
            with self.assertRaises(repo.EnrollError):
                repo.create("张三", bad)

    def test_phone_separators_are_stripped_not_rejected(self):
        eid = repo.create("李四", "138-0000-0000")
        row = next(r for r in repo.list_all() if r["id"] == eid)
        self.assertEqual(row["phone"], "13800000000")

    def test_overlong_name_is_refused_rather_than_silently_truncated(self):
        with self.assertRaises(repo.EnrollError):
            repo.create("张" * 41, "13800000000")


class TestEnrollStatsLeakNothing(DBTestCase):
    def test_public_stats_carry_count_but_no_names_or_phones(self):
        repo.create("张三", "13800000000")
        repo.create("李四", "13900000000")
        s = repo.stats()
        self.assertEqual(s["total"], 2)
        blob = repr(s)
        for secret in ("张三", "李四", "13800000000", "13900000000"):
            self.assertNotIn(secret, blob, f"公开计数里漏出了 {secret}")


if __name__ == "__main__":
    unittest.main()
