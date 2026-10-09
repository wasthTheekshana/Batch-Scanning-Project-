"""HTTPS client for dokscanner-central (spec: sync client).

stdlib only. TLS is always verified, redirects are never followed, response
bodies are size-capped, and every failure maps to one of three exceptions so
the sync layer can decide what to do (keep working / stop polling / report).
"""
from __future__ import annotations

import http.client
import json
import os
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable
from urllib.parse import quote, urlparse

MAX_RESPONSE_BYTES = 5 * 1024 * 1024
MIN_POLL, MAX_POLL = 30, 86400
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
LOOPBACK_ENV = "DOKSCANNER_CENTRAL_ALLOW_LOOPBACK_HTTP"

Transport = Callable[[str, str, dict, "bytes | None", float],
                     "tuple[int, bytes]"]


class CentralError(Exception):
    """Base class for central-service failures."""


class CentralUnavailable(CentralError):
    """Network error, timeout, 5xx/unexpected status, or a malformed reply."""


class CentralAuthError(CentralError):
    """401/403 on a PC call: the key is revoked, replaced, or not this PC's."""

    def __init__(self, status: int):
        super().__init__(f"central rejected this PC's key (HTTP {status})")
        self.status = status


class CentralRegistrationError(CentralError):
    """Enrollment code was invalid, used or expired."""


def allow_loopback_http() -> bool:
    return os.environ.get(LOOPBACK_ENV) == "1"


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None  # surface the 3xx as an HTTPError instead of following


def urllib_transport(method: str, url: str, headers: dict,
                     body: bytes | None, timeout: float) -> tuple[int, bytes]:
    ctx = ssl.create_default_context()
    opener = urllib.request.build_opener(
        _NoRedirect, urllib.request.HTTPSHandler(context=ctx))
    req = urllib.request.Request(url, data=body, method=method,
                                 headers=dict(headers))
    try:
        with opener.open(req, timeout=timeout) as resp:
            return resp.status, resp.read(MAX_RESPONSE_BYTES + 1)
    except urllib.error.HTTPError as e:
        try:
            return e.code, e.read(MAX_RESPONSE_BYTES + 1)
        finally:
            e.close()
    except (urllib.error.URLError, OSError, ssl.SSLError,
            http.client.HTTPException) as e:
        raise CentralUnavailable(f"{type(e).__name__}: {e}") from e


@dataclass
class RegisterResult:
    pc_id: str
    api_key: str
    poll_interval_seconds: int


@dataclass
class PullResult:
    pc_id: str
    poll_interval_seconds: int
    projects: list[dict]


def _clamp(v: int) -> int:
    return max(MIN_POLL, min(MAX_POLL, v))


def _json_object(status_ok_body: bytes) -> dict:
    if len(status_ok_body) > MAX_RESPONSE_BYTES:
        raise CentralUnavailable("response too large")
    try:
        data = json.loads(status_ok_body)
    except ValueError as e:
        raise CentralUnavailable("response is not valid JSON") from e
    if not isinstance(data, dict):
        raise CentralUnavailable("response is not a JSON object")
    return data


class CentralClient:
    def __init__(self, server_url: str, transport: Transport = urllib_transport,
                 timeout: float = 15.0, allow_insecure_loopback: bool = False):
        url = (server_url or "").strip().rstrip("/")
        parsed = urlparse(url)
        host = parsed.hostname
        if not host:
            raise ValueError("central server URL must be https://host[:port]")
        https_ok = parsed.scheme == "https"
        loopback_ok = (allow_insecure_loopback and parsed.scheme == "http"
                       and host in _LOOPBACK_HOSTS)
        if not (https_ok or loopback_ok):
            raise ValueError("central server URL must use https://")
        self.base = url
        self._transport = transport
        self._timeout = timeout

    def register(self, code: str) -> RegisterResult:
        body = json.dumps({"code": code}).encode("utf-8")
        status, data = self._transport(
            "POST", f"{self.base}/pcs/register",
            {"Content-Type": "application/json", "Accept": "application/json"},
            body, self._timeout)
        if status == 400:
            raise CentralRegistrationError(
                "invalid, used or expired enrollment code")
        if status != 200:
            raise CentralUnavailable(f"register failed (HTTP {status})")
        d = _json_object(data)
        pc_id, key, poll = d.get("pc_id"), d.get("api_key"), d.get(
            "poll_interval_seconds")
        if not (isinstance(pc_id, str) and pc_id and isinstance(key, str)
                and key and isinstance(poll, int) and not isinstance(poll, bool)):
            raise CentralUnavailable("malformed register response")
        return RegisterResult(pc_id, key, _clamp(poll))

    def pull_config(self, pc_id: str, api_key: str,
                    operator_id: str = "") -> PullResult:
        headers = {"Authorization": f"Bearer {api_key}",
                   "Accept": "application/json"}
        if operator_id:
            headers["X-Operator-Id"] = quote(operator_id, safe="")
        status, data = self._transport(
            "GET", f"{self.base}/pcs/{quote(pc_id, safe='')}/config",
            headers, None, self._timeout)
        if status in (401, 403):
            raise CentralAuthError(status)
        if status != 200:
            raise CentralUnavailable(f"pull failed (HTTP {status})")
        d = _json_object(data)
        pid, poll, projects = d.get("pc_id"), d.get("poll_interval_seconds"), \
            d.get("projects")
        if not (isinstance(pid, str) and isinstance(poll, int)
                and not isinstance(poll, bool) and isinstance(projects, list)
                and all(isinstance(p, dict) for p in projects)):
            raise CentralUnavailable("malformed config response")
        return PullResult(pid, _clamp(poll), projects)
