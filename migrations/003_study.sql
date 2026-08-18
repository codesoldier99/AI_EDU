-- =====================================================================
-- 003_study  「学习工作台」：题库 / 组卷 / 分步解题 / 限域调研
--
-- 吸收自 DeepTutor（HKUDS，Apache-2.0）的能力面，取舍理由见 docs/deeptutor.md。
-- 落表的只有三类东西：
--   1. 可积累资产          question（题库，与错误模式库同级）
--   2. 会话型交互留痕      quiz_paper / solve_session / solve_step（同 asking_session 先例）
--   3. 学生的交付物        study_note（调研报告本身）
-- 刻意不落的：作答结果。作答即事件，写 learning_event，判分与掌握度由事件流折叠得到。
-- 多一张"成绩表"就多一个会和事件流对不上的地方（CLAUDE.md §6）。
-- SQL 限定在 SQLite / PostgreSQL 双兼容子集内。
-- =====================================================================

-- ---------- 题库：可积累资产 ----------
-- origin=llm 的题一律 teacher_verified=0，属草案；组卷默认只取已审题（宁缺毋滥）。
CREATE TABLE IF NOT EXISTS question (
  id          INTEGER PRIMARY KEY,
  kp_id       INTEGER NOT NULL REFERENCES knowledge_point(id),
  qtype       TEXT NOT NULL DEFAULT 'choice',   -- choice | numeric | short | code
  stem        TEXT NOT NULL,
  options     TEXT NOT NULL DEFAULT '[]',       -- JSON，choice 专用
  answer      TEXT NOT NULL DEFAULT '',
  tolerance   REAL NOT NULL DEFAULT 0.0,        -- numeric 专用，相对误差
  keywords    TEXT NOT NULL DEFAULT '[]',       -- JSON，short/code 的确定性判分依据
  rationale   TEXT NOT NULL DEFAULT '',
  difficulty  REAL NOT NULL DEFAULT 0.5,
  origin      TEXT NOT NULL DEFAULT 'llm',      -- llm | teacher | import
  grader      TEXT NOT NULL DEFAULT 'choice',   -- choice | numeric | keyword | manual
  citations   TEXT NOT NULL DEFAULT '[]',       -- JSON，出题所依据的教材片段
  signature   TEXT NOT NULL DEFAULT '',         -- 去重指纹
  teacher_verified INTEGER NOT NULL DEFAULT 0,
  retired     INTEGER NOT NULL DEFAULT 0,
  created_at  TEXT NOT NULL,
  UNIQUE (kp_id, signature)
);
CREATE INDEX IF NOT EXISTS idx_question_kp ON question(kp_id, teacher_verified, retired);

-- ---------- 组卷：一次测练的输入侧 ----------
-- 只记"发了哪些题、为什么发"；作答结果不在这里，在 learning_event。
CREATE TABLE IF NOT EXISTS quiz_paper (
  id          INTEGER PRIMARY KEY,
  student_id  INTEGER NOT NULL REFERENCES student(id),
  purpose     TEXT NOT NULL DEFAULT 'mixed',    -- retention | verify | gap | root_cause | mixed
  scope_ref   TEXT NOT NULL DEFAULT '',         -- task:<id> 之类
  question_ids TEXT NOT NULL DEFAULT '[]',      -- JSON
  reasons     TEXT NOT NULL DEFAULT '[]',       -- JSON，逐题的选中理由（可被教师质疑）
  created_at  TEXT NOT NULL,
  submitted_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_paper_student ON quiz_paper(student_id, created_at);

-- ---------- 分步解题：一次只揭示一步 ----------
CREATE TABLE IF NOT EXISTS solve_session (
  id          INTEGER PRIMARY KEY,
  student_id  INTEGER NOT NULL REFERENCES student(id),
  problem     TEXT NOT NULL,
  kp_ids      TEXT NOT NULL DEFAULT '[]',
  n_steps     INTEGER NOT NULL DEFAULT 0,
  cursor      INTEGER NOT NULL DEFAULT 0,       -- 当前停在第几步（0 基）
  attempts    INTEGER NOT NULL DEFAULT 0,       -- 当前这一步已试了几次
  escalation_level INTEGER NOT NULL DEFAULT 0,
  status      TEXT NOT NULL DEFAULT 'open',     -- open | done | closed
  citations   TEXT NOT NULL DEFAULT '[]',
  created_at  TEXT NOT NULL,
  updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS solve_step (
  id          INTEGER PRIMARY KEY,
  session_id  INTEGER NOT NULL REFERENCES solve_session(id),
  idx         INTEGER NOT NULL,
  kp_id       INTEGER REFERENCES knowledge_point(id),
  ask         TEXT NOT NULL,                    -- 这一步要学生做什么（不含结论）
  check_kind  TEXT NOT NULL DEFAULT 'text',     -- numeric | text
  expected    TEXT NOT NULL DEFAULT '',         -- 该步结论：未通过/未降级前禁止下发
  student_text TEXT NOT NULL DEFAULT '',
  passed      INTEGER,                          -- NULL=未判定
  revealed    INTEGER NOT NULL DEFAULT 0,       -- 是否已把 expected 交出去（降级留痕）
  created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_step_session ON solve_step(session_id, idx);

-- ---------- 限域调研的产出物 ----------
CREATE TABLE IF NOT EXISTS study_note (
  id          INTEGER PRIMARY KEY,
  student_id  INTEGER NOT NULL REFERENCES student(id),
  kind        TEXT NOT NULL DEFAULT 'research',
  topic       TEXT NOT NULL,
  body_md     TEXT NOT NULL DEFAULT '',
  citations   TEXT NOT NULL DEFAULT '[]',
  n_sections  INTEGER NOT NULL DEFAULT 0,
  n_unsourced INTEGER NOT NULL DEFAULT 0,       -- 无材料支撑的段落数，>0 必须显式告警
  project_id  INTEGER REFERENCES project(id),
  created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_note_student ON study_note(student_id, created_at);

-- 题库是资产，删题会带走历史证据的解释力：只允许标 retired，不允许 DELETE。
CREATE TRIGGER IF NOT EXISTS question_no_delete
BEFORE DELETE ON question
BEGIN
  SELECT RAISE(ABORT, 'question 是可积累资产，只能标 retired，禁止 DELETE');
END;
