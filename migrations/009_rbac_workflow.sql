-- =====================================================================
-- 009  权限管理（RBAC）与人工审核流程（workflow）
--
-- 设计要点：
-- 1. 角色/权限矩阵持久化，路由层用 perm 码判定；演示仍兼容 teacher:/student: 令牌。
-- 2. workflow_item 是各待审队列的统一索引，不取代各业务表的真相；
--    workflow_event 只追加，审计与事件流同源。
-- 3. 业务裁决仍走原有 packages（quiz/graph/errors/review/exam），
--    workflow 只负责排队、认领、状态机与留痕。
-- =====================================================================

-- ---------- RBAC ----------
CREATE TABLE IF NOT EXISTS auth_permission (
  code        TEXT PRIMARY KEY,
  description TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS auth_role (
  code        TEXT PRIMARY KEY,          -- admin | teacher | ta | student
  name        TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS auth_role_permission (
  role_code   TEXT NOT NULL REFERENCES auth_role(code),
  perm_code   TEXT NOT NULL REFERENCES auth_permission(code),
  PRIMARY KEY (role_code, perm_code)
);

-- 统一账号：把既有 teacher / student 挂到角色上，不另建密码体系
CREATE TABLE IF NOT EXISTS auth_account (
  id            INTEGER PRIMARY KEY,
  kind          TEXT NOT NULL,           -- teacher | student | admin
  ident         TEXT NOT NULL,           -- 工号或学号
  display_name  TEXT NOT NULL DEFAULT '',
  role_code     TEXT NOT NULL REFERENCES auth_role(code),
  klasses       TEXT NOT NULL DEFAULT '[]',  -- 可见班级；空列表 = 全班（主任/教务）
  active        INTEGER NOT NULL DEFAULT 1,
  created_at    TEXT NOT NULL,
  UNIQUE (kind, ident)
);
CREATE INDEX IF NOT EXISTS idx_auth_account_role ON auth_account(role_code);

-- 不透明会话令牌（演示可继续用 teacher:T001；正式演示推荐 session:…）
CREATE TABLE IF NOT EXISTS auth_session (
  id          INTEGER PRIMARY KEY,
  account_id  INTEGER NOT NULL REFERENCES auth_account(id),
  token       TEXT NOT NULL UNIQUE,
  issued_at   TEXT NOT NULL,
  expires_at  TEXT,                      -- NULL = 演示期不过期
  revoked     INTEGER NOT NULL DEFAULT 0,
  note        TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_auth_session_token ON auth_session(token);

-- ---------- Workflow（人工审核统一队列） ----------
CREATE TABLE IF NOT EXISTS workflow_item (
  id          INTEGER PRIMARY KEY,
  kind        TEXT NOT NULL,             -- quiz_draft | kp_mapping | review_finding
                                         -- | error_pattern | exam_grade | quiz_grade
  ref_table   TEXT NOT NULL,
  ref_id      INTEGER NOT NULL,
  state       TEXT NOT NULL DEFAULT 'pending_review',
                                         -- draft | pending_review | claimed
                                         -- | approved | rejected | retired
  title       TEXT NOT NULL DEFAULT '',
  payload     TEXT NOT NULL DEFAULT '{}',
  created_by  TEXT NOT NULL DEFAULT 'system',
  claimed_by  TEXT,
  claimed_at  TEXT,
  decided_by  TEXT,
  decided_at  TEXT,
  comment     TEXT NOT NULL DEFAULT '',
  created_at  TEXT NOT NULL,
  updated_at  TEXT NOT NULL,
  UNIQUE (kind, ref_table, ref_id)
);
CREATE INDEX IF NOT EXISTS idx_workflow_kind_state ON workflow_item(kind, state);
CREATE INDEX IF NOT EXISTS idx_workflow_state ON workflow_item(state);

-- 流程审计：只追加，与 learning_event 同哲学
CREATE TABLE IF NOT EXISTS workflow_event (
  id          INTEGER PRIMARY KEY,
  item_id     INTEGER NOT NULL REFERENCES workflow_item(id),
  from_state  TEXT NOT NULL,
  to_state    TEXT NOT NULL,
  actor       TEXT NOT NULL,
  action      TEXT NOT NULL,             -- create | claim | approve | reject
                                         -- | retire | unclaim | comment | sync
  comment     TEXT NOT NULL DEFAULT '',
  occurred_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_workflow_event_item ON workflow_event(item_id, occurred_at);

CREATE TRIGGER IF NOT EXISTS workflow_event_no_update
BEFORE UPDATE ON workflow_event
BEGIN
  SELECT RAISE(ABORT, 'workflow_event 是只追加审计流，禁止 UPDATE');
END;

CREATE TRIGGER IF NOT EXISTS workflow_event_no_delete
BEFORE DELETE ON workflow_event
BEGIN
  SELECT RAISE(ABORT, 'workflow_event 是只追加审计流，禁止 DELETE');
END;
