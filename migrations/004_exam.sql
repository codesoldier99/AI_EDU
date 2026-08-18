-- =====================================================================
-- 004_exam  在线考试：入学选拔 + 纵向测评的基线
--
-- 设计见 docs/measurement-plan.md。三件事决定了这张 schema 的形状：
--   1. 分数线要能严格切开 128 人 -> 必须记录逐题得分与并列裁决键
--   2. 概念锚题两年内要反复重测 -> 锚题与选拔题分区，锚题不进组卷池
--   3. 结论要经得起第三方核查   -> 判分结果仍走 learning_event，可重算
--
-- 为什么 exam_answer 可以是一张普通表（会被 UPDATE），而不违反"只追加"铁律：
--   它是**卷面原件**，是学生交上来的那张纸，属于原始输入，不是系统的判断。
--   考试中每 15 秒自动保存一次，把每次按键都写进只追加事件流是荒唐的。
--   交卷判分之后产生的**证据**照旧写 learning_event，掌握度仍由事件流折叠得到。
--   同一条界线在 study_note / asking_turn 上已经划过一次：原始输入可改，判断不可改。
-- =====================================================================

CREATE TABLE IF NOT EXISTS exam (
  id            INTEGER PRIMARY KEY,
  code          TEXT NOT NULL UNIQUE,
  title         TEXT NOT NULL,
  duration_min  INTEGER NOT NULL DEFAULT 60,
  opens_at      TEXT,
  closes_at     TEXT,
  status        TEXT NOT NULL DEFAULT 'draft',   -- draft | published | closed
  total_score   REAL NOT NULL DEFAULT 0,
  -- 冻结的试卷快照：题目 id、分区、分值、顺序。发布后不得再改，
  -- 否则同一场考试的不同考生面对的不是同一张卷子。
  paper         TEXT NOT NULL DEFAULT '{}',
  shuffle       INTEGER NOT NULL DEFAULT 1,      -- 是否按考生打乱题序
  note          TEXT NOT NULL DEFAULT '',
  created_at    TEXT NOT NULL,
  published_at  TEXT
);

-- 一次性准考凭据。学生不用记密码，考前发一张条：学号 + 6 位口令。
CREATE TABLE IF NOT EXISTS exam_ticket (
  id            INTEGER PRIMARY KEY,
  exam_id       INTEGER NOT NULL REFERENCES exam(id),
  student_id    INTEGER NOT NULL REFERENCES student(id),
  ticket        TEXT NOT NULL,
  seat_no       TEXT NOT NULL DEFAULT '',
  issued_at     TEXT NOT NULL,
  used_at       TEXT,
  UNIQUE (exam_id, student_id),
  UNIQUE (exam_id, ticket)
);

-- 考试会话。截止时间由**服务端**在开考瞬间算定，之后一律以服务端为准：
-- 客户端时钟不可信，这是考试系统与教学系统最本质的差别。
CREATE TABLE IF NOT EXISTS exam_session (
  id            INTEGER PRIMARY KEY,
  exam_id       INTEGER NOT NULL REFERENCES exam(id),
  student_id    INTEGER NOT NULL REFERENCES student(id),
  token         TEXT NOT NULL UNIQUE,
  started_at    TEXT NOT NULL,
  deadline_at   TEXT NOT NULL,
  submitted_at  TEXT,
  status        TEXT NOT NULL DEFAULT 'open',    -- open | submitted | expired | voided
  item_order    TEXT NOT NULL DEFAULT '[]',
  client_ip     TEXT NOT NULL DEFAULT '',
  user_agent    TEXT NOT NULL DEFAULT '',
  -- 判分结果。A=概念锚题（纵向可比），B=选拔专用（考后可讲评）
  score_a       REAL,
  score_b       REAL,
  total_score   REAL,
  n_pending     INTEGER NOT NULL DEFAULT 0,      -- 待人工判分的题数
  graded_at     TEXT,
  UNIQUE (exam_id, student_id)
);
CREATE INDEX IF NOT EXISTS idx_exam_session_exam ON exam_session(exam_id, status);

-- 卷面原件：一人一题一行，考试中反复覆盖，交卷后冻结。
CREATE TABLE IF NOT EXISTS exam_answer (
  id            INTEGER PRIMARY KEY,
  session_id    INTEGER NOT NULL REFERENCES exam_session(id),
  question_id   INTEGER NOT NULL REFERENCES question(id),
  part          TEXT NOT NULL DEFAULT 'B',       -- A 锚题 | B 选拔题
  points        REAL NOT NULL DEFAULT 0,
  response      TEXT NOT NULL DEFAULT '',
  is_correct    INTEGER,                          -- NULL = 未判定（判不了 ≠ 判错）
  score         REAL,
  graded_by     TEXT NOT NULL DEFAULT '',
  grade_detail  TEXT NOT NULL DEFAULT '',
  answered_at   TEXT,
  updated_at    TEXT NOT NULL,
  UNIQUE (session_id, question_id)
);
CREATE INDEX IF NOT EXISTS idx_exam_answer_session ON exam_answer(session_id);

-- 交卷之后不许再改卷面。判分结果由教师复核时走"追加新事件"，不改这里。
CREATE TRIGGER IF NOT EXISTS exam_answer_frozen_after_submit
BEFORE UPDATE OF response ON exam_answer
WHEN (SELECT status FROM exam_session WHERE id = NEW.session_id) <> 'open'
BEGIN
  SELECT RAISE(ABORT, '该考试会话已交卷或已过期，卷面不可修改');
END;

-- 锚题不进日常组卷池：origin='anchor' 由 packages/quiz/selector.py 排除。
-- 这里只做一件事——让"这道题是不是锚题"可以被 SQL 直接问出来。
CREATE INDEX IF NOT EXISTS idx_question_origin ON question(origin, retired);
