"""AresBot V3 - Web Dashboard Entry Point."""
import os
import sys

# 将当前目录添加到 Python 路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import uvicorn
import yaml

from api.app import create_app
from db.database import init_db
from utils.crypto import init_encryption
from utils.logger import setup_logger


def load_config(config_path: str = "config.yaml") -> dict:
    """Load configuration from YAML file."""
    if os.path.exists(config_path):
        with open(config_path, "r") as f:
            return yaml.safe_load(f)
    return {}


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
    database_url = db_config.get("url", "") or os.environ.get("DATABASE_URL", "")
    if not database_url:
        print("Error: DATABASE_URL not configured")
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
