from __future__ import annotations

import http.client
import itertools
import json
import logging
import ssl
import time
import urllib.parse
import urllib.robotparser
from typing import Any, Callable

import requests

logger = logging.getLogger(__name__)


class HttpClient:
    def __init__(self, config: dict) -> None:
        self._rate_limit = config.get("RATE_LIMIT_SECONDS", 1.0)
        self._max_attempts = config.get("RETRY_ATTEMPTS", 3)
        self._backoff_base = config.get("RETRY_BACKOFF_BASE", 2.0)
        self._timeout = config.get("REQUEST_TIMEOUT", 30)
        user_agents = config.get("USER_AGENTS", ["python-requests/2.28"])
        self._ua_cycle = itertools.cycle(user_agents)
        self._last_request: dict[str, float] = {}
        self._robots_cache: dict[str, urllib.robotparser.RobotFileParser] = {}
        self._session = requests.Session()

    def get(self, url: str, params: dict | None = None, headers: dict | None = None) -> dict:
        def _do():
            self._wait_for_domain(url)
            h = self._base_headers()
            if headers:
                h.update(headers)
            resp = self._session.get(url, params=params, headers=h, timeout=self._timeout)
            resp.raise_for_status()
            return resp.json()

        return self._retry(_do)

    def post(self, url: str, data: str | dict, headers: dict | None = None) -> dict:
        def _do():
            self._wait_for_domain(url)
            h = self._base_headers()
            if headers:
                h.update(headers)
            # Pass dict directly so requests URL-encodes it correctly;
            # pass string as-is for callers that pre-encode.
            resp = self._session.post(url, data=data, headers=h, timeout=self._timeout)
            resp.raise_for_status()
            return resp.json()

        return self._retry(_do)

    def get_text(self, url: str, headers: dict | None = None) -> str:
        def _do():
            self._wait_for_domain(url)
            h = self._base_headers()
            if headers:
                h.update(headers)
            resp = self._session.get(url, headers=h, timeout=self._timeout)
            resp.raise_for_status()
            return resp.text

        return self._retry(_do)

    def can_fetch(self, base_url: str, path: str) -> bool:
        if base_url not in self._robots_cache:
            rp = urllib.robotparser.RobotFileParser()
            robots_url = base_url.rstrip("/") + "/robots.txt"
            try:
                self._wait_for_domain(robots_url)
                rp.set_url(robots_url)
                rp.read()
                self._robots_cache[base_url] = rp
            except Exception as exc:
                logger.warning("robots.txt fetch failed for %s: %s — assuming allowed", base_url, exc)
                return True
        ua = next(self._ua_cycle)
        result = self._robots_cache[base_url].can_fetch(ua, path)
        return result

    def _wait_for_domain(self, url: str) -> None:
        domain = urllib.parse.urlparse(url).netloc
        last = self._last_request.get(domain, 0.0)
        elapsed = time.monotonic() - last
        if elapsed < self._rate_limit:
            time.sleep(self._rate_limit - elapsed)
        self._last_request[domain] = time.monotonic()

    def _retry(self, fn: Callable[[], Any]) -> Any:
        last_exc: Exception | None = None
        for attempt in range(1, self._max_attempts + 1):
            try:
                return fn()
            except Exception as exc:
                last_exc = exc
                if attempt < self._max_attempts:
                    delay = self._backoff_base ** (attempt - 1)
                    logger.debug("Attempt %d failed (%s), retrying in %.1fs", attempt, exc, delay)
                    time.sleep(delay)
        raise last_exc  # type: ignore[misc]

    def post_raw(self, url: str, form_data: dict, user_agent: str | None = None) -> dict:
        """POST using http.client directly — avoids requests' Accept-Encoding injection.

        Some servers (e.g. overpass-api.de) return 406 when urllib3 adds
        'Accept-Encoding: gzip, deflate, br, zstd' due to an Apache brotli
        module misconfiguration. This bypasses that problem entirely.
        """
        def _do():
            self._wait_for_domain(url)
            parsed = urllib.parse.urlparse(url)
            # Overpass requires the query value sent verbatim (not percent-encoded).
            # Both urlencode and requests url-encode brackets/quotes, which the
            # server rejects with 406/504. Build the body manually: URL-encode
            # keys only, leave values as raw UTF-8 (matching curl --data behaviour).
            parts = [f"{urllib.parse.quote(k, safe='')}={v}" for k, v in form_data.items()]
            body = "&".join(parts).encode("utf-8")
            ctx = ssl.create_default_context() if parsed.scheme == "https" else None
            conn = (
                http.client.HTTPSConnection(parsed.netloc, context=ctx, timeout=self._timeout)
                if parsed.scheme == "https"
                else http.client.HTTPConnection(parsed.netloc, timeout=self._timeout)
            )
            try:
                conn.request(
                    "POST",
                    parsed.path or "/",
                    body=body,
                    headers={
                        "Content-Type": "application/x-www-form-urlencoded",
                        "Accept": "*/*",
                        "User-Agent": user_agent or next(self._ua_cycle),
                    },
                )
                resp = conn.getresponse()
                raw = resp.read()
                if resp.status >= 400:
                    raise Exception(f"HTTP {resp.status} for {url}")
                return json.loads(raw)
            finally:
                conn.close()

        return self._retry(_do)

    def _base_headers(self) -> dict:
        return {"User-Agent": next(self._ua_cycle)}
