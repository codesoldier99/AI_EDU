-- =====================================================================
-- 001_init  三层架构的持久化骨架
--   L1 graph      知识与能力图谱（可积累）
--   L2 state      学生状态 + 错误模式库（可积累·核心资产）
--      engagement 参与度派生态（可从事件流重算）
--   L3 侧只落日志与会话，不落"事实"
-- SQL 限定在 SQLite / PostgreSQL 双兼容子集内。
-- =====================================================================

-- ---------- L1 知识与能力图谱 ----------
CREATE TABLE IF NOT EXISTS course (
  id          INTEGER PRIMARY KEY,
  code        TEXT NOT NULL UNIQUE,
  name        TEXT NOT NULL,
  credit      REAL DEFAULT 0,
  term        TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS ability_module (
  id          INTEGER PRIMARY KEY,
  code        TEXT NOT NULL UNIQUE,          -- M1..M8
  name        TEXT NOT NULL,
  description TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS knowledge_point (
  id          INTEGER PRIMARY KEY,
  course_id   INTEGER NOT NULL REFERENCES course(id),
  code        TEXT NOT NULL UNIQUE,
  name        TEXT NOT NULL,
  description TEXT DEFAULT '',
  granularity TEXT DEFAULT 'atomic',         -- atomic | composite
  kp_type     TEXT DEFAULT 'concept',        -- concept | method | skill | tool
  difficulty  REAL DEFAULT 0.5,
  unit        TEXT DEFAULT '',               -- 章节归属，仅作展示用，不参与调度
  created_at  TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_kp_course ON knowledge_point(course_id);

-- 依赖 DAG：禁自环；成环由应用层 detect_cycles 在导入时拦截
CREATE TABLE IF NOT EXISTS prerequisite (
  from_kp_id  INTEGER NOT NULL REFERENCES knowledge_point(id),
  to_kp_id    INTEGER NOT NULL REFERENCES knowledge_point(id),
  edge_type   TEXT NOT NULL DEFAULT 'prereq', -- prereq | contains | related
  strength    REAL NOT NULL DEFAULT 1.0,
  PRIMARY KEY (from_kp_id, to_kp_id, edge_type),
  CHECK (from_kp_id <> to_kp_id)
);
CREATE INDEX IF NOT EXISTS idx_prereq_to ON prerequisite(to_kp_id);

CREATE TABLE IF NOT EXISTS kp_ability_link (
  kp_id       INTEGER NOT NULL REFERENCES knowledge_point(id),
  module_id   INTEGER NOT NULL REFERENCES ability_module(id),
  weight      REAL NOT NULL DEFAULT 1.0,
  PRIMARY KEY (kp_id, module_id)
);

-- ---------- 项目与任务（拉取式的前提） ----------
CREATE TABLE IF NOT EXISTS project (
  id          INTEGER PRIMARY KEY,
  code        TEXT NOT NULL UNIQUE,
  name        TEXT NOT NULL,
  ptype       TEXT NOT NULL,                 -- robot | vision | llm_app | data | edge
  adapter_key TEXT NOT NULL DEFAULT 'demo',
  description TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS project_task (
  id          INTEGER PRIMARY KEY,
  project_id  INTEGER NOT NULL REFERENCES project(id),
  code        TEXT NOT NULL UNIQUE,
  name        TEXT NOT NULL,
  parent_id   INTEGER REFERENCES project_task(id),
  milestone   INTEGER NOT NULL DEFAULT 0,
  seq         INTEGER NOT NULL DEFAULT 0,
  description TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_task_project ON project_task(project_id);

-- ★ 拉取式核心：任务需要哪些知识点（由导师标注，不由大模型生成）
CREATE TABLE IF NOT EXISTS task_kp_link (
  task_id     INTEGER NOT NULL REFERENCES project_task(id),
  kp_id       INTEGER NOT NULL REFERENCES knowledge_point(id),
  necessity   TEXT NOT NULL DEFAULT 'required',  -- required | helpful
  annotated_by TEXT NOT NULL DEFAULT 'teacher',
  PRIMARY KEY (task_id, kp_id)
);

CREATE TABLE IF NOT EXISTS task_dependency (
  from_task_id INTEGER NOT NULL REFERENCES project_task(id),
  to_task_id   INTEGER NOT NULL REFERENCES project_task(id),
  PRIMARY KEY (from_task_id, to_task_id),
  CHECK (from_task_id <> to_task_id)
);

-- ---------- L2 学生状态 ----------
CREATE TABLE IF NOT EXISTS student (
  id          INTEGER PRIMARY KEY,
  sid         TEXT NOT NULL UNIQUE,
  name        TEXT NOT NULL,
  cohort      TEXT NOT NULL DEFAULT '',
  klass       TEXT NOT NULL DEFAULT '',
  enrolled_at TEXT DEFAULT (datetime('now'))
);

-- 只追加事件流：任何状态都可由此重算，是可审计性的基础
CREATE TABLE IF NOT EXISTS learning_event (
  id          INTEGER PRIMARY KEY,
  student_id  INTEGER NOT NULL REFERENCES student(id),
  kp_id       INTEGER REFERENCES knowledge_point(id),
  event_type  TEXT NOT NULL,   -- quiz|homework|exam|practice|escalation|ask_turn
                               -- |review_finding|project_signal|milestone|first_run
  is_correct  INTEGER,         -- NULL 表示该事件不参与掌握度更新
  source      TEXT NOT NULL DEFAULT '',   -- 事件来源模块
  source_ref  TEXT NOT NULL DEFAULT '',
  occurred_at TEXT NOT NULL,
  payload     TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_event_student ON learning_event(student_id, occurred_at);
CREATE INDEX IF NOT EXISTS idx_event_kp ON learning_event(kp_id);
CREATE INDEX IF NOT EXISTS idx_event_type ON learning_event(event_type);

CREATE TABLE IF NOT EXISTS mastery_state (
  student_id    INTEGER NOT NULL REFERENCES student(id),
  kp_id         INTEGER NOT NULL REFERENCES knowledge_point(id),
  p_mastery     REAL NOT NULL,
  confidence    REAL NOT NULL DEFAULT 0,
  evidence_count INTEGER NOT NULL DEFAULT 0,
  last_event_id INTEGER REFERENCES learning_event(id),   -- 掌握度必须可追溯到事件
  updated_at    TEXT NOT NULL,
  PRIMARY KEY (student_id, kp_id)
);

CREATE TABLE IF NOT EXISTS ability_profile (
  student_id  INTEGER NOT NULL REFERENCES student(id),
  module_id   INTEGER NOT NULL REFERENCES ability_module(id),
  level       REAL NOT NULL DEFAULT 0,
  confidence  REAL NOT NULL DEFAULT 0,
  updated_at  TEXT NOT NULL,
  PRIMARY KEY (student_id, module_id)
);

-- BKT 四参数按知识点存，冷启动用课程级默认值
CREATE TABLE IF NOT EXISTS bkt_param (
  kp_id       INTEGER PRIMARY KEY REFERENCES knowledge_point(id),
  p_init      REAL NOT NULL,
  p_transit   REAL NOT NULL,
  p_slip      REAL NOT NULL,
  p_guess     REAL NOT NULL,
  fitted_n    INTEGER NOT NULL DEFAULT 0,
  updated_at  TEXT DEFAULT (datetime('now'))
);

-- ---------- engagement 派生态（禁止写 state） ----------
CREATE TABLE IF NOT EXISTS streak_state (
  student_id     INTEGER PRIMARY KEY REFERENCES student(id),
  current_days   INTEGER NOT NULL DEFAULT 0,
  longest_days   INTEGER NOT NULL DEFAULT 0,
  last_active_date TEXT DEFAULT '',
  total_active_days INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS achievement (
  id          INTEGER PRIMARY KEY,
  student_id  INTEGER NOT NULL REFERENCES student(id),
  kind        TEXT NOT NULL,   -- breakthrough | milestone | comeback | first_run | streak
  ref_id      TEXT NOT NULL DEFAULT '',
  detail      TEXT NOT NULL DEFAULT '',
  earned_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ach_student ON achievement(student_id, earned_at);

-- ---------- 错误模式库（最有价值的资产） ----------
CREATE TABLE IF NOT EXISTS error_pattern (
  id            INTEGER PRIMARY KEY,
  kp_id         INTEGER NOT NULL REFERENCES knowledge_point(id),
  signature     TEXT NOT NULL,
  description   TEXT NOT NULL,
  root_cause_kp_id INTEGER REFERENCES knowledge_point(id),
  occurrence_count INTEGER NOT NULL DEFAULT 0,
  first_seen    TEXT NOT NULL,
  last_seen     TEXT NOT NULL,
  teacher_verified INTEGER NOT NULL DEFAULT 0,
  teacher_note  TEXT NOT NULL DEFAULT ''
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_pattern_sig ON error_pattern(kp_id, signature);

CREATE TABLE IF NOT EXISTS error_instance (
  id          INTEGER PRIMARY KEY,
  pattern_id  INTEGER REFERENCES error_pattern(id),
  student_id  INTEGER NOT NULL REFERENCES student(id),
  event_id    INTEGER REFERENCES learning_event(id),
  kp_id       INTEGER NOT NULL REFERENCES knowledge_point(id),
  raw_text    TEXT NOT NULL DEFAULT '',
  created_at  TEXT NOT NULL
);

-- ---------- 项目信号 ----------
CREATE TABLE IF NOT EXISTS project_signal (
  id          INTEGER PRIMARY KEY,
  project_id  INTEGER NOT NULL REFERENCES project(id),
  student_id  INTEGER NOT NULL REFERENCES student(id),
  signal_class TEXT NOT NULL,   -- code_commit|build_test|runtime|doc_delivery|collaboration
  metric      TEXT NOT NULL DEFAULT '',
  value       REAL NOT NULL DEFAULT 0,
  occurred_at TEXT NOT NULL,
  raw_ref     TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_signal_student ON project_signal(student_id, occurred_at);

CREATE TABLE IF NOT EXISTS project_member (
  project_id  INTEGER NOT NULL REFERENCES project(id),
  student_id  INTEGER NOT NULL REFERENCES student(id),
  role        TEXT NOT NULL DEFAULT 'member',
  PRIMARY KEY (project_id, student_id)
);

CREATE TABLE IF NOT EXISTS task_assignment (
  task_id     INTEGER NOT NULL REFERENCES project_task(id),
  student_id  INTEGER NOT NULL REFERENCES student(id),
  status      TEXT NOT NULL DEFAULT 'todo',  -- todo | doing | done | blocked
  updated_at  TEXT NOT NULL,
  PRIMARY KEY (task_id, student_id)
);

-- ---------- 审查结果 ----------
CREATE TABLE IF NOT EXISTS review_finding (
  id          INTEGER PRIMARY KEY,
  student_id  INTEGER NOT NULL REFERENCES student(id),
  project_id  INTEGER REFERENCES project(id),
  file        TEXT NOT NULL DEFAULT '',
  line        INTEGER NOT NULL DEFAULT 0,
  category    TEXT NOT NULL,   -- style|logic|design|doc|security
  severity    TEXT NOT NULL,   -- info|warn|error
  rule        TEXT NOT NULL DEFAULT '',
  message     TEXT NOT NULL,
  suggestion  TEXT NOT NULL DEFAULT '',
  related_kp_ids TEXT NOT NULL DEFAULT '[]',
  produced_by TEXT NOT NULL DEFAULT 'static',   -- static | llm
  teacher_action TEXT NOT NULL DEFAULT 'pending', -- pending|accepted|modified|rejected
  wrote_back  INTEGER NOT NULL DEFAULT 0,
  created_at  TEXT NOT NULL
);

-- ---------- L3 侧：会话与调用日志（不是事实来源） ----------
CREATE TABLE IF NOT EXISTS asking_session (
  id          INTEGER PRIMARY KEY,
  student_id  INTEGER NOT NULL REFERENCES student(id),
  kp_id       INTEGER NOT NULL REFERENCES knowledge_point(id),
  entry_kp_id INTEGER REFERENCES knowledge_point(id),
  style       TEXT NOT NULL DEFAULT '',
  depth       INTEGER NOT NULL DEFAULT 1,
  attempts    INTEGER NOT NULL DEFAULT 0,
  escalation_level INTEGER NOT NULL DEFAULT 0,
  status      TEXT NOT NULL DEFAULT 'open',
  created_at  TEXT NOT NULL,
  updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS asking_turn (
  id          INTEGER PRIMARY KEY,
  session_id  INTEGER NOT NULL REFERENCES asking_session(id),
  role        TEXT NOT NULL,   -- system | student
  text        TEXT NOT NULL,
  citations   TEXT NOT NULL DEFAULT '[]',
  created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS llm_call_log (
  id          INTEGER PRIMARY KEY,
  purpose     TEXT NOT NULL,
  model_name  TEXT NOT NULL,
  model_version TEXT NOT NULL DEFAULT '',
  prompt_hash TEXT NOT NULL,
  tokens_in   INTEGER NOT NULL DEFAULT 0,
  tokens_out  INTEGER NOT NULL DEFAULT 0,
  latency_ms  INTEGER NOT NULL DEFAULT 0,
  degraded    INTEGER NOT NULL DEFAULT 0,
  created_at  TEXT NOT NULL
);

-- ---------- RAG 三库 ----------
CREATE TABLE IF NOT EXISTS doc_chunk (
  id          INTEGER PRIMARY KEY,
  kb          TEXT NOT NULL,   -- course | project | program
  ref         TEXT NOT NULL,
  title       TEXT NOT NULL DEFAULT '',
  text        TEXT NOT NULL,
  kp_codes    TEXT NOT NULL DEFAULT '[]',
  embedding   TEXT NOT NULL DEFAULT '[]',     -- 迁 pgvector 时改列类型即可
  embed_model TEXT NOT NULL DEFAULT '',
  embed_version TEXT NOT NULL DEFAULT '',
  created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chunk_kb ON doc_chunk(kb);

-- ---------- 教学侧 ----------
CREATE TABLE IF NOT EXISTS teacher (
  id          INTEGER PRIMARY KEY,
  code        TEXT NOT NULL UNIQUE,
  name        TEXT NOT NULL,
  klasses     TEXT NOT NULL DEFAULT '[]'      -- 可见班级白名单，越权由 API 层拦截
);

CREATE TABLE IF NOT EXISTS report_open_log (
  id          INTEGER PRIMARY KEY,
  teacher_code TEXT NOT NULL,
  report_kind TEXT NOT NULL,
  opened_at   TEXT NOT NULL
);
