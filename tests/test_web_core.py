"""把前端纯逻辑的 Node 测试接进统一测试入口。

3D 视图的渲染壳没法在无头环境里验证（本机无可用的无头浏览器），
但布局物理、色阶、降噪策略这些**确定性逻辑**必须测——
这和后端把 plan()（可测）与 express()（不可测）分开是同一个道理。

没装 Node 时自动跳过，不影响零依赖的主线。
"""
from __future__ import annotations

import shutil
import subprocess
import unittest

from base import ROOT


class TestWebCore(unittest.TestCase):
    @unittest.skipIf(not shutil.which("node"), "未安装 Node，跳过前端逻辑测试")
    def test_kg_core(self):
        r = subprocess.run(
            ["node", str(ROOT / "tests" / "web_core.test.mjs")],
            capture_output=True, text=True, cwd=str(ROOT), timeout=120,
        )
        if r.returncode != 0:
            self.fail(f"前端逻辑测试失败：\n{r.stdout}\n{r.stderr}")
        self.assertIn("个用例通过", r.stdout)


if __name__ == "__main__":
    unittest.main()
