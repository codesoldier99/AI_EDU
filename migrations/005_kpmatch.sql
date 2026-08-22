-- 知识点自动匹配：候选草案队列
--
-- 为什么不直接写 task_kp_link：
-- 任务-知识点映射是学分认定与能力追溯的依据（见 data/seed/projects.yaml 开头那段），
-- 它必须由导师负责。模型能做的是**把 284 个知识点里最像的几个挑出来并给出证据**，
-- 省掉教师逐条翻图谱的时间——但"算不算数"这一步不能省。
-- 这与 question 表用 teacher_verified 隔离模型出的题是同一条规矩。
--
-- 因此本表是**队列**不是**事实**：只有 status='accepted' 的候选才会被写进
-- task_kp_link（annotated_by='teacher'），系统的其余部分只读 task_kp_link，
-- 对本表一无所知。删掉整张表，拉取式调度照常工作，只是教师要手工标注而已。

CREATE TABLE IF NOT EXISTS task_kp_candidate (
  id          INTEGER PRIMARY KEY,
  task_id     INTEGER NOT NULL REFERENCES project_task(id),
  kp_id       INTEGER NOT NULL REFERENCES knowledge_point(id),
  necessity   TEXT NOT NULL DEFAULT 'required',   -- required | helpful
  score       REAL NOT NULL DEFAULT 0,            -- 确定性检索得分（可复算）
  confidence  REAL NOT NULL DEFAULT 0,            -- 0–1，含与次名的差距
  evidence    TEXT NOT NULL DEFAULT '[]',         -- JSON：命中的词元，教师据此判断
  source_ref  TEXT NOT NULL DEFAULT '',           -- 证据来自哪段文字/哪个文件
  rationale   TEXT NOT NULL DEFAULT '',           -- 模型写的一句话解释，不参与判定
  matcher     TEXT NOT NULL DEFAULT 'lexical',    -- 匹配器版本，便于回溯与对比
  status      TEXT NOT NULL DEFAULT 'pending',    -- pending | accepted | rejected
  decided_by  TEXT NOT NULL DEFAULT '',
  decided_at  TEXT NOT NULL DEFAULT '',
  created_at  TEXT NOT NULL,
  UNIQUE (task_id, kp_id)
);

CREATE INDEX IF NOT EXISTS idx_kpcand_status ON task_kp_candidate(status, confidence DESC);
CREATE INDEX IF NOT EXISTS idx_kpcand_task ON task_kp_candidate(task_id, status);
