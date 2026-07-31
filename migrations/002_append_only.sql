-- =====================================================================
-- 002  事件流只追加的强制约束
-- PostgreSQL 对应写法： REVOKE UPDATE, DELETE ON learning_event FROM app_user;
-- SQLite 无权限系统，用触发器等价实现；违反即 ABORT。
-- =====================================================================

CREATE TRIGGER IF NOT EXISTS learning_event_no_update
BEFORE UPDATE ON learning_event
BEGIN
  SELECT RAISE(ABORT, 'learning_event 是只追加事件流，禁止 UPDATE');
END;

CREATE TRIGGER IF NOT EXISTS learning_event_no_delete
BEFORE DELETE ON learning_event
BEGIN
  SELECT RAISE(ABORT, 'learning_event 是只追加事件流，禁止 DELETE');
END;

-- 掌握度必须挂事件：插入/更新 mastery_state 时 last_event_id 不得为空
CREATE TRIGGER IF NOT EXISTS mastery_requires_event_ins
BEFORE INSERT ON mastery_state
WHEN NEW.last_event_id IS NULL
BEGIN
  SELECT RAISE(ABORT, '禁止在没有 LearningEvent 的情况下写入掌握度');
END;

CREATE TRIGGER IF NOT EXISTS mastery_requires_event_upd
BEFORE UPDATE ON mastery_state
WHEN NEW.last_event_id IS NULL
BEGIN
  SELECT RAISE(ABORT, '禁止在没有 LearningEvent 的情况下更新掌握度');
END;
