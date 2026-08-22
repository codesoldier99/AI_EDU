-- 集中实践环节的学分载体：把项目专有知识接到实践课程上
--
-- 背景：DAC 项目拉取的 158 个知识点里有 97 个（DMD 结构光、144 孔物料盘编号、
-- 这台设备的触发时序）在任何理论课程大纲里都找不到对应。
-- 在这之前系统的处理是"如实显示、不折算学分"——诚实，但对学生不公平：
-- 他确实做了这些事，只是这些事不归任何一门理论课管。
--
-- 培养方案里本来就有承接它的地方：**集中实践环节**（课程设计 / 专业综合实习 /
-- 毕业设计）。这类课程的学分本来就不是靠听课拿的，靠的是做完一个真项目。
--
-- 所以建立"项目 → 实践课程"的绑定：被绑定的实践课程，其覆盖范围 =
-- 自身知识点 ∪ 所绑定项目的项目知识域。
--
-- 仍然守住的两条：
--   1. 一个项目知识点只能绑到**一门**实践课（否则同一份工作认两次学分）
--   2. 实践环节的学分不能只看知识点覆盖度——里程碑验收是必须的，
--      系统给出覆盖度与里程碑两项事实，签字仍然是教师的事

CREATE TABLE IF NOT EXISTS practice_binding (
  project_code TEXT PRIMARY KEY,          -- 一个项目只能绑一门实践课，防止重复计学分
  course_id    INTEGER NOT NULL REFERENCES course(id),
  note         TEXT NOT NULL DEFAULT '',
  created_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_practice_course ON practice_binding(course_id);
