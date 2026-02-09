"""AresBot V3 - API Service Entry Point."""
import os
import sys
from urllib.parse import quote_plus

# 添加项目根目录到 Python 路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import uvicorn
import yaml

from api.app import create_app
from api.db.database import init_db, build_database_url
from shared.utils.crypto import init_encryption
from shared.utils.logger import setup_logger


def load_config(config_path: str = "config.yaml") -> dict:
    """Load configuration from YAML file."""
    if os.path.exists(config_path):
        with open(config_path, "r") as f:
            return yaml.safe_load(f)
    return {}


def build_db_url_from_config(db_config: dict) -> str:
    """Build database URL from config, handling special characters in password."""
    # 优先使用完整 URL
    if db_config.get("url"):
        return db_config["url"]

    # 环境变量优先级高于配置文件
    host = os.environ.get("DB_HOST") or db_config.get("host", "localhost")
    port = os.environ.get("DB_PORT") or db_config.get("port", 3306)
    user = os.environ.get("DB_USER") or db_config.get("user", "aresbot")
    password = os.environ.get("DB_PASSWORD") or db_config.get("password", "")
    database = os.environ.get("DB_NAME") or db_config.get("name", "aresbot")

    # URL 编码密码
    encoded_password = quote_plus(str(password)) if password else ""

    return f"mysql+aiomysql://{user}:{encoded_password}@{host}:{port}/{database}"


def main():
    """Main entry point."""
    config = load_config()

    log_level = config.get("logging", {}).get("level", "INFO")
    setup_logger(level=log_level)

    encryption_key = config.get("security", {}).get("encryption_key", "")
    if not encryption_key:
        encryption_key = os.environ.get("ENCRYPTION_KEY", "")
    if not encryption_key:
        print("Warning: No encryption key configured. Generating a temporary one.")
        import secrets
        encryption_key = secrets.token_hex(32)
    init_encryption(encryption_key)

    db_config = config.get("database", {})
    # 优先从 config.yaml 构建，否则从环境变量构建
    database_url = build_db_url_from_config(db_config) if db_config else build_database_url()
    if not database_url:
        print("Error: Database not configured")
        sys.exit(1)

    init_db(
        database_url=database_url,
        pool_size=db_config.get("pool_size", 5),
        max_overflow=db_config.get("max_overflow", 10),
    )

    app = create_app()

    server_config = config.get("server", {})
    host = server_config.get("host", "0.0.0.0")
    port = server_config.get("port", 8000)

    uvicorn.run(app, host=host, port=port)


if __name__ == "__main__":
    main()
