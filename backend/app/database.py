import os

import psycopg
from dotenv import load_dotenv
from psycopg.rows import dict_row


load_dotenv()


def get_connection():
    dsn = os.getenv("ETL4_READ_DSN")

    if not dsn:
        raise RuntimeError(
            "ETL4_READ_DSN is not configured. "
            "Set the read-only PostgreSQL connection string before starting the API."
        )

    conn = psycopg.connect(
        dsn,
        row_factory=dict_row,
        autocommit=True,
        connect_timeout=5,
    )

    conn.execute("SET search_path TO core, public")
    conn.execute("SET statement_timeout TO '60s'")

    return conn
