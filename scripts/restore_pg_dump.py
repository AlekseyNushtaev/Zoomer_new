"""
Восстановление PostgreSQL из pg_dump -Fc (.dump).

Использование:
  python scripts/restore_pg_dump.py
  python scripts/restore_pg_dump.py config_bd/zoomer.dump

Перед запуском остановите бота (main.py), иначе активные соединения мешают DROP.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent


def _bin(name: str) -> str:
    custom = (os.environ.get(f"PG_{name.upper()}_BIN") or os.environ.get("PG_DUMP_BIN") or "").strip()
    if custom and name == "restore":
        return custom.replace("pg_dump", "pg_restore")
    if custom and name == "dump":
        return custom
    found = shutil.which(f"pg_{name}")
    return found or f"pg_{name}"


def main() -> int:
    load_dotenv(ROOT / ".env")
    db = os.environ.get("POSTGRES_DB")
    user = os.environ.get("POSTGRES_USER")
    password = os.environ.get("POSTGRES_PASSWORD")
    host = os.environ.get("POSTGRES_HOST", "localhost")
    port = os.environ.get("POSTGRES_PORT", "5432")

    if not all([db, user, password]):
        print("Set POSTGRES_DB, POSTGRES_USER, POSTGRES_PASSWORD in .env", file=sys.stderr)
        return 1

    dump_arg = sys.argv[1] if len(sys.argv) > 1 else "config_bd/zoomer.dump"
    dump_path = Path(dump_arg)
    if not dump_path.is_file():
        print(f"Dump file not found: {dump_path}", file=sys.stderr)
        return 1

    env = {**os.environ, "PGPASSWORD": password}
    pg_restore = (os.environ.get("PG_RESTORE_BIN") or "").strip() or shutil.which("pg_restore") or "pg_restore"
    psql = (os.environ.get("PSQL_BIN") or "").strip() or shutil.which("psql") or "psql"

    size_mb = dump_path.stat().st_size / 1024 / 1024
    print(f"DB: {db}@{host}:{port}, dump: {dump_path} ({size_mb:.2f} MB)")

    term_sql = (
        f"SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
        f"WHERE datname = '{db}' AND pid <> pg_backend_pid();"
    )
    subprocess.run(
        [psql, "-h", host, "-p", str(port), "-U", user, "-d", "postgres", "-v", "ON_ERROR_STOP=1", "-c", term_sql],
        env=env,
        check=False,
    )

    print("pg_restore --clean --if-exists ...")
    proc = subprocess.run(
        [
            pg_restore,
            "-h",
            host,
            "-p",
            str(port),
            "-U",
            user,
            "-d",
            db,
            "--clean",
            "--if-exists",
            "--no-owner",
            "--no-privileges",
            "-v",
            str(dump_path.resolve()),
        ],
        env=env,
        capture_output=True,
        text=True,
    )
    if proc.stdout:
        print(proc.stdout[-8000:] if len(proc.stdout) > 8000 else proc.stdout)
    if proc.returncode != 0:
        err = proc.stderr or ""
        # pg_restore часто возвращает 1 из‑за harmless warnings
        if "errors ignored on restore" in err.lower() or proc.returncode == 1:
            print(err[-4000:] if len(err) > 4000 else err)
            if "FATAL" in err or "could not connect" in err.lower():
                print("Restore failed.", file=sys.stderr)
                return proc.returncode
            print("Done (pg_restore may have non-fatal warnings).")
            return 0
        print(err, file=sys.stderr)
        return proc.returncode

    print("Restore completed successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
