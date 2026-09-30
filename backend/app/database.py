import os
import ssl
from pathlib import Path

import certifi
import psycopg
from dotenv import load_dotenv
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row


load_dotenv()


def get_connection():
    dsn = os.getenv("ETL4_READ_DSN")

    if not dsn:
        raise RuntimeError(
            "ETL4_READ_DSN is not configured. "
            "Set the read-only PostgreSQL connection string before starting the API."
        )

    auth_mode = os.getenv("ETL4_DB_AUTH", "password").strip().lower()

    if auth_mode == "password":
        conn = psycopg.connect(
            dsn,
            row_factory=dict_row,
            autocommit=True,
            connect_timeout=5,
        )

    elif auth_mode == "iam":
        try:
            import boto3
        except ImportError as exc:
            raise RuntimeError(
                "AWS IAM database authentication requires boto3. "
                "Install backend/requirements-aws.txt."
            ) from exc

        options = conninfo_to_dict(dsn)

        required = ("host", "user", "dbname")
        if not all(options.get(key) for key in required):
            raise RuntimeError(
                "ETL4_READ_DSN must contain host, user and dbname for AWS IAM mode."
            )

        session = boto3.Session()
        region = (
            os.getenv("ETL4_AWS_REGION")
            or session.region_name
        )

        if not region:
            raise RuntimeError(
                "AWS region is not configured for IAM database authentication."
            )

        ca_file = (
            os.getenv("ETL4_CA_FILE")
            or ssl.get_default_verify_paths().cafile
            or certifi.where()
        )

        if not ca_file or not Path(ca_file).is_file():
            raise RuntimeError(
                "A trusted CA bundle is required for the Aurora connection."
            )

        options.pop("password", None)
        options["sslmode"] = "verify-full"
        options["sslrootcert"] = ca_file
        options["connect_timeout"] = "60"

        port = int(options.get("port", 5432))

        options["password"] = session.client(
            "rds",
            region_name=region,
        ).generate_db_auth_token(
            DBHostname=options["host"],
            Port=port,
            DBUsername=options["user"],
            Region=region,
        )

        conn = psycopg.connect(
            **options,
            row_factory=dict_row,
            autocommit=True,
        )

    else:
        raise RuntimeError(
            "ETL4_DB_AUTH must be either 'password' or 'iam'."
        )

    conn.execute("SET default_transaction_read_only=on")
    conn.execute("SET search_path TO core, public")
    conn.execute("SET statement_timeout TO '60s'")

    return conn
