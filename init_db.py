"""Database initialization script."""
import asyncio
import os
import sys

import yaml


async def init_database():
    """Initialize database tables."""
    # Load config
    config = {}
    if os.path.exists("config.yaml"):
        with open("config.yaml", "r") as f:
            config = yaml.safe_load(f) or {}

    # Get database URL
    db_config = config.get("database", {})
    database_url = db_config.get("url", "") or os.environ.get("DATABASE_URL", "")

    if not database_url:
        print("Error: DATABASE_URL not configured")
        print("Set it in config.yaml or environment variable")
        sys.exit(1)

    print(f"Connecting to database...")

    # Import after setting up path
    from db.database import init_db, create_tables, close_db
    from db import models  # Import models to register them

    # Initialize connection
    init_db(
        database_url=database_url,
        pool_size=db_config.get("pool_size", 5),
        max_overflow=db_config.get("max_overflow", 10),
    )

    # Create tables
    print("Creating tables...")
    await create_tables()

    print("Tables created successfully:")
    print("  - exchange_account")
    print("  - strategy")
    print("  - trade")

    await close_db()
    print("Done!")


if __name__ == "__main__":
    # Add current directory to path
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    asyncio.run(init_database())
