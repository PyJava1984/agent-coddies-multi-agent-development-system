"""Tiny HTTP client shared by the Jira and GitLab wrappers.

Uses `requests` when installed, falls back to `urllib` so the toolkit works on a
locked-down machine with no package installs. Adds retry-with-backoff on 429 and
5xx, and never logs a request header.
"""

from __future__ import annotations

import json
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

try:
    import requests as _requests

    HTTP_BACKEND = "requests"
except ImportError:  # pragma: no cover - environment dependent
    _requests = None
    HTTP_BACKEND = "urllib"

RETRY_STATUS = {408, 429, 500, 502, 503, 504}
DEFAULT_TIMEOUT = 60


class HttpError(RuntimeError):
    def __init__(self, status: int, url: str, body: str):
        self.status = status
        self.url = url
        self.body = body
        snippet = (body or "").strip()
        if len(snippet) > 800:
            snippet = snippet[:800] + " ..."
        super().__init__(f"HTTP {status} for {url}\n{snippet}")


class Response:
    def __init__(self, status: int, body: bytes, headers: dict):
        self.status = status
        self.body = body
        self.headers = headers

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")

    def json(self) -> Any:
        if not self.body:
            return None
        try:
            return json.loads(self.text)
        except json.JSONDecodeError as exc:
            raise HttpError(self.status, "<response>", self.text) from exc


def _ssl_context(verify) -> Any:
    if verify is True or verify is None:
        return None
    if verify is False:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
    return ssl.create_default_context(cafile=str(verify))


def request(
    method: str,
    url: str,
    headers: dict = None,
    json_body: Any = None,
    data: bytes = None,
    params: dict = None,
    verify=True,
    timeout: int = DEFAULT_TIMEOUT,
    retries: int = 3,
) -> Response:
    headers = dict(headers or {})
    if params:
        clean = {k: v for k, v in params.items() if v is not None}
        if clean:
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}{urllib.parse.urlencode(clean, doseq=True)}"
    if json_body is not None:
        data = json.dumps(json_body).encode("utf-8")
        headers.setdefault("Content-Type", "application/json")
    headers.setdefault("Accept", "application/json")
    headers.setdefault("User-Agent", "agent-coddies/1.0")

    last_error = None
    for attempt in range(retries):
        if attempt:
            time.sleep(min(2 ** attempt, 8))
        try:
            if _requests is not None:
                resp = _requests.request(
                    method, url, headers=headers, data=data, verify=verify, timeout=timeout
                )
                out = Response(resp.status_code, resp.content, dict(resp.headers))
            else:
                req = urllib.request.Request(url, data=data, headers=headers, method=method)
                ctx = _ssl_context(verify)
                try:
                    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as fh:
                        out = Response(fh.status, fh.read(), dict(fh.headers))
                except urllib.error.HTTPError as exc:
                    out = Response(exc.code, exc.read(), dict(exc.headers or {}))
        except Exception as exc:  # noqa: BLE001 - network failure, retry
            last_error = exc
            continue

        if out.status in RETRY_STATUS and attempt < retries - 1:
            last_error = HttpError(out.status, url, out.text)
            continue
        if out.status >= 400:
            raise HttpError(out.status, url, out.text)
        return out

    raise RuntimeError(f"request to {url} failed after {retries} attempts: {last_error}")
