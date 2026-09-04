-- 教师报名：全院教师会上扫码/点链接就能报名参与实验班建设。
--
-- 刻意做得很薄：它不参与任何教学判定，不写事件流，也不碰掌握度。
-- 报名表就是一张报名表——把它做进系统，只是因为纸质表格会丢，
-- 而且会后没人愿意再誊一遍。
CREATE TABLE IF NOT EXISTS signup (
  id          INTEGER PRIMARY KEY,
  name        TEXT NOT NULL,
  dept        TEXT DEFAULT '',           -- 教研室 / 系
  contact     TEXT DEFAULT '',           -- 手机或邮箱，教师自愿填
  roles       TEXT NOT NULL DEFAULT '[]', -- JSON：mentor / review / dev
  topic       TEXT DEFAULT '',           -- 想带的项目、想审的课程、想做的模块
  note        TEXT DEFAULT '',
  source      TEXT DEFAULT '',           -- 从哪个入口来的，便于知道哪次会有效果
  created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_signup_created ON signup(created_at);
