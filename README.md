# AresBot v3

加密货币网格交易机器人，支持多节点分布式部署。

## 目录

- [项目架构](#项目架构)
- [技术栈](#技术栈)
- [目录结构](#目录结构)
- [核心组件](#核心组件)
- [数据流](#数据流)
- [环境要求](#环境要求)
- [配置说明](#配置说明)
- [构建与部署](#构建与部署)
- [API 接口](#api-接口)
- [开发指南](#开发指南)

---

## 项目架构

```
┌─────────────────────────────────────────────────────────────┐
│                     Nginx (反向代理)                         │
│                        Port: 80                              │
└─────────────────────────────┬───────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                    OAuth2 Proxy                              │
│                  (GitHub 认证)                               │
└─────────────────────────────┬───────────────────────────────┘
                              │
           ┌──────────────────┴──────────────────┐
           ▼                                     ▼
┌─────────────────────┐              ┌─────────────────────┐
│   Vue 3 前端        │              │   FastAPI 后端       │
│   (静态文件)        │              │   Port: 8000        │
└─────────────────────┘              └──────────┬──────────┘
                                                │
                    ┌───────────────────────────┼───────────────────────────┐
                    │                           │                           │
                    ▼                           ▼                           ▼
          ┌─────────────────┐        ┌─────────────────┐        ┌─────────────────┐
          │     MySQL       │        │     Redis       │        │  Celery Worker  │
          │   (持久化存储)   │        │  (状态/任务队列) │        │   (策略执行)     │
          └─────────────────┘        └─────────────────┘        └─────────────────┘
                                              │
                                              │ 分布式锁 + 状态同步
                                              │
                    ┌─────────────────────────┼─────────────────────────┐
                    ▼                         ▼                         ▼
             ┌────────────┐           ┌────────────┐           ┌────────────┐
             │ Worker 1   │           │ Worker 2   │           │ Worker N   │
             │ (策略执行)  │           │ (策略执行)  │           │ (策略执行)  │
             └────────────┘           └────────────┘           └────────────┘
```

### 多节点部署说明

- **分布式锁**: 使用 Redis 实现，确保同一策略只在一个 Worker 上运行
- **状态同步**: 策略运行状态实时写入 Redis，API 和前端可查询任意策略的运行节点
- **水平扩展**: 可动态增加 Celery Worker 节点，自动负载均衡

---

## 技术栈

### 后端
| 组件 | 技术 | 版本 |
|------|------|------|
| Web 框架 | FastAPI | ≥0.109.0 |
| ORM | SQLModel (SQLAlchemy) | ≥0.0.14 |
| 数据库 | MySQL | 8.x |
| 任务队列 | Celery | ≥5.3.0 |
| 缓存/消息 | Redis | 7.x |
| 交易所 API | CCXT | ≥4.2.0 |

### 前端
| 组件 | 技术 | 版本 |
|------|------|------|
| 框架 | Vue 3 | 3.x |
| 语言 | TypeScript | 5.x |
| UI 库 | Element Plus | 2.x |
| 图表 | ECharts | 5.x |
| 构建工具 | Vite | 5.x |
| 状态管理 | Pinia | 2.x |

### 基础设施
| 组件 | 技术 |
|------|------|
| 容器化 | Docker + Docker Compose |
| 反向代理 | Nginx |
| 认证 | OAuth2 Proxy (GitHub) |

---

## 目录结构

```
AresBotv3/
├── api/                        # FastAPI 路由和应用
│   ├── app.py                  # FastAPI 应用工厂
│   ├── deps.py                 # 依赖注入
│   └── routes/                 # API 路由
│       ├── account.py          # 账户管理
│       ├── strategy.py         # 策略管理
│       └── trade.py            # 交易记录
│
├── core/                       # 核心抽象和接口
│   ├── base_strategy.py        # 策略基类
│   ├── base_exchange.py        # 交易所基类
│   ├── event_bus.py            # 事件总线
│   ├── state_store.py          # 状态存储
│   └── redis_client.py         # Redis 客户端封装
│
├── db/                         # 数据库层
│   ├── database.py             # 数据库连接管理
│   ├── models.py               # SQLModel 模型定义
│   └── crud.py                 # CRUD 操作
│
├── domain/                     # 业务域模型
│   ├── order.py                # 订单模型
│   ├── position_tracker.py     # 持仓跟踪
│   └── risk_manager.py         # 风控管理
│
├── engine/                     # 交易引擎
│   ├── trading_engine.py       # 主交易引擎
│   ├── engine_manager.py       # 引擎管理器
│   └── position_syncer.py      # 持仓同步
│
├── exchanges/                  # 交易所实现
│   └── binance_spot.py         # Binance 现货
│
├── strategies/                 # 策略实现
│   └── grid_strategy.py        # 网格策略
│
├── tasks/                      # Celery 任务
│   ├── __init__.py
│   └── strategy_task.py        # 策略执行任务
│
├── utils/                      # 工具函数
│   ├── crypto.py               # 加密/解密
│   ├── logger.py               # 日志配置
│   └── retry.py                # 重试装饰器
│
├── web/                        # Vue 前端
│   ├── src/
│   │   ├── api/                # API 客户端
│   │   ├── components/         # Vue 组件
│   │   ├── views/              # 页面视图
│   │   ├── stores/             # Pinia 存储
│   │   ├── types/              # TypeScript 类型
│   │   └── router/             # Vue Router
│   └── package.json
│
├── celery_app.py               # Celery 应用配置
├── config.py                   # 配置数据类
├── main.py                     # 应用入口
├── config.yaml                 # YAML 配置文件
├── docker-compose.yml          # Docker Compose 配置
├── Dockerfile                  # Docker 构建文件
├── nginx.conf                  # Nginx 配置
└── requirements.txt            # Python 依赖
```

---

## 核心组件

### 1. 交易引擎 (TradingEngine)

交易主循环，协调所有交易组件：

```
┌─────────────────────────────────────────────────────┐
│                   TradingEngine                      │
│                                                      │
│  ┌──────────────────────────────────────────────┐  │
│  │              主循环 (_run_loop)               │  │
│  │                                              │  │
│  │  1. _fetch_price()      获取当前价格         │  │
│  │  2. _sync_orders()      同步订单状态         │  │
│  │  3. _check_new_orders() 检查是否下新单       │  │
│  │  4. _check_reprice()    检查是否需要改价     │  │
│  │  5. _check_stop_loss()  检查止损条件         │  │
│  │  6. _periodic_sync()    定期同步持仓         │  │
│  │  7. _update_status()    更新状态到 Redis     │  │
│  └──────────────────────────────────────────────┘  │
│                                                      │
│  组件:                                               │
│  - Strategy (策略逻辑)                              │
│  - Exchange (交易所接口)                            │
│  - RiskManager (风控管理)                           │
│  - PositionTracker (持仓跟踪)                       │
│  - EventBus (事件总线)                              │
└─────────────────────────────────────────────────────┘
```

### 2. 网格策略 (GridStrategy)

```
价格
  ▲
  │     卖单区域
  │  ─────────────── 卖价 = 买价 × (1 + sell_offset%)
  │
  │  ═══════════════ 当前市价
  │
  │  ─────────────── 买价1 = 市价 × (1 - offset%)
  │  ─────────────── 买价2 = 市价 × (1 - offset% × 2)
  │  ─────────────── 买价3 = 市价 × (1 - offset% × 3)
  │     买单区域 (grid_levels 层)
  └────────────────────────────────────────────▶ 时间
```

### 3. 风控管理 (RiskManager)

| 风控规则 | 说明 |
|----------|------|
| 最大持仓数 | 限制同时持有的仓位数量 |
| 价格止损 | 亏损超过阈值时触发止损 |
| 时间止损 | 持仓时间超过阈值时触发止损 |
| 日亏损限制 | 单日累计亏损达到限额后停止交易 |
| 冷却期 | 连续亏损后进入冷却期，暂停开仓 |

### 4. Redis 数据结构

```
# 策略运行实例信息 (Hash)
strategy:running:{strategy_id}
├── task_id         # Celery 任务 ID
├── worker_ip       # 运行节点 IP
├── worker_hostname # 运行节点主机名
├── status          # running | stopping | error
├── started_at      # 启动时间戳
├── current_price   # 当前价格
├── pending_buys    # 待成交买单数
├── pending_sells   # 待成交卖单数
├── position_count  # 持仓数
├── last_error      # 最后错误信息
└── updated_at      # 最后更新时间戳

# 策略分布式锁 (String)
strategy:lock:{strategy_id} = task_id
TTL: 86400 (24小时)

# 活跃节点列表 (Set)
workers:active = ["192.168.1.10", "192.168.1.11", ...]
```

---

## 数据流

### 1. 策略启动流程

```
┌─────────┐     ┌─────────┐     ┌─────────┐     ┌─────────┐
│  前端   │────▶│  API    │────▶│  Redis  │────▶│ Celery  │
│         │     │         │     │ (锁检查) │     │ Worker  │
└─────────┘     └─────────┘     └─────────┘     └────┬────┘
                                                     │
                                                     ▼
                                              ┌─────────────┐
                                              │   Trading   │
                                              │   Engine    │
                                              └──────┬──────┘
                                                     │
                    ┌────────────────────────────────┼────────────────────────────────┐
                    ▼                                ▼                                ▼
             ┌─────────────┐                  ┌─────────────┐                  ┌─────────────┐
             │   交易所    │                  │   Redis     │                  │   SQLite    │
             │  (下单)     │                  │ (状态更新)   │                  │ (成交记录)  │
             └─────────────┘                  └─────────────┘                  └─────────────┘
```

### 2. 订单生命周期

```
┌──────────┐     ┌──────────┐     ┌──────────┐     ┌──────────┐
│  策略    │────▶│  下买单  │────▶│  买单    │────▶│  添加    │
│  决策    │     │          │     │  成交    │     │  持仓    │
└──────────┘     └──────────┘     └──────────┘     └────┬─────┘
                                                        │
                                                        ▼
                                                  ┌──────────┐
                                                  │  下卖单  │
                                                  │          │
                                                  └────┬─────┘
                                                       │
                                                       ▼
┌──────────┐     ┌──────────┐     ┌──────────┐     ┌──────────┐
│  移除    │◀────│  计算    │◀────│  卖单    │◀────│  等待    │
│  持仓    │     │  盈亏    │     │  成交    │     │  成交    │
└──────────┘     └──────────┘     └──────────┘     └──────────┘
```

### 3. 状态查询流程

```
┌─────────┐     ┌─────────┐     ┌─────────┐
│  前端   │────▶│  API    │────▶│  Redis  │
│ 定时轮询│     │ /status │     │ HGETALL │
└─────────┘     └─────────┘     └────┬────┘
     ▲                               │
     │                               │
     └───────────────────────────────┘
           返回策略状态 + 运行节点 IP
```

---

## 环境要求

- Docker ≥ 20.10
- Docker Compose ≥ 2.0
- Node.js ≥ 18 (本地开发前端)
- Python ≥ 3.11 (本地开发后端)

---

## 配置说明

### 环境变量 (.env)

完整的环境变量说明请参考 `.env.example` 文件。

**主要配置项：**

```bash
# -------------------- GitHub OAuth --------------------
GITHUB_CLIENT_ID=your_github_client_id
GITHUB_CLIENT_SECRET=your_github_client_secret
OAUTH2_REDIRECT_URL=http://localhost/oauth2/callback

# -------------------- 安全密钥 --------------------
COOKIE_SECRET=your_cookie_secret_32_bytes
ENCRYPTION_KEY=your_encryption_key_64_hex_chars

# -------------------- 服务端口 --------------------
NGINX_PORT=80
API_PORT=8000
MYSQL_PORT=3306
REDIS_PORT=6379

# -------------------- MySQL 配置 --------------------
MYSQL_ROOT_PASSWORD=root_password
MYSQL_DATABASE=aresbot
MYSQL_USER=aresbot
MYSQL_PASSWORD=aresbot_password
MYSQL_TZ=Asia/Shanghai              # 容器系统时区（北京时间）
MYSQL_DEFAULT_TIME_ZONE=+08:00      # MySQL 默认会话时区

# -------------------- Redis 配置 --------------------
REDIS_PASSWORD=redis_password
REDIS_CONNECT_TIMEOUT=1.0 # Redis 连接超时(秒)
REDIS_SOCKET_TIMEOUT=1.0  # Redis 读写超时(秒)

# -------------------- Celery Worker 配置 --------------------
CELERY_CONCURRENCY=4      # 单个 Worker 并发数
WORKER_REPLICAS=2         # Worker 副本数量
CELERY_LOG_LEVEL=info     # 日志级别
STRATEGY_STOP_POLL_INTERVAL=0.8  # 停止信号兜底轮询间隔(秒)

# -------------------- 工作节点专用 --------------------
MASTER_HOST=192.168.1.100   # 主节点 IP
MASTER_MYSQL_PORT=3306
MASTER_REDIS_PORT=6379
```

### 生成安全密钥

```bash
# 生成 COOKIE_SECRET (32字节)
openssl rand -base64 32

# 生成 ENCRYPTION_KEY (32字节十六进制)
openssl rand -hex 32
```

---

## 构建与部署

### 单机部署 (Docker Compose)

```bash
# 1. 克隆项目
git clone <repository_url>
cd AresBotv3

# 2. 创建环境变量文件
cp .env.example .env
# 编辑 .env 填入必要配置

# 3. 构建前端
cd web
npm install
npm run build
cd ..

# 4. 启动所有服务
docker-compose up -d

# 5. 查看日志
docker-compose logs -f

# 6. 扩展 Worker 数量
docker-compose up -d --scale celery-worker=4
```

### 多节点部署

适用于需要更多计算资源的场景，将 Worker 分布到多台服务器。

**架构说明**

```
主节点 (Server A)                    工作节点 (Server B, C, ...)
┌─────────────────────┐             ┌─────────────────────┐
│ Nginx               │             │                     │
│ OAuth2 Proxy        │             │ Celery Worker ×N    │
│ FastAPI             │◀───────────▶│                     │
│ MySQL        ───────┼─────────────┼──▶ 连接主节点       │
│ Redis        ───────┼─────────────┼──▶ MySQL/Redis      │
│ Celery Worker ×N    │             │                     │
└─────────────────────┘             └─────────────────────┘
```

#### 步骤 1: 配置主节点

**主节点 .env 文件**

```bash
# ==================== 主节点配置 ====================
# GitHub OAuth
GITHUB_CLIENT_ID=your_github_client_id
GITHUB_CLIENT_SECRET=your_github_client_secret
OAUTH2_REDIRECT_URL=http://your-domain.com/oauth2/callback

# 安全密钥
COOKIE_SECRET=your_cookie_secret
ENCRYPTION_KEY=your_encryption_key

# 服务端口 (需要开放给工作节点)
NGINX_PORT=80
API_PORT=8000
MYSQL_PORT=3306
REDIS_PORT=6379

# MySQL 配置
MYSQL_ROOT_PASSWORD=your_root_password
MYSQL_DATABASE=aresbot
MYSQL_USER=aresbot
MYSQL_PASSWORD=your_mysql_password
MYSQL_TZ=Asia/Shanghai
MYSQL_DEFAULT_TIME_ZONE=+08:00

# Redis 配置
REDIS_PASSWORD=your_redis_password

# Worker 配置
CELERY_CONCURRENCY=4
WORKER_REPLICAS=2
```

**启动主节点**

```bash
# 在主节点服务器上
cd AresBotv3
docker-compose up -d

# 查看服务状态
docker-compose ps

# 查看日志
docker-compose logs -f
```

#### 步骤 2: 配置工作节点

**工作节点 .env 文件**

```bash
# ==================== 工作节点配置 ====================
# 主节点连接信息
MASTER_HOST=192.168.1.100          # 主节点 IP 地址
MASTER_MYSQL_PORT=3306
MASTER_REDIS_PORT=6379

# 数据库凭证 (与主节点相同)
MYSQL_USER=aresbot
MYSQL_PASSWORD=your_mysql_password
MYSQL_DATABASE=aresbot
MYSQL_TZ=Asia/Shanghai
MYSQL_DEFAULT_TIME_ZONE=+08:00

# Redis 凭证 (与主节点相同)
REDIS_PASSWORD=your_redis_password
REDIS_CONNECT_TIMEOUT=1.0
REDIS_SOCKET_TIMEOUT=1.0

# 安全密钥 (与主节点相同)
ENCRYPTION_KEY=your_encryption_key

# Worker 配置
CELERY_CONCURRENCY=4
WORKER_REPLICAS=4
CELERY_LOG_LEVEL=info
STRATEGY_STOP_POLL_INTERVAL=0.8
```

**启动工作节点**

```bash
# 在工作节点服务器上
cd AresBotv3

# 使用工作节点专用配置文件启动
docker-compose -f docker-compose.worker.yml up -d

# 扩展 Worker 数量
docker-compose -f docker-compose.worker.yml up -d --scale celery-worker=8

# 查看日志
docker-compose -f docker-compose.worker.yml logs -f
```

#### 步骤 3: 防火墙配置

主节点需要开放端口给工作节点：

```bash
# Ubuntu/Debian (ufw)
sudo ufw allow from WORKER_IP to any port 3306  # MySQL
sudo ufw allow from WORKER_IP to any port 6379  # Redis

# CentOS/RHEL (firewalld)
sudo firewall-cmd --permanent --add-rich-rule='rule family="ipv4" source address="WORKER_IP" port port="3306" protocol="tcp" accept'
sudo firewall-cmd --permanent --add-rich-rule='rule family="ipv4" source address="WORKER_IP" port port="6379" protocol="tcp" accept'
sudo firewall-cmd --reload
```

#### 步骤 4: 验证多节点部署

```bash
# 在主节点上检查所有 Worker
docker exec -it $(docker ps -qf "name=redis") redis-cli -a your_redis_password

# Redis 命令
> KEYS strategy:running:*     # 查看运行中的策略
> SMEMBERS workers:active     # 查看活跃的 Worker 节点

# 查看 Celery Worker 状态
docker exec -it $(docker ps -qf "name=celery") celery -A celery_app inspect active
```

#### 多节点配置汇总

| 配置文件 | 用途 | 使用位置 |
|----------|------|----------|
| `docker-compose.yml` | 主节点完整服务 | 主节点服务器 |
| `docker-compose.worker.yml` | 仅 Worker 服务 | 工作节点服务器 |
| `.env` | 环境变量配置 | 所有服务器 |
| `.env.example` | 配置模板 | 参考用 |

### 本地开发

```bash
# 后端
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt

# 启动 Redis (Docker)
docker run -d --name redis -p 6379:6379 redis:7-alpine

# 启动 MySQL (Docker)
docker run -d --name mysql -p 3306:3306 \
  -e MYSQL_ROOT_PASSWORD=root \
  -e MYSQL_DATABASE=aresbot \
  -e MYSQL_USER=user \
  -e MYSQL_PASSWORD=pass \
  mysql:8

# 启动 API
python main.py

# 启动 Celery Worker (新终端)
celery -A celery_app worker --loglevel=info

# 前端 (新终端)
cd web
npm install
npm run dev
```

### 服务端口

| 服务 | 端口 | 说明 |
|------|------|------|
| Nginx | 80 | 对外入口 |
| OAuth2 Proxy | 4180 | 认证代理 (内部) |
| FastAPI | 8000 | API 服务 (内部) |
| MySQL | 3306 | 数据库 (内部) |
| Redis | 6379 | 缓存/消息队列 (内部) |

---

## API 接口

### 账户管理

| 方法 | 端点 | 说明 |
|------|------|------|
| GET | /api/accounts | 获取账户列表 |
| POST | /api/accounts | 创建账户 |
| GET | /api/accounts/{id} | 获取账户详情 |
| PUT | /api/accounts/{id} | 更新账户 |
| DELETE | /api/accounts/{id} | 删除账户 |

### 策略管理

| 方法 | 端点 | 说明 |
|------|------|------|
| GET | /api/strategies | 获取策略列表 |
| POST | /api/strategies | 创建策略 |
| GET | /api/strategies/running | 获取所有运行中策略 |
| GET | /api/strategies/{id} | 获取策略详情 |
| PUT | /api/strategies/{id} | 更新策略 |
| DELETE | /api/strategies/{id} | 删除策略 |
| POST | /api/strategies/{id}/start | 启动策略 |
| POST | /api/strategies/{id}/stop | 停止策略 |
| GET | /api/strategies/{id}/status | 获取策略运行状态 |

### 交易记录

| 方法 | 端点 | 说明 |
|------|------|------|
| GET | /api/trades | 获取交易记录 |
| GET | /api/trades/stats | 获取交易统计 |

### 健康检查

| 方法 | 端点 | 说明 |
|------|------|------|
| GET | /health | API 健康检查 |
| GET | /health/redis | Redis 连接检查 |

---

## 开发指南

### 添加新交易所

1. 在 `exchanges/` 创建新文件，如 `okx_spot.py`
2. 继承 `BaseExchange` 并实现所有抽象方法
3. 在 `tasks/strategy_task.py` 中添加交易所选择逻辑

```python
# exchanges/okx_spot.py
from core.base_exchange import BaseExchange

class OKXSpot(BaseExchange):
    def get_ticker_price(self) -> float:
        # 实现获取价格逻辑
        pass
    
    def place_batch_orders(self, orders):
        # 实现批量下单逻辑
        pass
    
    # ... 其他方法
```

### 添加新策略

1. 在 `strategies/` 创建新文件
2. 继承 `BaseStrategy` 并实现交易决策逻辑

```python
# strategies/dca_strategy.py
from core.base_strategy import BaseStrategy, TradeDecision

class DCAStrategy(BaseStrategy):
    def should_buy(self, current_price, active_buy_orders, active_sell_orders):
        # 实现定投买入逻辑
        pass
    
    def should_sell(self, buy_price, buy_quantity, current_price):
        # 实现卖出逻辑
        pass
```

### 代码规范

- 使用 `black` 格式化 Python 代码
- 使用 `eslint` + `prettier` 格式化前端代码
- 所有新功能需要添加类型注解
- 提交前运行测试

```bash
# Python 格式化
black .

# 前端格式化
cd web && npm run lint
```

---

## 常见问题

### 1. 策略无法启动

检查：
- Redis 是否正常运行: `docker-compose logs redis`
- Celery Worker 是否正常: `docker-compose logs celery-worker`
- 策略是否已在运行（分布式锁）

### 2. 前端无法连接 API

检查：
- Nginx 配置是否正确
- OAuth2 Proxy 是否正常运行
- API 服务是否启动

### 3. 交易所连接失败

检查：
- API Key 是否正确
- 是否使用了测试网配置
- 网络是否能访问交易所 API

---

## License

MIT License
