"""Tiny admin-API client (cookie session + CSRF token) for the central service."""
import http.cookiejar
import json
import urllib.error
import urllib.request


class Admin:
    def __init__(self, base: str):
        self.base = base.rstrip("/")
        self.jar = http.cookiejar.CookieJar()
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))
        self.csrf = None

    def call(self, method, path, body=None, csrf=True):
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Content-Type": "application/json"}
        if csrf and self.csrf:
            headers["X-CSRF-Token"] = self.csrf
        req = urllib.request.Request(self.base + path, data=data, method=method,
                                     headers=headers)
        try:
            with self.op.open(req, timeout=10) as r:
                raw = r.read()
                return r.status, (json.loads(raw) if raw else None)
        except urllib.error.HTTPError as e:
            raw = e.read()
            try:
                return e.code, json.loads(raw)
            except ValueError:
                return e.code, raw.decode(errors="replace")

    def login(self, user="root", password="correct horse battery"):
        status, body = self.call("POST", "/admin/login",
                                 {"username": user, "password": password}, csrf=False)
        if status == 200:
            self.csrf = body["csrf_token"]
        return status, body
