-- 求职智能体：把"学生两年内积累的百万级信息点"折叠成用人单位看得懂的三层结构。
--
-- 三层不是新发明的一套坐标系，而是对已有 L1/L2 资产的一次**只读投影**：
--   第一层  job_posting        岗位需求本身（教师/企业导师维护，配置数据，不是学生事实）
--   第二层  job_requirement    岗位需求的分解树（能力维度，可再分解，parent_id 自引用）
--   第三层  requirement_kp_link 需求叶子节点 → 具体知识点（复用 knowledge_point，
--                              不新建一份"能力点"坐标系——那会和图谱产生第二份真相）
--
-- 第三层往下，"学生在这个知识点上究竟做过什么"仍然只读 LearningEvent / project_signal，
-- 这里不建任何新的学生事实表——铁律 2：可积累的东西不得依赖可替换实现，
-- 而这三张表本身也不是学生事实，是和 course / project_task 一样的配置图谱。
--
-- 岗位需求 -> 知识点的映射由教师/企业导师在种子数据里直接给出（如同 task_kp_link
-- 由教师标注一样），不经过自动匹配候选队列——它不是学分认定依据，写错了也只影响
-- 一份简历措辞，不影响任何培养方案或考核，因此不必复用 task_kp_candidate 那一整套
-- 待审流程；但同一原则仍然成立：这张表只能由人工维护，模型不得写入。

CREATE TABLE IF NOT EXISTS job_posting (
  id          INTEGER PRIMARY KEY,
  code        TEXT NOT NULL UNIQUE,
  name        TEXT NOT NULL,
  company     TEXT NOT NULL DEFAULT '',
  description TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS job_requirement (
  id             INTEGER PRIMARY KEY,
  job_id         INTEGER NOT NULL REFERENCES job_posting(id),
  parent_id      INTEGER REFERENCES job_requirement(id),
  code           TEXT NOT NULL,
  name           TEXT NOT NULL,
  weight         REAL NOT NULL DEFAULT 1.0,
  signal_classes TEXT NOT NULL DEFAULT '',   -- JSON 列表：可选，工程/协作类需求没有对应知识点，
                                              -- 改用五类项目信号（见 packages/adapters/base.py）佐证
  seq            INTEGER NOT NULL DEFAULT 0,
  UNIQUE (job_id, code)
);
CREATE INDEX IF NOT EXISTS idx_jobreq_job ON job_requirement(job_id);
CREATE INDEX IF NOT EXISTS idx_jobreq_parent ON job_requirement(parent_id);

CREATE TABLE IF NOT EXISTS requirement_kp_link (
  requirement_id INTEGER NOT NULL REFERENCES job_requirement(id),
  kp_id          INTEGER NOT NULL REFERENCES knowledge_point(id),
  weight         REAL NOT NULL DEFAULT 1.0,
  PRIMARY KEY (requirement_id, kp_id)
);
CREATE INDEX IF NOT EXISTS idx_reqkp_kp ON requirement_kp_link(kp_id);
