"""Apply migrations in order and grant permissions to the import and reader roles.
按顺序执行迁移，并设置导入和读取角色的权限。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from _db_common import CODE, config, connection, insert, schema_hash
from _run_reports import main_guard, report
from _db_files import sha
from _settings import settings, configure
from psycopg import sql


def main():
    c = config()
    with connection(dbname="postgres") as conn:
        for key in ("ingest", "reader"):
            role = c["roles"][key]
            if not conn.execute(
                "SELECT 1 FROM pg_roles WHERE rolname=%s", (role["user"],)
            ).fetchone():
                conn.execute(
                    sql.SQL(
                        "CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE"
                    ).format(sql.Identifier(role["user"]), sql.Literal(role["password"]))
                )
        if not conn.execute(
            "SELECT 1 FROM pg_database WHERE datname=%s", (c["database"],)
        ).fetchone():
            conn.execute(
                sql.SQL("CREATE DATABASE {} OWNER {} TEMPLATE template0 ENCODING {}").format(
                    sql.Identifier(c["database"]),
                    sql.Identifier(c["roles"]["owner"]["user"]),
                    sql.Literal("UTF8"),
                )
            )
    with connection() as conn:
        conn.execute("CREATE EXTENSION IF NOT EXISTS postgis")
        baseline = conn.execute(
            "SELECT pg_database_size(current_database()) AS database_bytes, postgis_full_version() AS postgis"
        ).fetchone()
        if not (settings.reports_dir / "8_empty_database.json").exists():
            report("8_empty_database.json", baseline)
        for p in sorted((CODE / "migrations").glob("*.sql")):
            exists = conn.execute("SELECT to_regclass('core.schema_migration') AS t").fetchone()[
                "t"
            ]
            row = (
                conn.execute(
                    "SELECT sha256 FROM core.schema_migration WHERE name=%s", (p.name,)
                ).fetchone()
                if exists
                else None
            )
            if row:
                if row["sha256"] != sha(p):
                    raise ValueError("Applied migration was modified; add a new migration")
                continue
            with conn.transaction():
                conn.execute(p.read_text(encoding="utf-8"))
                insert(conn, "schema_migration", {"name": p.name, "sha256": sha(p)})
        ingest = c["roles"]["ingest"]["user"]
        reader = c["roles"]["reader"]["user"]
        conn.execute(
            sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(sql.Identifier(c["database"]))
        )
        conn.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
        for user in (ingest, reader):
            conn.execute(
                sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
                    sql.Identifier(c["database"]), sql.Identifier(user)
                )
            )
            conn.execute(
                sql.SQL("GRANT USAGE ON SCHEMA core,public TO {}").format(sql.Identifier(user))
            )
            conn.execute(
                sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA core TO {}").format(
                    sql.Identifier(user)
                )
            )
            conn.execute(
                sql.SQL("ALTER ROLE {} SET search_path TO core,public").format(sql.Identifier(user))
            )
            conn.execute(
                sql.SQL("ALTER ROLE {} SET statement_timeout TO '30s'").format(sql.Identifier(user))
            )
        conn.execute(
            sql.SQL("GRANT INSERT ON ALL TABLES IN SCHEMA core TO {}").format(
                sql.Identifier(ingest)
            )
        )
        # 媒体版本复制使用会话内的 UUID 对照表。 / Media version cloning uses a UUID mapping table scoped to the session.
        conn.execute(
            sql.SQL("GRANT TEMPORARY ON DATABASE {} TO {}").format(
                sql.Identifier(c["database"]), sql.Identifier(ingest)
            )
        )
        conn.execute(
            sql.SQL("REVOKE INSERT ON core.schema_migration FROM {}").format(sql.Identifier(ingest))
        )
        conn.execute(
            sql.SQL(
                "GRANT UPDATE ON core.ingest_run,core.revision_validation,core.active_release,core.asset_location TO {}"
            ).format(sql.Identifier(ingest))
        )
        conn.execute(
            sql.SQL("ALTER ROLE {} SET default_transaction_read_only TO on").format(
                sql.Identifier(reader)
            )
        )
        tables = conn.execute(
            "SELECT count(*) AS n FROM information_schema.tables WHERE table_schema='core' AND table_type='BASE TABLE'"
        ).fetchone()["n"]
        report(
            "8_schema.json",
            {
                "status": "passed",
                "schema_sha256": schema_hash(),
                "table_count": tables,
                "postgis": baseline["postgis"],
            },
        )
        from _schema_docs import generate

        generate(conn)
    print(f"Schema applied: {tables} tables; owner / ingest / read-only roles ready")


if __name__ == "__main__":
    from _settings import StepParser

    args = StepParser().parse_args()
    configure(args)
    main_guard(main)
