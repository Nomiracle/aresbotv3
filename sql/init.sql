-- AresBot V3 Database Initialization
-- MySQL 8.0+

CREATE DATABASE IF NOT EXISTS aresbot DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;

USE aresbot;

-- Exchange Account Table
CREATE TABLE IF NOT EXISTS exchange_account (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    user_email VARCHAR(255) NOT NULL COMMENT '用户邮箱',
    exchange VARCHAR(50) NOT NULL COMMENT '交易所标识',
    label VARCHAR(100) NOT NULL COMMENT '备注名',
    api_key VARCHAR(255) NOT NULL COMMENT 'API Key (AES加密)',
    api_secret VARCHAR(255) NOT NULL COMMENT 'API Secret (AES加密)',
    testnet TINYINT NOT NULL DEFAULT 0 COMMENT '测试网 0=否 1=是',
    is_active TINYINT NOT NULL DEFAULT 1 COMMENT '启用 0=否 1=是',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    INDEX idx_user_email (user_email)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='交易所账户';

-- Strategy Table
CREATE TABLE IF NOT EXISTS strategy (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    user_email VARCHAR(255) NOT NULL COMMENT '用户邮箱',
    account_id BIGINT NOT NULL COMMENT '账户ID',
    name VARCHAR(100) NOT NULL COMMENT '策略名称',
    symbol VARCHAR(20) NOT NULL COMMENT '交易对',
    base_order_size DECIMAL(20,8) NOT NULL COMMENT '基础订单量',
    buy_price_deviation DECIMAL(10,4) NOT NULL COMMENT '买入价格偏离%',
    sell_price_deviation DECIMAL(10,4) NOT NULL COMMENT '卖出价格偏离%',
    grid_levels INT NOT NULL DEFAULT 3 COMMENT '网格层数',
    polling_interval DECIMAL(10,2) NOT NULL DEFAULT 1.00 COMMENT '轮询间隔秒',
    price_tolerance DECIMAL(10,4) NOT NULL DEFAULT 0.5000 COMMENT '改价容差%',
    stop_loss DECIMAL(10,4) DEFAULT NULL COMMENT '止损%',
    stop_loss_delay INT DEFAULT NULL COMMENT '止损延迟秒',
    max_open_positions INT NOT NULL DEFAULT 10 COMMENT '最大持仓数',
    max_daily_drawdown DECIMAL(20,8) DEFAULT NULL COMMENT '日最大回撤',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
    INDEX idx_user_email (user_email),
    INDEX idx_account_id (account_id),
    CONSTRAINT fk_strategy_account FOREIGN KEY (account_id) REFERENCES exchange_account(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='策略配置';

-- Trade Table
CREATE TABLE IF NOT EXISTS trade (
    id BIGINT AUTO_INCREMENT PRIMARY KEY,
    strategy_id BIGINT NOT NULL COMMENT '策略ID',
    order_id VARCHAR(64) NOT NULL COMMENT '订单号',
    symbol VARCHAR(20) NOT NULL COMMENT '交易对',
    side VARCHAR(10) NOT NULL COMMENT 'buy/sell',
    price DECIMAL(20,8) NOT NULL COMMENT '价格',
    quantity DECIMAL(20,8) NOT NULL COMMENT '数量',
    amount DECIMAL(20,8) NOT NULL COMMENT '金额',
    fee DECIMAL(20,8) NOT NULL DEFAULT 0 COMMENT '手续费',
    pnl DECIMAL(20,8) DEFAULT NULL COMMENT '盈亏',
    grid_index INT DEFAULT NULL COMMENT '网格层级',
    related_order_id VARCHAR(64) DEFAULT NULL COMMENT '关联订单号',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '成交时间',
    INDEX idx_strategy_id (strategy_id),
    INDEX idx_created_at (created_at),
    CONSTRAINT fk_trade_strategy FOREIGN KEY (strategy_id) REFERENCES strategy(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='成交记录';
