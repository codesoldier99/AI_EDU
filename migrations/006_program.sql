-- 培养方案：把知识点体系挂到真实的专业培养方案上
--
-- 为什么需要这一层：
-- 在这之前，"课程"只是知识点的一个分组标签（course.code='ML'）。
-- 但学分认定要回答的是"这门课能不能给他学分"，而课是写在**培养方案**里的：
-- 有课程代码、开课学期、学分、课程模块、考核方式，且同一门课在不同培养方案里
-- 学期与学分可以不同（本科第3学期开的课，专升本可能第1学期开）。
--
-- 所以拆成两层：
--   course          谁定义、谁维护这些知识点（知识点归属唯一）
--   program_course  这门课在某个培养方案里的位置（学期/学分/模块/考核）
-- 同一门 course 可以出现在多个 program 里，各自有各自的学期与学分。
--
-- 知识点归属仍然唯一（knowledge_point.course_id 不变）：
-- "向量与矩阵运算"由线性代数定义，机器学习只是**依赖**它，不能拿它去认定机器学习的学分。
-- 这条如果松掉，学分就会被重复计算——同一个知识点在三门课里各算一次。

CREATE TABLE IF NOT EXISTS program (
  id           INTEGER PRIMARY KEY,
  code         TEXT NOT NULL UNIQUE,        -- AI-ZSB-2026
  name         TEXT NOT NULL,
  level        TEXT NOT NULL DEFAULT '',    -- 专升本 / 本科 / 高职单招本科
  klass        TEXT NOT NULL DEFAULT '',    -- 院长实验班
  version      TEXT NOT NULL DEFAULT '',
  total_credit REAL NOT NULL DEFAULT 0,
  note         TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS program_course (
  program_id INTEGER NOT NULL REFERENCES program(id),
  course_id  INTEGER NOT NULL REFERENCES course(id),
  module     TEXT NOT NULL DEFAULT '',      -- 学科平台 / 专业核心 / 专业方向 / 集中实践
  semester   INTEGER NOT NULL DEFAULT 0,
  credit     REAL NOT NULL DEFAULT 0,
  hours      INTEGER NOT NULL DEFAULT 0,
  exam_type  TEXT NOT NULL DEFAULT '',      -- 考试 / 考查
  required   INTEGER NOT NULL DEFAULT 1,
  seq        INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (program_id, course_id)
);
CREATE INDEX IF NOT EXISTS idx_progcourse_sem ON program_course(program_id, semester);

-- 悬空知识点需求队列 —— 拉取式图谱建设的核心机制
--
-- 允许教师在标注任务时引用**尚不存在**的知识点代码。系统不报错、不忽略，
-- 而是把它登记成一条需求：谁需要它、哪个项目要用、被需求了几次。
--
-- 这样"下一门课先建哪一部分"就变成一个由数据回答的问题，
-- 而不是由教务排期回答的问题。40 门课不必平均用力——
-- 被 3 个项目拉过的那 20 个知识点，比没人拉的 300 个更该先建。
--
-- 知识点一旦按该代码建出来，需求自动闭合（status='built'）。
CREATE TABLE IF NOT EXISTS kp_demand (
  id           INTEGER PRIMARY KEY,
  code         TEXT NOT NULL,               -- 被引用但尚不存在的知识点代码
  course_code  TEXT NOT NULL DEFAULT '',    -- 由代码前缀推断出的归属课程
  demanded_by  TEXT NOT NULL,               -- task:T-DAC-3-5 / kp:DAC-05-10
  project_code TEXT NOT NULL DEFAULT '',
  kind         TEXT NOT NULL DEFAULT 'task_required',  -- task_required|task_helpful|prereq
  status       TEXT NOT NULL DEFAULT 'open',           -- open|built|dismissed
  first_seen   TEXT NOT NULL,
  UNIQUE (code, demanded_by)
);
CREATE INDEX IF NOT EXISTS idx_kpdemand_status ON kp_demand(status, code);

-- 课程代码前缀 → 课程：让悬空代码能被归到某门课名下
-- （PROB-NORM 属于概率论，LA-COND 属于线性代数）
CREATE TABLE IF NOT EXISTS course_prefix (
  prefix    TEXT PRIMARY KEY,
  course_id INTEGER NOT NULL REFERENCES course(id)
);
