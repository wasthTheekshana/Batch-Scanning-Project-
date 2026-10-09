import http.server
import json
import ssl
import threading
import urllib.error
import urllib.request

import pytest

from core.central_client import (MAX_RESPONSE_BYTES, CentralAuthError,
                                 CentralClient, CentralRegistrationError,
                                 CentralUnavailable, urllib_transport)


class FakeTransport:
    def __init__(self, status=200, payload=None, raw=None, exc=None):
        self.status, self.payload, self.raw, self.exc = status, payload, raw, exc
        self.calls = []

    def __call__(self, method, url, headers, body, timeout):
        self.calls.append((method, url, dict(headers), body, timeout))
        if self.exc:
            raise self.exc
        data = self.raw if self.raw is not None else json.dumps(self.payload).encode()
        return self.status, data


PULL_OK = {"pc_id": "pc-1", "poll_interval_seconds": 300,
           "projects": [{"project_id": "p1", "name": "Inv", "version": 2,
                         "config": {"a": 1}}]}


def client(t, **kw):
    return CentralClient("https://central.example", transport=t, **kw)


# ---- URL policy ---------------------------------------------------------------
@pytest.mark.parametrize("url", ["http://central.example", "ftp://x", "central.example",
                                 "", "https://", "http://127.0.0.1.evil.com"])
def test_rejects_non_https_urls(url):
    with pytest.raises(ValueError):
        CentralClient(url, transport=FakeTransport())


def test_http_allowed_only_for_loopback_with_the_flag():
    with pytest.raises(ValueError):
        CentralClient("http://127.0.0.1:8000", transport=FakeTransport())
    CentralClient("http://127.0.0.1:8000", transport=FakeTransport(),
                  allow_insecure_loopback=True)
    CentralClient("http://localhost:8000", transport=FakeTransport(),
                  allow_insecure_loopback=True)
    with pytest.raises(ValueError):
        CentralClient("http://example.com", transport=FakeTransport(),
                      allow_insecure_loopback=True)


@pytest.mark.parametrize("url", ["http://127.0.0.1.evil.com",
                                 "http://localhost.evil.com",
                                 "http://127.0.0.1@evil.com",
                                 "http://evil.com@127.0.0.1.evil.com"])
def test_loopback_flag_rejects_lookalike_hostnames(url):
    with pytest.raises(ValueError):
        CentralClient(url, transport=FakeTransport(), allow_insecure_loopback=True)


# ---- register -----------------------------------------------------------------
def test_register_success():
    t = FakeTransport(payload={"pc_id": "pc-1", "api_key": "dsk_x",
                               "poll_interval_seconds": 600})
    r = client(t).register("ABCDEFGH1234")
    assert (r.pc_id, r.api_key, r.poll_interval_seconds) == ("pc-1", "dsk_x", 600)
    method, url, headers, body, _ = t.calls[0]
    assert method == "POST" and url == "https://central.example/pcs/register"
    assert json.loads(body) == {"code": "ABCDEFGH1234"}
    assert headers["Content-Type"] == "application/json"


def test_register_bad_code_is_registration_error():
    with pytest.raises(CentralRegistrationError):
        client(FakeTransport(status=400, payload={"detail": "x"})).register("c" * 12)


def test_register_server_error_is_unavailable():
    with pytest.raises(CentralUnavailable):
        client(FakeTransport(status=503, raw=b"oops")).register("c" * 12)


def test_register_malformed_response_is_unavailable():
    with pytest.raises(CentralUnavailable):
        client(FakeTransport(payload={"pc_id": "x"})).register("c" * 12)


# ---- pull ---------------------------------------------------------------------
def test_pull_success_sends_bearer_and_encoded_operator():
    t = FakeTransport(payload=PULL_OK)
    r = client(t).pull_config("pc-1", "dsk_key", "Zoë/x y")
    assert r.pc_id == "pc-1" and r.poll_interval_seconds == 300
    assert r.projects[0]["version"] == 2
    method, url, headers, body, _ = t.calls[0]
    assert method == "GET" and url == "https://central.example/pcs/pc-1/config"
    assert headers["Authorization"] == "Bearer dsk_key"
    assert headers["X-Operator-Id"] == "Zo%C3%AB%2Fx%20y"
    assert body is None


def test_pull_without_operator_sends_no_header():
    t = FakeTransport(payload=PULL_OK)
    client(t).pull_config("pc-1", "k")
    assert "X-Operator-Id" not in t.calls[0][2]


def test_pc_id_is_url_quoted_in_the_path():
    t = FakeTransport(payload=PULL_OK)
    client(t).pull_config("a/b c", "k")
    assert t.calls[0][1] == "https://central.example/pcs/a%2Fb%20c/config"


@pytest.mark.parametrize("status", [401, 403])
def test_pull_auth_failures(status):
    with pytest.raises(CentralAuthError) as e:
        client(FakeTransport(status=status, payload={"detail": "x"})).pull_config("pc-1", "k")
    assert e.value.status == status


@pytest.mark.parametrize("status", [500, 502, 404, 302, 429])
def test_pull_other_statuses_are_unavailable(status):
    with pytest.raises(CentralUnavailable):
        client(FakeTransport(status=status, raw=b"x")).pull_config("pc-1", "k")


def test_transport_failure_propagates_as_unavailable():
    with pytest.raises(CentralUnavailable):
        client(FakeTransport(exc=CentralUnavailable("dns"))).pull_config("pc-1", "k")


@pytest.mark.parametrize("raw", [b"not json", b"[]", b"null", b'"str"'])
def test_pull_non_object_json_is_unavailable(raw):
    with pytest.raises(CentralUnavailable):
        client(FakeTransport(raw=raw)).pull_config("pc-1", "k")


@pytest.mark.parametrize("mutate", [
    lambda d: d.pop("projects"),
    lambda d: d.update(projects="x"),
    lambda d: d.update(projects=["notadict"]),
    lambda d: d.update(poll_interval_seconds="x"),
    lambda d: d.pop("pc_id"),
])
def test_pull_malformed_structure_is_unavailable(mutate):
    d = json.loads(json.dumps(PULL_OK))
    mutate(d)
    with pytest.raises(CentralUnavailable):
        client(FakeTransport(payload=d)).pull_config("pc-1", "k")


def test_pull_poll_interval_is_clamped():
    d = dict(PULL_OK, poll_interval_seconds=1)
    assert client(FakeTransport(payload=d)).pull_config("pc-1", "k").poll_interval_seconds == 30


def test_oversized_response_is_rejected():
    # a VALID config document padded past the cap, so only the size check
    # (not a JSON parse failure) can reject it
    ok = json.dumps(dict(PULL_OK, pad="")).encode()
    big = json.dumps(dict(PULL_OK, pad="x" * (MAX_RESPONSE_BYTES - len(ok) + 1))).encode()
    assert len(big) == MAX_RESPONSE_BYTES + 1
    assert client(FakeTransport(raw=ok)).pull_config("pc-1", "k").pc_id == "pc-1"
    with pytest.raises(CentralUnavailable, match="too large"):
        client(FakeTransport(raw=big)).pull_config("pc-1", "k")


def test_per_project_entries_are_not_validated_here():
    # entry-level validation (ids, versions) belongs to config_overlay so one
    # bad project cannot hide the others
    d = dict(PULL_OK, projects=[{"project_id": "../x"}, PULL_OK["projects"][0]])
    r = client(FakeTransport(payload=d)).pull_config("pc-1", "k")
    assert len(r.projects) == 2


@pytest.mark.parametrize("status,raw,exc_type", [
    (401, b"x", CentralAuthError),
    (500, b"x", CentralUnavailable),
    (200, b"not json", CentralUnavailable),
])
def test_error_messages_never_contain_the_api_key(status, raw, exc_type):
    key = "dsk_SECRET_KEY_VALUE"
    with pytest.raises(exc_type) as e:
        client(FakeTransport(status=status, raw=raw)).pull_config("pc-1", key)
    assert key not in str(e.value) and "Bearer" not in str(e.value)


# ---- the real urllib transport against a loopback server ----------------------
class _Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "http://127.0.0.1:1/elsewhere")
            self.end_headers()
            return
        body = json.dumps(PULL_OK).encode()
        self.send_response(200 if self.path.endswith("/config") else 404)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture()
def loopback_server():
    srv = http.server.HTTPServer(("127.0.0.1", 0), _Handler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{srv.server_port}"
    finally:
        srv.shutdown()
        srv.server_close()
        t.join(timeout=5)


def test_urllib_transport_end_to_end(loopback_server):
    c = CentralClient(loopback_server, allow_insecure_loopback=True, timeout=5)
    assert c.pull_config("pc-1", "k").projects[0]["project_id"] == "p1"


def test_urllib_transport_does_not_follow_redirects(loopback_server):
    status, _ = urllib_transport("GET", loopback_server + "/redirect", {}, None, 5)
    assert status == 302


def test_urllib_transport_connection_refused_is_unavailable():
    with pytest.raises(CentralUnavailable):
        urllib_transport("GET", "http://127.0.0.1:1/x", {}, None, 2)


@pytest.mark.parametrize("exc", [
    urllib.error.URLError(ssl.SSLCertVerificationError("certificate verify failed")),
    ssl.SSLError("handshake failure"),
])
def test_urllib_transport_tls_errors_are_unavailable(monkeypatch, exc):
    def boom(self, *a, **kw):
        raise exc
    monkeypatch.setattr(urllib.request.OpenerDirector, "open", boom)
    with pytest.raises(CentralUnavailable):
        urllib_transport("GET", "https://central.example/x", {}, None, 2)


def test_urllib_transport_uses_a_verifying_tls_context(monkeypatch):
    seen = {}

    class SpyHTTPSHandler(urllib.request.HTTPSHandler):
        def __init__(self, *a, **kw):
            seen["ctx"] = kw.get("context")
            super().__init__(*a, **kw)
    monkeypatch.setattr(urllib.request, "HTTPSHandler", SpyHTTPSHandler)
    with pytest.raises(CentralUnavailable):
        urllib_transport("GET", "http://127.0.0.1:1/x", {}, None, 2)
    ctx = seen["ctx"]
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname is True
