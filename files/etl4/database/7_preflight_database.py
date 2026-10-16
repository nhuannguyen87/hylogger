"""Register the selected input manifest and prepare an isolated local database instance.
登记本次输入清单，准备独立的本地数据库实例。
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import secrets
import socket
import subprocess
from _db_common import ETL, PG_BIN, config, connection
from _prepared_inputs import summary_holes
from _run_reports import command, main_guard, now, report
from _db_files import digest, inside, read, sha, write
from _settings import StepParser
from _settings import settings, configure


def freeze_inputs():
    pointer = read(settings.prepared_dir / "latest_verified.json")
    summary_path = inside(settings.prepared_dir, settings.prepared_dir / pointer["summary_json"])
    if sha(summary_path) != pointer["summary_sha256"]:
        raise ValueError("Verified summary hash mismatch")
    summary = read(summary_path)
    summary_holes(summary)
    prepared_hashes = {}
    raw_hashes = {}
    for h in summary["holes"]:
        hole = h["hole_id"]
        folder = inside(settings.prepared_dir, settings.prepared_dir / h["directory"])
        hashes = dict(h["report_sha256"])
        last = None
        for name, expected in h["report_sha256"].items():
            if sha(folder / name) != expected:
                raise ValueError(f"Report changed: {hole}/{name}")
        report_names = [
            "1_inventory.json",
            "2_integrity.json",
            "3_metadata_report.json",
            "4_alignment_report.json",
            "5_storage_report.json",
        ]
        for name in report_names:
            r = read(folder / name)
            if (
                r["status"] not in ("passed", "passed_with_issues")
                or r["pipeline_id"] != h["pipeline_id"]
            ):
                raise ValueError("Invalid prerequisite")
            if last and r.get("previous_step") != int(last.split("_")[0]):
                raise ValueError("Broken step order")
            last = name
        metadata = read(folder / "3_metadata_report.json")
        alignment = read(folder / "4_alignment_report.json")
        storage = read(folder / "5_storage_report.json")
        hashes["3_normalized_metadata.json"] = metadata["normalized_metadata_sha256"]
        hashes.update(alignment["artifacts"])
        hashes.update({a["relative_path"]: a["sha256"] for a in storage["assets"]})
        # 步骤 6 已验证产物；这里登记摘要，步骤 28 和 37 在验收边界重新核验。 / Step 6 already checked the outputs; register their digests here and recheck them at acceptance in steps 28 and 37.
        raw_hashes[hole] = {}
        for a in storage["raw_assets"]:
            raw_hashes[hole][a["source_relative_path"]] = a["sha256"]
        prepared_hashes[hole] = hashes
        print(
            f"{hole}: frozen {len(hashes)} prepared files and {len(raw_hashes[hole])} raw files",
            flush=True,
        )
    lock = {
        "summary_path": pointer["summary_json"],
        "summary": summary,
        "pipeline_id": summary["pipeline_id"],
        "holes": summary["active_holes"],
        "prepared_hashes": prepared_hashes,
        "raw_hashes": raw_hashes,
        "totals": summary["totals"],
    }
    existing = settings.reports_dir / "7_input_lock.json"
    write(settings.reports_dir / "input_manifests" / (digest(lock) + ".json"), lock)
    write(existing, lock)
    return lock


def local_runtime(action):
    if settings.db_config is not None:
        raise ValueError("An existing instance configuration is selected; use --inputs-only to register inputs and start or stop the instance from its original runtime directory")
    pgdata = inside(settings.runtime_dir, settings.runtime_dir / "pgdata")
    if action == "stop":
        if not (pgdata / "ETL4_OWNED_CLUSTER").exists():
            raise ValueError("Not an ETL4-owned cluster")
        command([PG_BIN / "pg_ctl.exe", "-D", pgdata, "stop", "-m", "fast", "-w", "-t", "30"])
        print("Local ETL4 cluster stopped")
        return
    settings.runtime_dir.mkdir(parents=True, exist_ok=True)
    if not (settings.runtime_dir / "local_config.json").exists():
        c = {
            "host": "127.0.0.1",
            "port": 55433,
            "database": settings.dbname,
            "pg_bin": str(PG_BIN),
            "roles": {
                k: {"user": v, "password": secrets.token_urlsafe(32)}
                for k, v in [
                    ("owner", "etl4_owner"),
                    ("ingest", "etl4_ingest"),
                    ("reader", "etl4_reader"),
                ]
            },
        }
        write(settings.runtime_dir / "local_config.json", c)
        # 保留工作目录继承的权限，避免当前执行环境失去访问权限。 / Preserve inherited work directory permissions so the current environment retains access.
    c = config()
    if not (pgdata / "PG_VERSION").exists():
        if pgdata.exists() and any(pgdata.iterdir()):
            raise ValueError("Refusing initialization in a nonempty directory")
        password_file = settings.runtime_dir / "init_password.tmp"
        write(password_file, c["roles"]["owner"]["password"] + "\n")
        try:
            command(
                [
                    PG_BIN / "initdb.exe",
                    "-D",
                    pgdata,
                    "-U",
                    c["roles"]["owner"]["user"],
                    "--pwfile",
                    password_file,
                    "--auth",
                    "scram-sha-256",
                    "--encoding",
                    "UTF8",
                    "--locale",
                    "C",
                    "--data-checksums",
                ]
            )
        finally:
            password_file.unlink(missing_ok=True)
        write(
            pgdata / "ETL4_OWNED_CLUSTER",
            {"purpose": "ETL4 isolated local database", "created_at": now()},
        )
        with (pgdata / "postgresql.conf").open("a", encoding="utf-8") as f:
            f.write(
                "\n# ETL4 isolated local trial\nlisten_addresses = '127.0.0.1'\nport = 55433\nmax_connections = 30\nshared_buffers = '128MB'\nlog_min_error_statement = 'panic'\n"
            )
    if not (pgdata / "ETL4_OWNED_CLUSTER").exists():
        raise ValueError("Existing cluster lacks ownership marker")
    status = subprocess.run(
        [str(PG_BIN / "pg_ctl.exe"), "-D", str(pgdata), "status"], capture_output=True
    )
    if status.returncode:
        with socket.socket() as s:
            if s.connect_ex((c["host"], c["port"])) == 0:
                raise ValueError("Configured local port is already in use")
        command(
            [
                PG_BIN / "pg_ctl.exe",
                "-D",
                pgdata,
                "-l",
                settings.runtime_dir / "postgresql.log",
                "start",
                "-w",
                "-t",
                "30",
            ]
        )
    with connection(dbname="postgres") as conn:
        # Windows 返回的此路径使用系统代码页。 / Windows returns this path using the system code page.
        raw = bytes.fromhex(
            conn.execute(
                "SELECT encode(current_setting('data_directory')::bytea,'hex') AS path_hex"
            ).fetchone()["path_hex"]
        )
        try:
            decoded = raw.decode("utf-8")
        except UnicodeDecodeError:
            decoded = raw.decode("mbcs")
        actual = Path(decoded).resolve()
        if actual != pgdata.resolve():
            raise ValueError("Connected to an unexpected database cluster")
        version = conn.execute("SELECT version() AS version").fetchone()["version"]
    report(
        "7_environment.json",
        {
            "status": "passed",
            "postgres_version": version,
            "host": c["host"],
            "port": c["port"],
            "cluster_path": str(pgdata),
            "postgis_control": (PG_BIN.parent / "share/extension/postgis.control").read_text(
                encoding="utf-8"
            ),
        },
    )
    print(
        "Isolated PostgreSQL is ready on 127.0.0.1:55433; credentials remain in ignored runtime config",
        flush=True,
    )


def main():
    p = StepParser()
    p.add_argument("--runtime-only", action="store_true")
    p.add_argument("--inputs-only", action="store_true", help="Check inputs only and use the configured database instance")
    p.add_argument("--stop", action="store_true")
    args = p.parse_args()
    configure(args)
    if args.inputs_only and (args.runtime_only or args.stop):
        p.error("--inputs-only cannot be combined with --runtime-only or --stop")
    if args.stop:
        local_runtime("stop")
        return
    if not args.runtime_only:
        freeze_inputs()
    if args.inputs_only:
        return
    local_runtime("start")


if __name__ == "__main__":
    main_guard(main)
