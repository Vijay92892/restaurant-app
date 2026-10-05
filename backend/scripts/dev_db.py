"""Zero-install local PostgreSQL 16 for development (binaries from the `pgserver` wheel).

    python scripts/dev_db.py           # start (or reuse) the dev database, migrate, update .env
    python scripts/dev_db.py --stop    # stop it
    python scripts/dev_db.py --status  # is it running?

Postgres runs as a background service *detached from your terminal*, so pressing Ctrl+C to stop
uvicorn in the same window cannot crash it. It listens on 127.0.0.1:54329 (override with
--port), so .env stays stable across restarts. Data: backend/.devdb, log: backend/.devdb.log.

Creates the same two-role layout as production (see scripts/bootstrap_db.sql):
  restaurant_owner  owns the schema, runs migrations
  restaurant_app    runtime role used by the API (no superuser, no BYPASSRLS, owns nothing)

Not for production use.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import secrets
import subprocess
import sys
from pathlib import Path

import asyncpg
import pgserver
from alembic.config import Config

from alembic import command

BACKEND = Path(__file__).resolve().parents[1]
DATA_DIR = BACKEND / ".devdb"
LOG_FILE = BACKEND / ".devdb.log"  # outside DATA_DIR: Windows locks open files
ENV_FILE = BACKEND / ".env"
BIN = Path(pgserver.__file__).parent / "pginstall" / "bin"
DEFAULT_PORT = 54329
OWNER, APP = "restaurant_owner", "restaurant_app"
DATABASE = "restaurant"  # overridable with --database (e.g. a separate end-to-end test DB)
# Development conveniences (production settings validation rejects/overrides as needed).
DEV_DEFAULTS = {"REGISTRATION_MODE": "open"}


def _exe(name: str) -> str:
    return str(BIN / (f"{name}.exe" if os.name == "nt" else name))


def _run(*args: str, detach: bool = False, check: bool = True) -> subprocess.CompletedProcess[str]:
    kwargs: dict[str, object] = {"capture_output": True, "text": True}
    if detach:
        if os.name == "nt":
            # Own process group and its own *windowless* console: Ctrl+C in the user's terminal
            # never reaches it, and there is no window whose closing would stop the database.
            # (With DETACHED_PROCESS, pg_ctl's cmd.exe opened a new console window instead.)
            kwargs["creationflags"] = (
                subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
            )
        else:
            kwargs["start_new_session"] = True
        # pg_ctl must not inherit our pipes, or it would keep them (and us) open.
        kwargs.update(capture_output=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    result = subprocess.run(list(args), **kwargs)  # type: ignore[call-overload]  # noqa: S603
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip() if not detach else ""
        raise SystemExit(
            f"{Path(args[0]).name} failed ({result.returncode}). {detail}\nSee {LOG_FILE}"
        )
    return result


def _running_port() -> int | None:
    """Port of the running dev server, or None if it isn't running."""
    if _run(_exe("pg_ctl"), "-D", str(DATA_DIR), "status", check=False).returncode != 0:
        return None
    lines = (DATA_DIR / "postmaster.pid").read_text().splitlines()
    return int(lines[3])


def _ensure_initialised() -> None:
    if (DATA_DIR / "PG_VERSION").exists():
        return
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    _run(
        _exe("initdb"),
        "-D",
        str(DATA_DIR),
        "-U",
        "postgres",
        "--auth=trust",
        "--encoding=UTF8",
        "--no-locale",
    )


def _start(port: int) -> int:
    running = _running_port()
    if running is not None:
        return running
    _run(
        _exe("pg_ctl"),
        "-D",
        str(DATA_DIR),
        "-l",
        str(LOG_FILE),
        "-w",
        "-t",
        "120",
        "-o",
        f"-h 127.0.0.1 -p {port}",
        "start",
        detach=True,
    )
    started = _running_port()
    if started is None:
        raise SystemExit(f"PostgreSQL did not start. See {LOG_FILE}")
    return started


async def _bootstrap(port: int) -> None:
    conn = await asyncpg.connect(f"postgresql://postgres@127.0.0.1:{port}/postgres")
    try:
        roles = {r["rolname"] for r in await conn.fetch("SELECT rolname FROM pg_roles")}
        if OWNER not in roles:
            await conn.execute(f"CREATE ROLE {OWNER} LOGIN")
        if APP not in roles:
            await conn.execute(
                f"CREATE ROLE {APP} LOGIN NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE"
            )
        if not await conn.fetchval("SELECT 1 FROM pg_database WHERE datname = $1", DATABASE):
            await conn.execute(f"CREATE DATABASE {DATABASE} OWNER {OWNER}")
            await conn.execute(f"REVOKE ALL ON DATABASE {DATABASE} FROM PUBLIC")
            await conn.execute(f"GRANT CONNECT ON DATABASE {DATABASE} TO {OWNER}, {APP}")
    finally:
        await conn.close()
    conn = await asyncpg.connect(f"postgresql://postgres@127.0.0.1:{port}/{DATABASE}")
    try:
        await conn.execute("REVOKE ALL ON SCHEMA public FROM PUBLIC")
        await conn.execute(f"ALTER SCHEMA public OWNER TO {OWNER}")
    finally:
        await conn.close()


def _write_env(port: int) -> None:
    urls = {
        "DATABASE_URL": f"postgresql+asyncpg://{APP}@127.0.0.1:{port}/{DATABASE}",
        "MIGRATION_DATABASE_URL": f"postgresql+asyncpg://{OWNER}@127.0.0.1:{port}/{DATABASE}",
    }
    if ENV_FILE.exists():
        text = ENV_FILE.read_text(encoding="utf-8")
        for key, value in urls.items():
            line = f"{key}={value}"
            pattern = re.compile(rf"^{key}=.*$", re.M)
            text = pattern.sub(line, text) if pattern.search(text) else text + f"\n{line}\n"
        for key, value in DEV_DEFAULTS.items():  # added only if missing; never overwritten
            if not re.search(rf"^{key}=", text, re.M):
                text = text.rstrip("\n") + f"\n{key}={value}\n"
        ENV_FILE.write_text(text, encoding="utf-8")
        return
    ENV_FILE.write_text(
        "\n".join(
            [
                "ENV=development",
                *(f"{k}={v}" for k, v in urls.items()),
                f"APP_DB_ROLE={APP}",
                f"JWT_SECRET={secrets.token_urlsafe(48)}",
                f"PIN_HMAC_KEY={secrets.token_urlsafe(48)}",
                f"PIN_ENC_KEY={secrets.token_urlsafe(48)}",
                "RATE_LIMIT_BACKEND=memory",
                "MAIL_BACKEND=console",
                *(f"{k}={v}" for k, v in DEV_DEFAULTS.items()),
                "",
            ]
        ),
        encoding="utf-8",
    )


def _migrate(port: int) -> None:
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    cfg.attributes["database_url"] = f"postgresql+asyncpg://{OWNER}@127.0.0.1:{port}/{DATABASE}"
    cfg.attributes["app_db_role"] = APP
    command.upgrade(cfg, "head")


def main() -> int:
    global DATABASE
    parser = argparse.ArgumentParser()
    parser.add_argument("--stop", action="store_true", help="stop the dev database")
    parser.add_argument("--status", action="store_true", help="show whether it is running")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--database", default=DATABASE, help="database name (default: restaurant)")
    parser.add_argument("--no-env", action="store_true", help="do not create/update .env")
    args = parser.parse_args()
    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", args.database):
        raise SystemExit("--database must be a plain lowercase identifier")
    DATABASE = args.database

    if args.status:
        port = _running_port() if (DATA_DIR / "PG_VERSION").exists() else None
        print(f"running on 127.0.0.1:{port}" if port else "not running")
        return 0
    if args.stop:
        if (DATA_DIR / "PG_VERSION").exists() and _running_port() is not None:
            _run(_exe("pg_ctl"), "-D", str(DATA_DIR), "-m", "fast", "-w", "stop")
        print("Dev database stopped.")
        return 0

    _ensure_initialised()
    port = _start(args.port)
    asyncio.run(_bootstrap(port))
    if not args.no_env:
        _write_env(port)
    _migrate(port)
    note = "" if args.no_env else " .env updated."
    print(f"Dev database {DATABASE!r} ready on 127.0.0.1:{port} (data: {DATA_DIR}).{note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
