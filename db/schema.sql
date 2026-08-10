-- D:/hragent/db/schema.sql
CREATE DATABASE IF NOT EXISTS hragent CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE hragent;

-- 用户表（替代 middleware.py 的 MOCK_USERS）
CREATE TABLE IF NOT EXISTS users (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    username    VARCHAR(64) NOT NULL UNIQUE,
    password    VARCHAR(256) NOT NULL,
    display_name VARCHAR(64) NOT NULL,
    role        VARCHAR(16) NOT NULL DEFAULT 'employee',
    open_id     VARCHAR(128) DEFAULT NULL,
    voice_enabled BOOLEAN DEFAULT TRUE,
    voice_rate    VARCHAR(8) DEFAULT '+20%',
    created_at  DATETIME(3) DEFAULT CURRENT_TIMESTAMP(3)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 对话历史（替代 agent/memory.py 中 asyncpg 的 messages 表）
CREATE TABLE IF NOT EXISTS conversations (
    id          CHAR(36) PRIMARY KEY,
    session_id  VARCHAR(128) NOT NULL,
    user_id     INT NOT NULL,
    role        VARCHAR(16) NOT NULL,
    content     TEXT NOT NULL,
    intent      VARCHAR(32) DEFAULT '',
    created_at  DATETIME(3) DEFAULT CURRENT_TIMESTAMP(3),
    INDEX idx_session (session_id, created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 审批请求表（替代 routes/approval.py 的 MOCK_APPROVALS + mcp_servers/approval/ 的 SQLite）
CREATE TABLE IF NOT EXISTS approval_requests (
    id            CHAR(36) PRIMARY KEY,
    type          VARCHAR(32) NOT NULL,
    title         VARCHAR(256) NOT NULL,
    applicant_id  INT NOT NULL,
    body          JSON NOT NULL,
    status        VARCHAR(16) DEFAULT 'draft',
    assignee_id   INT DEFAULT NULL,
    result_reason TEXT,
    created_at    DATETIME(3) DEFAULT CURRENT_TIMESTAMP(3),
    updated_at    DATETIME(3) DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
    resolved_at   DATETIME(3) DEFAULT NULL,
    INDEX idx_status (status),
    INDEX idx_applicant (applicant_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 审批日志
CREATE TABLE IF NOT EXISTS approval_logs (
    id            CHAR(36) PRIMARY KEY,
    request_id    CHAR(36) NOT NULL,
    from_status   VARCHAR(16),
    to_status     VARCHAR(16),
    operator_id   INT NOT NULL,
    operator_role VARCHAR(16),
    comment       TEXT,
    created_at    DATETIME(3) DEFAULT CURRENT_TIMESTAMP(3),
    FOREIGN KEY (request_id) REFERENCES approval_requests(id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- 知识库文档（替代 routes/knowledge.py 的 MOCK_DOCS）
CREATE TABLE IF NOT EXISTS knowledge_docs (
    id          CHAR(36) PRIMARY KEY,
    title       VARCHAR(256) NOT NULL,
    content     TEXT NOT NULL,
    category    VARCHAR(32) DEFAULT '',
    tags        JSON DEFAULT NULL,
    created_by  INT DEFAULT NULL,
    updated_at  DATETIME(3) DEFAULT CURRENT_TIMESTAMP(3) ON UPDATE CURRENT_TIMESTAMP(3),
    created_at  DATETIME(3) DEFAULT CURRENT_TIMESTAMP(3)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
