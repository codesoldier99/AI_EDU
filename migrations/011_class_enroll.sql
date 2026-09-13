-- 学生报名实验班：扫码填姓名+手机号即可，用于收集报名意向名单。
--
-- 刻意跟 signup 表（教师报名参与实验班建设）分开，虽然结构像：
-- 这两张表的读者、权限、后续流程完全不同——一个是教师给系统投人力，
-- 一个是学生申请进笔试候选池，混在一张表里迟早会在"名单该给谁看"上出错。
--
-- 只留姓名和手机号两个字段：管理办法第二条的完整流程（签承诺书、笔试）
-- 走线下/纸质，这张表只解决"先把想报名的人截住，不然会后就找不回来了"。
CREATE TABLE IF NOT EXISTS class_enroll (
  id          INTEGER PRIMARY KEY,
  name        TEXT NOT NULL,
  phone       TEXT NOT NULL,
  source      TEXT DEFAULT '',           -- 从哪个入口来的（如二维码来源标记）
  created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_class_enroll_created ON class_enroll(created_at);
CREATE INDEX IF NOT EXISTS idx_class_enroll_phone ON class_enroll(phone);
