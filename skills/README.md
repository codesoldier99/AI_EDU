# 教学技能包（skills/）

把一位教师"怎么问、怎么出题"的手感写成一个 Markdown 文件，系统就会用它。
不需要改代码，不需要重启——存盘即生效。

## 写法

一个 `.md` 文件 = 一个技能包。开头是 frontmatter，之后是正文（给表达层看的说法）。

```markdown
---
name: counterexample-first
kind: asking_style          # asking_style | quiz_template | review_note
style: 反例质疑              # 仅 asking_style 用：绑定到哪一种追问方式
applies_to_kp_type: [concept, method]   # 可选，留空表示不限
priority: 10                # 同类多个包时的先后
author: 张老师
description: 先用一个会失败的例子逼出边界条件
---

正文：你希望助教在这一类追问里说些什么、避开什么。
```

## 三条边界（重要）

1. **技能包不能决定"算不算掌握"。** 掌握度只由 BKT 依据事件产生。
   往这个目录里放任何东西都改变不了这一点。
2. **技能包不能让追问直接给答案。** 降级梯度由 `AskingStrategy` 控制并留痕，
   正文里写"直接告诉他答案"不会生效。
3. **不从网上安装。** 本目录进 git，教师可读、可评审、可回滚。
   演示环境经常没有外网，这也是为什么不做远程 hub。

## 当前可用的 kind

| kind | 影响谁 | 影响什么 |
|---|---|---|
| `asking_style` | `packages/agents/asking.py` | 选定追问方式后，注入这一类问法的表达要点 |
| `quiz_template` | `packages/agents/quiz.py` | 起草题目时的出题要求（题面风格、干扰项设计） |
| `review_note` | 教师侧展示 | 评审关注点提醒，不参与任何判定 |

查看已装载的包：`make skills`（等价 `python3 aiedu.py skills`）。
