"""Check a deployed central service from the outside: is HTTPS set up the way it should be?

    python scripts/check_tls.py https://central.bank.example
    python scripts/check_tls.py https://localhost:18443 --insecure --http-port 18080     (test stack)
    python scripts/check_tls.py https://central.bank.example --cafile bank-root-ca.pem   (private CA)

Checks: the certificate is trusted for the name and not about to expire; plain HTTP is redirected
to HTTPS; the security headers are present; an oversized upload is stopped by the proxy; the
admin session cookie is Secure, HttpOnly and SameSite=Strict. Exit code 0 when everything passes.
"""
from __future__ import annotations

import argparse
import http.client
import json
import socket
import ssl
import sys
from datetime import datetime, timezone
from urllib.parse import urlparse

BIG = 2_000_000          # bytes: above the proxy's 1 MB cap
MIN_DAYS_LEFT = 14


def context(insecure: bool, cafile: str | None) -> ssl.SSLContext:
    if insecure:
        ctx = ssl.create_default_context()
        ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
        return ctx
    return ssl.create_default_context(cafile=cafile)


def certificate_days_left(host: str, port: int, ctx: ssl.SSLContext) -> float:
    with socket.create_connection((host, port), timeout=10) as raw:
        with ctx.wrap_socket(raw, server_hostname=host) as tls:
            cert = tls.getpeercert()
    if not cert:        # an unverified connection returns no parsed certificate
        return float("inf")
    expires = datetime.fromtimestamp(ssl.cert_time_to_seconds(cert["notAfter"]), timezone.utc)
    return (expires - datetime.now(timezone.utc)).total_seconds() / 86400


def request(scheme: str, host: str, port: int, method: str, path: str, ctx, body: bytes | None = None,
            headers: dict | None = None):
    conn = (http.client.HTTPSConnection(host, port, timeout=15, context=ctx) if scheme == "https"
            else http.client.HTTPConnection(host, port, timeout=15))
    try:
        conn.request(method, path, body=body, headers=headers or {})
        resp = conn.getresponse()
        resp.read()
        return resp
    finally:
        conn.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("url")
    ap.add_argument("--insecure", action="store_true", help="do not verify the certificate (test stacks only)")
    ap.add_argument("--cafile", help="trust this CA file (a private CA the PCs also trust)")
    ap.add_argument("--http-port", type=int, default=80)
    ap.add_argument("--login", help="USER:PASSWORD of an admin, to check the session cookie flags")
    args = ap.parse_args(argv)
    u = urlparse(args.url)
    host, port = u.hostname, u.port or 443
    ctx = context(args.insecure, args.cafile)
    results: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        results.append((name, ok, detail))
        print(f"{'ok  ' if ok else 'FAIL'}  {name}{(': ' + detail) if detail else ''}")

    try:
        left = certificate_days_left(host, port, ctx)
        if args.insecure:
            check("certificate check skipped (--insecure): do not use for the real domain", True)
        else:
            check("certificate is trusted for this name", True)
            check("certificate is not about to expire", left > MIN_DAYS_LEFT, f"{left:.0f} days left")
    except (ssl.SSLError, OSError) as e:
        check("certificate is trusted for this name", False, str(e))
        return 1

    health = request("https", host, port, "GET", "/healthz", ctx)
    check("the service answers over HTTPS", health.status == 200, f"status {health.status}")
    for header, expect in (("Strict-Transport-Security", "max-age"), ("X-Content-Type-Options", "nosniff"),
                           ("X-Frame-Options", "DENY"), ("Referrer-Policy", "no-referrer")):
        value = health.getheader(header) or ""
        check(f"header {header}", expect.lower() in value.lower(), value or "missing")

    try:
        plain = request("http", host, args.http_port, "GET", "/healthz", None)
        where = plain.getheader("Location") or ""
        check("plain HTTP is redirected to HTTPS", plain.status in (301, 302, 307, 308) and where.startswith("https://"),
              f"{plain.status} {where}")
    except OSError as e:
        check("plain HTTP is redirected to HTTPS", False, str(e))

    big = request("https", host, port, "POST", "/pcs/register", ctx, body=b"a" * BIG,
                  headers={"Content-Type": "application/json"})
    check("an oversized upload is stopped by the proxy", big.status == 413, f"status {big.status}")

    if args.login:
        user, _, password = args.login.partition(":")
        resp = request("https", host, port, "POST", "/admin/login", ctx,
                       body=json.dumps({"username": user, "password": password}).encode(),
                       headers={"Content-Type": "application/json"})
        cookie = (resp.getheader("Set-Cookie") or "").lower()
        check("session cookie is Secure, HttpOnly, SameSite=Strict",
              resp.status == 200 and all(f in cookie for f in ("secure", "httponly", "samesite=strict")),
              f"status {resp.status}")
    failed = [n for n, ok, _ in results if not ok]
    print("\nTLS CHECK", "PASSED" if not failed else f"FAILED ({len(failed)})")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
