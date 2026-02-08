-- AresBot 数据库初始化脚本
-- 此脚本在 MySQL 容器首次启动时自动执行

-- 设置字符集
SET NAMES utf8mb4;
SET CHARACTER SET utf8mb4;

-- 创建数据库（如果不存在）
CREATE DATABASE IF NOT EXISTS aresbot
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

USE aresbot;

-- 账户表
CREATE TABLE IF NOT EXISTS exchange_account (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_email VARCHAR(255) NOT NULL,
    exchange VARCHAR(50) NOT NULL DEFAULT 'binance',
    label VARCHAR(100) NOT NULL,
    api_key TEXT NOT NULL,
    api_secret TEXT NOT NULL,
    testnet BOOLEAN NOT NULL DEFAULT FALSE,
    is_active BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_user_email (user_email),
    INDEX idx_exchange (exchange)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 策略表
CREATE TABLE IF NOT EXISTS strategy (
    id INT AUTO_INCREMENT PRIMARY KEY,
    user_email VARCHAR(255) NOT NULL,
    account_id INT NOT NULL,
    name VARCHAR(100) NOT NULL,
    symbol VARCHAR(20) NOT NULL,
    base_order_size DECIMAL(20, 8) NOT NULL,
    buy_price_deviation DECIMAL(10, 4) NOT NULL,
    sell_price_deviation DECIMAL(10, 4) NOT NULL,
    grid_levels INT NOT NULL DEFAULT 3,
    polling_interval DECIMAL(10, 2) NOT NULL DEFAULT 1.0,
    price_tolerance DECIMAL(10, 4) NOT NULL DEFAULT 0.5,
    stop_loss DECIMAL(10, 4) DEFAULT NULL,
    stop_loss_delay INT DEFAULT NULL,
    max_open_positions INT NOT NULL DEFAULT 10,
    max_daily_drawdown DECIMAL(20, 8) DEFAULT NULL,
    worker_name VARCHAR(100) DEFAULT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_user_email (user_email),
    INDEX idx_account_id (account_id),
    INDEX idx_symbol (symbol),
    FOREIGN KEY (account_id) REFERENCES exchange_account(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 交易记录表
CREATE TABLE IF NOT EXISTS trade (
    id INT AUTO_INCREMENT PRIMARY KEY,
    strategy_id INT NOT NULL,
    order_id VARCHAR(100) NOT NULL,
    symbol VARCHAR(20) NOT NULL,
    side ENUM('BUY', 'SELL') NOT NULL,
    price DECIMAL(20, 8) NOT NULL,
    quantity DECIMAL(20, 8) NOT NULL,
    amount DECIMAL(20, 8) NOT NULL,
    fee DECIMAL(20, 8) DEFAULT 0,
    pnl DECIMAL(20, 8) DEFAULT NULL,
    grid_index INT DEFAULT NULL,
    related_order_id VARCHAR(100) DEFAULT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_strategy_id (strategy_id),
    INDEX idx_order_id (order_id),
    INDEX idx_symbol (symbol),
    INDEX idx_created_at (created_at),
    FOREIGN KEY (strategy_id) REFERENCES strategy(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

-- 完成提示
SELECT 'AresBot database initialized successfully!' AS message;
