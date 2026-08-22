-- 项目信号去重：同一条痕迹只能记一次
--
-- 暴露过程：`make signals` 跑第二遍，DAC 项目的信号从 15 条变成 30 条。
-- persist_signals 是裸 INSERT，没有任何唯一性约束，重跑一次就翻一倍。
--
-- 为什么这不是小问题：五类信号会折算成 M1–M8 能力画像。
-- 提交数翻倍 = 这个学生的"投入"凭空翻倍，而且看不出来——
-- 报表上只会显示他很勤奋。运维手一抖多跑一次采集，画像就歪了。
--
-- 同一条信号的身份 = 项目 + 学生 + 类别 + 指标 + 原始引用（commit sha / run id）。
-- 先清掉已有重复（每组只留 id 最小的那条），再加唯一索引把门关上。

DELETE FROM project_signal WHERE id NOT IN (
  SELECT MIN(id) FROM project_signal
  GROUP BY project_id, student_id, signal_class, metric, raw_ref, occurred_at
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_signal_identity
  ON project_signal(project_id, student_id, signal_class, metric, raw_ref, occurred_at);
