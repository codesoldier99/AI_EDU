-- =====================================================================
-- 003  教师工作台 · 教学资产（教学大纲 / 授课计划 / 课件产物）
--
-- 这批表不是"可积累的学生状态"，是教师可修订的教学设计文档，版本语义用
-- version + superseded_by 自引用链表示，不套用 002 的 append-only 触发器
-- （那是给"不可篡改的证据流"用的；大纲/课件允许教师改稿，历史版本留痕即可）。
--
-- 知识点一律用 kp.code 引用，不复制 name/description——课程改了知识点描述，
-- 大纲/课件不需要跟着改一份自己的副本（铁律 2）。
-- =====================================================================

CREATE TABLE IF NOT EXISTS syllabus (
  id            INTEGER PRIMARY KEY,
  course_id     INTEGER NOT NULL REFERENCES course(id),
  version       INTEGER NOT NULL DEFAULT 1,
  superseded_by INTEGER REFERENCES syllabus(id),
  status        TEXT NOT NULL DEFAULT 'draft',      -- draft | teacher_confirmed
  content_json  TEXT NOT NULL DEFAULT '{}',          -- SyllabusPlan.to_dict()
  generator_version TEXT NOT NULL DEFAULT '',
  llm_model_version TEXT NOT NULL DEFAULT '',
  created_by    TEXT NOT NULL DEFAULT '',             -- 教师工号
  created_at    TEXT NOT NULL,
  confirmed_at  TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_syllabus_course ON syllabus(course_id, version);

CREATE TABLE IF NOT EXISTS teaching_plan (
  id            INTEGER PRIMARY KEY,
  syllabus_id   INTEGER NOT NULL REFERENCES syllabus(id),
  seq           INTEGER NOT NULL,
  title         TEXT NOT NULL,
  kp_codes_json TEXT NOT NULL DEFAULT '[]',
  duration_min  INTEGER NOT NULL DEFAULT 90,
  narrative     TEXT NOT NULL DEFAULT '',
  created_at    TEXT NOT NULL,
  UNIQUE(syllabus_id, seq)
);

CREATE TABLE IF NOT EXISTS courseware_deck (
  id                  INTEGER PRIMARY KEY,
  teaching_plan_id    INTEGER NOT NULL REFERENCES teaching_plan(id),
  deck_plan_json      TEXT NOT NULL,                  -- DeckPlan.to_dict()，内容与渲染分离
  artifact_type       TEXT NOT NULL DEFAULT 'pptx',
  render_tool         TEXT NOT NULL DEFAULT '',        -- officecli | builtin_stdlib
  render_tool_version TEXT NOT NULL DEFAULT '',
  file_path           TEXT NOT NULL DEFAULT '',
  degraded            INTEGER NOT NULL DEFAULT 0,
  kp_coverage_json    TEXT NOT NULL DEFAULT '[]',
  created_at          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_deck_plan ON courseware_deck(teaching_plan_id);
