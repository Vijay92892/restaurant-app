"""Check a live deployment from the outside, the way a phone or an attacker sees it.

    python scripts/check_deployment.py https://order.example.in
    python scripts/check_deployment.py https://order.example.in --pin 1234 --table <QR link>
    python scripts/check_deployment.py https://localhost:8443 --insecure --http-port 8088

Run it after every deployment. It checks what the reverse proxy and the server configuration
must get right (docs/06 §7, docs/07): HTTPS and its redirect, security headers on the customer
page and the API, caching, API docs switched off, browser origins refused, body and WebSocket
size limits, and that the live-update socket works through the proxy. With --table (a table's QR
link) and --pin it also checks the customer session cookie's flags; that opens one table session.
Changes nothing else. Exit code 0 when every check passes.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import ssl
import sys
from dataclasses import dataclass, field
from urllib.parse import urlsplit

import httpx
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidStatus

MISSING_QR = "A" * 22  # well-formed, never issued
MIN_HSTS_SECONDS = 15_552_000  # 180 days


@dataclass
class Report:
    failures: list[str] = field(default_factory=list)
    passed: int = 0

    def check(self, ok: bool, what: str, detail: str = "") -> bool:
        if ok:
            self.passed += 1
            print(f"  ok    {what}")
        else:
            self.failures.append(what)
            print(f"  FAIL  {what}{f' — {detail}' if detail else ''}")
        return ok


def hsts_ok(value: str | None) -> bool:
    match = re.search(r"max-age=(\d+)", value or "")
    return bool(match) and int(match.group(1)) >= MIN_HSTS_SECONDS


def no_version_leak(response: httpx.Response) -> bool:
    server = response.headers.get("server", "")
    return not re.search(r"\d", server) and "uvicorn" not in server.lower()


def check_page(r: Report, client: httpx.Client) -> None:
    print("Customer page")
    page = client.get(f"/t/{MISSING_QR}")
    r.check(
        page.status_code == 200 and "text/html" in page.headers.get("content-type", ""),
        "a table link serves the ordering page",
        f"{page.status_code}",
    )
    h = page.headers
    r.check(
        hsts_ok(h.get("strict-transport-security")),
        "Strict-Transport-Security (≥180 days)",
        h.get("strict-transport-security", "missing"),
    )
    r.check(h.get("x-content-type-options") == "nosniff", "X-Content-Type-Options: nosniff")
    r.check(
        h.get("referrer-policy") == "no-referrer",
        "Referrer-Policy: no-referrer",
        h.get("referrer-policy", "missing"),
    )
    r.check(
        "frame-ancestors 'none'" in h.get("content-security-policy", ""),
        "framing forbidden (CSP frame-ancestors 'none')",
        h.get("content-security-policy", "missing"),
    )
    r.check("default-src 'self'" in page.text, "the page's own Content-Security-Policy is present")
    r.check(
        "no-cache" in h.get("cache-control", ""),
        "the page is revalidated (Cache-Control: no-cache)",
        h.get("cache-control", "missing"),
    )
    r.check(no_version_leak(page), "no server software/version in headers", h.get("server", ""))
    script = re.search(r'src="(/assets/[^"]+\.js)"', page.text)
    if r.check(script is not None, "the page references its script bundle"):
        asset = client.get(script.group(1))
        r.check(
            asset.status_code == 200 and "immutable" in asset.headers.get("cache-control", ""),
            "bundles are cached long-term (immutable)",
            asset.headers.get("cache-control", "missing"),
        )
    reset = client.get("/reset-password")
    r.check(
        reset.status_code == 200 and "text/html" in reset.headers.get("content-type", ""),
        "the password-reset page is served",
    )


def check_api(r: Report, client: httpx.Client, base: str) -> None:
    print("API")
    health = client.get("/healthz")
    r.check(health.status_code == 200 and health.json() == {"status": "ok"}, "/healthz answers")
    missing = client.get(f"/api/v1/public/tables/{MISSING_QR}")
    body = missing.json() if "json" in missing.headers.get("content-type", "") else {}
    r.check(
        missing.status_code == 404 and "error" in body,
        "unknown QR codes get the generic 404",
        f"{missing.status_code}",
    )
    h = missing.headers
    r.check(hsts_ok(h.get("strict-transport-security")), "API sends Strict-Transport-Security")
    r.check(h.get("x-content-type-options") == "nosniff", "API sends nosniff")
    r.check(
        h.get("cache-control") == "no-store",
        "API responses are not cached",
        h.get("cache-control", "missing"),
    )
    r.check(no_version_leak(missing), "API reveals no server version", h.get("server", ""))
    for path in ("/docs", "/openapi.json", "/api/v1/openapi.json"):
        response = client.get(path)
        r.check(
            "swagger-ui" not in response.text and '"openapi"' not in response.text,
            f"API documentation is off ({path})",
        )
    evil = {"Origin": "https://evil.example"}
    cors = client.get(f"/api/v1/public/tables/{MISSING_QR}", headers=evil)
    r.check("access-control-allow-origin" not in cors.headers, "other websites get no CORS access")
    forged = client.post(
        f"/api/v1/public/tables/{MISSING_QR}/session", json={"pin": "0000"}, headers=evil
    )
    r.check(
        forged.status_code == 403,
        "customer writes from other websites are refused",
        f"{forged.status_code}",
    )
    own = client.post(
        f"/api/v1/public/tables/{MISSING_QR}/session",
        json={"pin": "0000"},
        headers={"Origin": base},
    )
    r.check(
        own.status_code != 403,
        "customer writes from this site are allowed (PUBLIC_WEB_ORIGINS)",
        f"{own.status_code}",
    )
    big = client.post(
        "/api/v1/auth/login",
        content=b"{" + b" " * 2_000_000 + b"}",
        headers={"Content-Type": "application/json"},
    )
    r.check(
        big.status_code == 413, "oversized request bodies are refused (413)", f"{big.status_code}"
    )


def check_redirect(r: Report, base: str, http_port: int | None) -> None:
    print("HTTP → HTTPS")
    parts = urlsplit(base)
    port = f":{http_port}" if http_port else ""
    try:
        response = httpx.get(
            f"http://{parts.hostname}{port}/t/{MISSING_QR}", follow_redirects=False, timeout=10
        )
    except httpx.HTTPError as exc:
        r.check(False, "plain http redirects to https", str(exc))
        return
    location = response.headers.get("location", "")
    r.check(
        response.status_code in (301, 302, 307, 308) and location.startswith("https://"),
        "plain http redirects to https",
        f"{response.status_code} {location}",
    )


async def check_socket(r: Report, base: str, context: ssl.SSLContext | None) -> None:
    print("Live updates (WebSocket)")
    url = base.replace("https://", "wss://", 1) + "/api/v1/ws/staff"
    try:
        async with connect(url, ssl=context, open_timeout=10, max_size=None) as socket:
            await socket.send(json.dumps({"type": "auth", "access_token": "not-a-token"}))
            try:
                await asyncio.wait_for(socket.recv(), 10)
                code = None
            except ConnectionClosed as closed:
                code = closed.rcvd.code if closed.rcvd else None
        r.check(
            code == 4401,
            "the socket connects through the proxy and refuses bad tokens (4401)",
            f"close code {code}",
        )
    except Exception as exc:  # noqa: BLE001 - report any failure to connect
        r.check(False, "the socket connects through the proxy", repr(exc))
        return
    try:
        async with connect(url, ssl=context, open_timeout=10, max_size=None) as socket:
            await socket.send("x" * 100_000)
            try:
                await asyncio.wait_for(socket.recv(), 10)
                code = None
            except ConnectionClosed as closed:
                code = closed.rcvd.code if closed.rcvd else None
        r.check(code == 1009, "oversized socket messages are refused (1009)", f"close code {code}")
    except Exception as exc:  # noqa: BLE001
        r.check(False, "oversized socket messages are refused (1009)", repr(exc))
    # The server refuses before accepting: the handshake fails with 403 (or, with some servers,
    # the socket opens and closes at once with 4403).
    browser = {"Origin": "https://evil.example"}
    what = "pages on other websites cannot open the socket"
    try:
        async with connect(url, ssl=context, open_timeout=10, additional_headers=browser) as socket:
            try:
                await asyncio.wait_for(socket.recv(), 10)
                code = None
            except ConnectionClosed as closed:
                code = closed.rcvd.code if closed.rcvd else None
        r.check(code == 4403, what, f"close code {code}")
    except InvalidStatus as refused:
        status = refused.response.status_code
        r.check(status == 403, what, f"handshake answered {status}")
    except Exception as exc:  # noqa: BLE001
        r.check(False, what, repr(exc))


def check_cookie(r: Report, client: httpx.Client, base: str, qr: str, pin: str) -> None:
    print("Customer session cookie")
    response = client.post(
        f"/api/v1/public/tables/{qr}/session", json={"pin": pin}, headers={"Origin": base}
    )
    if not r.check(
        response.status_code == 200, "the table PIN opens a session", f"{response.status_code}"
    ):
        return
    cookie = response.headers.get("set-cookie", "")
    lower = cookie.lower()
    r.check(
        cookie.startswith("__Host-"),
        "__Host- cookie (this site only, path /)",
        cookie.split("=")[0],
    )
    r.check(
        "secure" in lower and "httponly" in lower and "samesite=strict" in lower,
        "Secure, HttpOnly, SameSite=Strict",
    )
    client.request("DELETE", "/api/v1/public/session", headers={"Origin": base})


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("url", help="the public address, e.g. https://order.example.in")
    parser.add_argument(
        "--insecure", action="store_true", help="accept an untrusted certificate (rehearsals)"
    )
    parser.add_argument("--http-port", type=int, help="plain-http port, if not 80 (rehearsals)")
    parser.add_argument("--skip-redirect", action="store_true", help="skip the http→https check")
    # The whole link, not the bare token: tokens may start with "-", which looks like an option.
    parser.add_argument("--table", help="a table's QR link, to check the session cookie")
    parser.add_argument("--pin", help="that restaurant's current table PIN")
    args = parser.parse_args(argv)

    base = args.url.strip().rstrip("/")
    # Pasted values often carry stray whitespace or a Windows line ending.
    qr = (args.table or "").strip().rstrip("/").rsplit("/t/", 1)[-1]
    pin = (args.pin or "").strip()
    if not base.startswith("https://"):
        print("The address must start with https://", file=sys.stderr)
        return 2
    context = None
    if args.insecure:
        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE

    report = Report()
    with httpx.Client(
        base_url=base, verify=not args.insecure, follow_redirects=False, timeout=20
    ) as client:
        try:
            check_page(report, client)
            check_api(report, client, base)
            if qr and pin:
                check_cookie(report, client, base, qr, pin)
        except httpx.HTTPError as exc:
            report.check(False, "the site answers over HTTPS", repr(exc))
    if not args.skip_redirect:
        check_redirect(report, base, args.http_port)
    asyncio.run(check_socket(report, base, context))

    print()
    if report.failures:
        print(f"{len(report.failures)} of {report.passed + len(report.failures)} checks FAILED:")
        for failure in report.failures:
            print(f"  - {failure}")
        return 1
    print(f"All {report.passed} checks passed.")
    return 0


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    raise SystemExit(main())
