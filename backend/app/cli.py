"""Operator CLI.

    python -m app.cli create-restaurant --code spice-garden --name "Spice Garden" \
        --owner-username owner --owner-name "Asha" [--owner-email asha@example.com]
    python -m app.cli apply-setup --code spice-garden --file setup.json [--dry-run]
    python -m app.cli qr-sheet --code spice-garden [--table T4] [--out qr-cards.html]
    python -m app.cli regenerate-qr --code spice-garden --table T4
    python -m app.cli show-pin --code spice-garden
    python -m app.cli pilot-report --code spice-garden [--days 7] [--out report.md]
    python -m app.cli purge

The owner password is read interactively (never from argv, which leaks into shell history and
process listings). `apply-setup` creates tables, staff, option groups and the menu from one file
(re-runnable; the format is in docs/08-pilot-playbook.md). `qr-sheet` writes printable table
cards (to stdout without --out); `regenerate-qr` replaces a lost or copied table card's code
(the old card stops working). `show-pin` prints the current table PIN and each table's link
(what a manager sees in the app). `pilot-report` summarises the pilot (orders, timings, PIN use,
problem reports). `purge` runs the data-retention job once (the API also runs it hourly).
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import sys
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import TextIO

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.errors import AppError
from app.core.net import RequestMeta
from app.core.permissions import permissions_for
from app.core.pin import PinCrypto
from app.core.security import passwords
from app.db.session import Database, bind_tenant
from app.models import Restaurant, User
from app.models.enums import Role
from app.repositories import resolvers
from app.services import (
    pilot_report,
    pin_service,
    qr_sheet,
    retention_service,
    setup_service,
    table_service,
)
from app.services.auth_service import StaffContext
from app.services.restaurant_service import create_restaurant_with_owner


def read_password_line(stream: TextIO) -> str:
    """First line of `stream`, without the newline or a leading byte-order mark (Windows
    PowerShell adds one to piped text, and it must never become part of a password)."""
    return stream.readline().rstrip("\r\n").removeprefix("\N{BYTE ORDER MARK}")


async def _create_restaurant(args: argparse.Namespace) -> int:
    if args.password_stdin:
        password = read_password_line(sys.stdin)
    else:
        password = getpass.getpass("Owner password: ")
        if password != getpass.getpass("Repeat password: "):
            print("Passwords do not match.", file=sys.stderr)
            return 1
    settings = get_settings()
    passwords.configure(settings)
    db = Database(settings)
    try:
        async with db.sessionmaker() as session:
            restaurant, owner = await create_restaurant_with_owner(
                session,
                PinCrypto(settings),
                code=args.code.strip().lower(),
                name=args.name.strip(),
                owner_username=args.owner_username.strip().lower(),
                owner_display_name=args.owner_name.strip(),
                owner_email=args.owner_email.strip().lower() if args.owner_email else None,
                owner_password=password,
            )
            await session.commit()
    except AppError as exc:
        print(f"Error: {exc.message}", file=sys.stderr)
        return 1
    finally:
        await db.dispose()
    print(
        f"Created restaurant {restaurant.code!r} ({restaurant.id}) with owner {owner.username!r}."
    )
    return 0


class OperatorError(Exception):
    """A problem worth one line on stderr (not a traceback)."""


Work = Callable[[AsyncSession, StaffContext, Settings], Awaitable[int]]


async def _operator(session: AsyncSession, code: str) -> StaffContext:
    """Bind `session` to the restaurant with this code and act as its owner (the audit log
    records the owner's account, with a `cli-` request id)."""
    restaurant_id = await resolvers.resolve_restaurant_code(session, code.strip().lower())
    if restaurant_id is None:
        raise OperatorError(f"No restaurant with code {code!r}.")
    await bind_tenant(session, restaurant_id)
    restaurant = await session.scalar(select(Restaurant).where(Restaurant.id == restaurant_id))
    owner = await session.scalar(
        select(User).where(
            User.restaurant_id == restaurant_id,
            User.role == Role.OWNER.value,
            User.deleted_at.is_(None),
        )
    )
    if restaurant is None or owner is None:
        raise OperatorError(f"Restaurant {code!r} has no owner account.")
    return StaffContext(
        user=owner,
        restaurant=restaurant,
        session_id=uuid.uuid4(),
        permissions=permissions_for(Role.OWNER.value),
    )


async def _with_restaurant(code: str, work: Work) -> int:
    settings = get_settings()
    passwords.configure(settings)
    db = Database(settings)
    try:
        async with db.sessionmaker() as session:
            ctx = await _operator(session, code)
            return await work(session, ctx, settings)
    except OperatorError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except AppError as exc:
        print(f"Error: {exc.message}", file=sys.stderr)
        return 1
    finally:
        await db.dispose()


def load_setup(path: Path) -> setup_service.SetupFile:
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except OSError as exc:
        raise OperatorError(f"Cannot read {path}: {exc.strerror}") from exc
    except json.JSONDecodeError as exc:
        raise OperatorError(f"{path} is not valid JSON (line {exc.lineno}): {exc.msg}") from exc
    try:
        return setup_service.SetupFile.model_validate(data)
    except ValidationError as exc:
        problems = [
            f"  {'.'.join(str(part) for part in error['loc']) or '(file)'}: {error['msg']}"
            for error in exc.errors()
        ]
        raise OperatorError(f"{path} has problems:\n" + "\n".join(problems[:30])) from exc


async def _apply_setup(args: argparse.Namespace) -> int:
    try:
        setup = load_setup(Path(args.file))
    except OperatorError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    async def work(session: AsyncSession, ctx: StaffContext, settings: Settings) -> int:
        result = await setup_service.apply(session, ctx, settings, setup, dry_run=args.dry_run)
        created, updated = (
            ("Would create", "Would update") if args.dry_run else ("Created", "Updated")
        )
        for line in result.created:
            print(f"{created}: {line}")
        for line in result.updated:
            print(f"{updated}: {line}")
        print(f"Already up to date: {result.unchanged}")
        if result.new_staff:
            print("\nTemporary passwords (shown once; each person must change theirs at first")
            print("sign-in). Hand them over privately, then clear this screen:")
            for username, role, password in result.new_staff:
                print(f"  {username:<20} {role:<8} {password}")
        if args.dry_run:
            print("\nDry run: nothing was changed.")
        return 0

    return await _with_restaurant(args.code, work)


def _pick_table(tables: list[table_service.TableView], label: str) -> table_service.TableView:
    wanted = label.strip().casefold()
    for view in tables:
        if view.table.label.casefold() == wanted:
            return view
    known = ", ".join(v.table.label for v in tables) or "none"
    raise OperatorError(f"No table {label!r}. Tables: {known}.")


async def _qr_sheet(args: argparse.Namespace) -> int:
    async def work(session: AsyncSession, ctx: StaffContext, settings: Settings) -> int:
        tables = await table_service.list_tables(session, ctx.restaurant_id)
        if args.table:
            tables = [_pick_table(tables, label) for label in args.table]
        options = {"hint": args.hint} if args.hint else {}
        page = qr_sheet.render(ctx.restaurant, tables, settings, **options)
        if (
            not args.out
        ):  # e.g. docker compose exec -T api python -m app.cli qr-sheet ... > cards.html
            sys.stdout.write(page)
            return 0
        await asyncio.to_thread(Path(args.out).write_text, page, encoding="utf-8")
        print(f"Wrote {args.out}: open it in a browser and print on A4 at 100% scale.")
        return 0

    return await _with_restaurant(args.code, work)


async def _regenerate_qr(args: argparse.Namespace) -> int:
    async def work(session: AsyncSession, ctx: StaffContext, settings: Settings) -> int:
        view = _pick_table(await table_service.list_tables(session, ctx.restaurant_id), args.table)
        meta = RequestMeta(
            ip="127.0.0.1", user_agent="app.cli regenerate-qr", request_id="cli-regenerate-qr"
        )
        token = await table_service.regenerate_qr(session, ctx, meta, view.table.id)
        print(f"Table {view.table.label}: new link {table_service.qr_url(settings, token)}")
        print("The old card no longer works. Print the new one with qr-sheet --table.")
        return 0

    return await _with_restaurant(args.code, work)


async def _show_pin(args: argparse.Namespace) -> int:
    async def work(session: AsyncSession, ctx: StaffContext, settings: Settings) -> int:
        crypto = PinCrypto(settings)
        current = await pin_service.ensure_current_pin(session, crypto, ctx.restaurant_id)
        await session.refresh(ctx.restaurant)
        info = pin_service.describe(crypto, ctx.restaurant, current)
        await session.commit()  # keeps a self-healed initial PIN, as the API does
        when = (
            info.next_rotation_at.astimezone().strftime("%d %b %H:%M")
            if info.next_rotation_at
            else "only when changed by hand"
        )
        print(f"Table PIN: {info.pin}   (next change: {when})")
        for view in await table_service.list_tables(session, ctx.restaurant_id):
            if view.table.is_active and view.qr_token is not None:
                link = table_service.qr_url(settings, view.qr_token)
                print(f"  {view.table.label:<10} {link}")
        return 0

    return await _with_restaurant(args.code, work)


async def _pilot_report(args: argparse.Namespace) -> int:
    async def work(session: AsyncSession, ctx: StaffContext, settings: Settings) -> int:
        report = await pilot_report.build(session, ctx.restaurant, days=args.days)
        if args.out:
            await asyncio.to_thread(Path(args.out).write_text, report, encoding="utf-8")
            print(f"Wrote {args.out}.")
        else:
            sys.stdout.write(report)
        return 0

    return await _with_restaurant(args.code, work)


async def _purge() -> int:
    settings = get_settings()
    db = Database(settings)
    try:
        purged = await retention_service.purge_expired(db, settings)
    finally:
        await db.dispose()
    if purged is None:
        print("Another worker is running the retention job right now; nothing done.")
        return 0
    for name, count in purged.as_dict().items():
        print(f"{name}: {count} deleted")
    return 0


def main(argv: list[str] | None = None) -> int:
    # Piped output on Windows uses the ANSI code page: a dish name in Hindi or an arrow in the
    # report must not crash the command (use --out for a faithful UTF-8 file).
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create-restaurant", help="create a restaurant and its owner account")
    create.add_argument("--code", required=True, help="restaurant code used at staff login")
    create.add_argument("--name", required=True)
    create.add_argument("--owner-username", required=True)
    create.add_argument("--owner-name", required=True)
    create.add_argument("--owner-email")
    create.add_argument(
        "--password-stdin",
        action="store_true",
        help="read the owner password from the first line of stdin (for automation)",
    )
    setup = sub.add_parser("apply-setup", help="create tables, staff and the menu from a file")
    setup.add_argument("--code", required=True)
    setup.add_argument("--file", required=True, help="setup file (JSON)")
    setup.add_argument("--dry-run", action="store_true", help="only show what would change")
    sheet = sub.add_parser("qr-sheet", help="write printable QR cards for every table")
    sheet.add_argument("--code", required=True)
    sheet.add_argument("--table", action="append", help="only this table (repeatable)")
    sheet.add_argument("--out", help="HTML file to write (default: print it)")
    sheet.add_argument("--hint", help="the PIN line on each card (default: ask a staff member)")
    regen = sub.add_parser("regenerate-qr", help="replace a table's QR code (lost/copied card)")
    regen.add_argument("--code", required=True)
    regen.add_argument("--table", required=True, help="the table's label, e.g. T4")
    show = sub.add_parser("show-pin", help="print the current table PIN and table links")
    show.add_argument("--code", required=True)
    report = sub.add_parser("pilot-report", help="summarise the pilot for one restaurant")
    report.add_argument("--code", required=True)
    report.add_argument("--days", type=int, default=7, choices=range(1, 91), metavar="1-90")
    report.add_argument("--out", help="Markdown file to write (default: print it)")
    sub.add_parser("purge", help="delete expired sessions and old audit logs now")
    args = parser.parse_args(argv)
    if args.command == "create-restaurant":
        return asyncio.run(_create_restaurant(args))
    if args.command == "apply-setup":
        return asyncio.run(_apply_setup(args))
    if args.command == "qr-sheet":
        return asyncio.run(_qr_sheet(args))
    if args.command == "regenerate-qr":
        return asyncio.run(_regenerate_qr(args))
    if args.command == "show-pin":
        return asyncio.run(_show_pin(args))
    if args.command == "pilot-report":
        return asyncio.run(_pilot_report(args))
    if args.command == "purge":
        return asyncio.run(_purge())
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
