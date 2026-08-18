"""教师工作台 · 教学资产生成层（教学大纲 / 授课计划 / 课件）。

依赖方向：apps -> courseware -> {agents(仅复用 Agent.express), state(只读),
graph(只读/经 repo 写图谱), rag, llm}。禁止被 graph/state/engagement 反向依赖。

本包不建掌握度表、不直接写 mastery_state；评卷/作答判定（Phase B）
一律经 packages.state.tracker.record 写事件，见 tests/test_layering.py。

外部渲染二进制（OfficeCLI）的 subprocess 调用收敛在 officecli_render.py 一处，
其余文件不得出现 subprocess。
"""
