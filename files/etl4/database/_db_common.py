"""Database connections, identifiers, inserts, and schema checks.

数据库连接、标识生成、写入和表结构检查。
"""

from __future__ import annotations
import os
import sys
import uuid
import psycopg
from pathlib import Path
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg import sql

CODE = Path(__file__).resolve().parent
sys.path.insert(0, str(CODE.parent))
from _settings import SOURCE_ROOT, BASE_HOLES
from _settings import settings

# 文件和报告函数由调用处直接从所属模块导入。 / Callers import file and report functions directly from their owning modules.
from _db_files import digest, read, sha

ETL = SOURCE_ROOT


ALLOWED = BASE_HOLES
PG_BIN = Path(os.environ.get("ETL4_PG_BIN", "C:/Program Files/PostgreSQL/17/bin"))
LOCK_KEY = 470040001


def uid(*parts):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, "etl4-db:" + ":".join(map(str, parts))))


def config():
    value = read(settings.config_file)
    return {**value, "database": settings.dbname}


def connection(role="owner", dbname=None, autocommit=True):
    key = {"owner": "ETL4_ADMIN_DSN", "ingest": "ETL4_INGEST_DSN", "reader": "ETL4_READ_DSN"}[role]
    overrides = {"row_factory": dict_row, "autocommit": autocommit, "connect_timeout": 5}
    if os.environ.get(key):
        overrides["dbname"] = dbname or settings.dbname
        conn = psycopg.connect(os.environ[key], **overrides)
    else:
        c = config()
        role_data = c["roles"][role]
        conn = psycopg.connect(
            host=c["host"],
            port=c["port"],
            user=role_data["user"],
            password=role_data["password"],
            dbname=dbname or c["database"],
            **overrides,
        )
    conn.execute("SET search_path TO core, public")
    conn.execute("SET statement_timeout TO '60s'")
    return conn


def insert(conn, table, data):
    values = [Jsonb(v) if isinstance(v, dict) else v for v in data.values()]
    query = sql.SQL("INSERT INTO core.{} ({}) VALUES ({})").format(
        sql.Identifier(table),
        sql.SQL(",").join(map(sql.Identifier, data)),
        sql.SQL(",").join(sql.Placeholder() for _ in data),
    )
    conn.execute(query, values)


def pick(obj, *keys):
    return {k: obj.get(k) for k in keys}


def schema_hash():
    return digest({p.name: sha(p) for p in sorted((CODE / "migrations").glob("*.sql"))})


def importer_hash():
    return digest({p.name: sha(p) for p in [CODE / "_ingest.py", CODE / "_db_common.py"]})


def assert_schema(conn):
    for p in sorted((CODE / "migrations").glob("*.sql")):
        row = conn.execute(
            "SELECT sha256 FROM core.schema_migration WHERE name=%s", (p.name,)
        ).fetchone()
        if not row or row["sha256"] != sha(p):
            raise ValueError("Migration is missing or checksum differs")
