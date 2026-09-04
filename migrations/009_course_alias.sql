-- 课程代码转发指针。
--
-- merge_course() 把旧课程代码合并进培养方案里的正式代码时，会 DELETE 掉旧的空壳行。
-- 这本身没错（同一门课不该有两行），但**删完没留转发指针**，于是所有还写着旧代码的
-- 地方——前端里的 /api/universe/ML、班级诊断、deck 脚本、老师收藏的深链——
-- 一夜之间全部 404 或静默返回"未找到课程"。知识宇宙在服务器上打不开就是这么来的。
--
-- 课程代码是身份，合并只是改名；改名后旧名字必须还能指回来。
CREATE TABLE IF NOT EXISTS course_alias (
  alias      TEXT PRIMARY KEY,                 -- 历史课程代码，如 ML
  course_id  INTEGER NOT NULL REFERENCES course(id) ON DELETE CASCADE,
  merged_at  TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_course_alias_course ON course_alias(course_id);

-- 回填 Phase 4.2 那次合并：ML > G18Z21022（见 data/seed/program_ai_zsb.yaml）。
-- 写成 SELECT 而不是写死 id，是因为各库里 course.id 不一样；
-- 目标课程不存在的库（例如只跑了 001 的空库）什么都不会插入。
INSERT OR IGNORE INTO course_alias(alias, course_id, merged_at)
SELECT 'ML', id, datetime('now') FROM course WHERE code = 'G18Z21022';
