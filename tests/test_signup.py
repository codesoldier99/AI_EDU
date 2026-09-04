"""教师报名：入口要足够低，出口不能顺手把人的联系方式漏出去。

这个模块不参与任何教学判定，所以测试也只盯两件事：
校验拦不拦得住脏数据；公开接口会不会把名单吐出来。
"""
from __future__ import annotations

import unittest

from base import DBTestCase

from packages.signup import repo


class TestSignupValidation(DBTestCase):
    def test_records_name_roles_and_contact(self):
        sid = repo.create("张三", "智能科学与技术教研室", "13800000000",
                          ["mentor", "dev"], "带一个光学检测项目", source="faculty-meeting")
        row = next(r for r in repo.list_all() if r["id"] == sid)
        self.assertEqual(row["name"], "张三")
        self.assertEqual(row["roles"], ["mentor", "dev"])
        self.assertEqual(row["role_names"], ["项目导师", "一起做开发"])
        self.assertEqual(row["source"], "faculty-meeting")

    def test_name_is_required(self):
        with self.assertRaises(repo.SignupError):
            repo.create("   ", roles=["mentor"])

    def test_at_least_one_role_is_required(self):
        with self.assertRaises(repo.SignupError):
            repo.create("李四", roles=[])

    def test_unknown_roles_are_dropped_not_stored(self):
        """前端可以改，接口不能信。不认识的方式直接丢掉，
        丢完一个不剩就等于没选——不能让脏枚举混进按方式分组的联系名单。"""
        with self.assertRaises(repo.SignupError):
            repo.create("王五", roles=["admin", "root"])
        sid = repo.create("赵六", roles=["mentor", "nonsense"])
        row = next(r for r in repo.list_all() if r["id"] == sid)
        self.assertEqual(row["roles"], ["mentor"])

    def test_overlong_field_is_refused_rather_than_silently_truncated(self):
        """截断会让老师以为自己写的东西存进去了。宁可当场报错。"""
        with self.assertRaises(repo.SignupError):
            repo.create("张三", roles=["mentor"], topic="项" * 400)


class TestSignupStatsLeakNothing(DBTestCase):
    def test_public_stats_carry_counts_but_no_names_or_contacts(self):
        repo.create("张三", "教研室A", "13800000000", ["mentor"])
        repo.create("李四", "教研室B", "li@example.edu", ["review", "dev"])
        s = repo.stats()
        self.assertEqual(s["total"], 2)
        self.assertEqual(s["by_role"], {"mentor": 1, "review": 1, "dev": 1})
        blob = repr(s)
        for secret in ("张三", "李四", "13800000000", "li@example.edu", "教研室A"):
            self.assertNotIn(secret, blob, f"公开计数里漏出了 {secret}")


if __name__ == "__main__":
    unittest.main()
