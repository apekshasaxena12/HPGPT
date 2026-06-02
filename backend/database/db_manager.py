# backend/database/db_manager.py
import os
from databases import Database
from dotenv import load_dotenv
from sqlalchemy import create_engine, MetaData

# Load environment variables
load_dotenv()

# PostgreSQL URL (use your .env file to set this)
DATABASE_URL = os.getenv("DATABASE_URL")

# Async database instance (used in FastAPI)
database = Database(DATABASE_URL)

# SQLAlchemy engine (used for migrations or ORM if needed)
engine = create_engine(DATABASE_URL.replace("+asyncpg", ""))

# Metadata object for managing table creation (if you use SQLAlchemy models)
metadata = MetaData()

__all__ = ["database", "engine", "metadata", "DATABASE_URL"]

