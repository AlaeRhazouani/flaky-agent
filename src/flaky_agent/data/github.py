"""Minimal GitHub REST client: authentication, pagination, rate limits, retries."""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from typing import Any

import httpx

API_URL = "https://api.github.com"

log = logging.getLogger(__name__)


class NotFound(Exception):
    """The resource is gone (404/410), e.g. a log past GitHub's retention period."""


class GitHubClient:
    def __init__(
        self,
        token: str,
        *,
        max_retries: int = 5,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.max_retries = max_retries
        self._http = httpx.Client(
            base_url=API_URL,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=60,
            # Log downloads redirect to a signed blob URL; httpx drops the
            # Authorization header on cross-origin redirects.
            follow_redirects=True,
            transport=transport,
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> GitHubClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def get_json(self, url: str, params: dict[str, Any] | None = None) -> Any:
        return self._get(url, params).json()

    def get_bytes(self, url: str) -> bytes:
        return self._get(url).content

    def paginate(
        self, url: str, params: dict[str, Any] | None = None, key: str | None = None
    ) -> Iterator[dict[str, Any]]:
        """Yield every item of a paginated endpoint. `key` selects the list in the body."""
        next_url: str | None = url
        next_params: dict[str, Any] | None = {"per_page": 100, **(params or {})}
        while next_url:
            resp = self._get(next_url, next_params)
            body = resp.json()
            yield from body[key] if key else body
            next_url = resp.links.get("next", {}).get("url")
            next_params = None  # the "next" link already carries the query string

    def _get(self, url: str, params: dict[str, Any] | None = None) -> httpx.Response:
        for attempt in range(self.max_retries):
            try:
                resp = self._http.get(url, params=params)
            except httpx.TransportError as exc:
                log.warning("network error on %s (%s), retrying", url, exc)
                time.sleep(2**attempt)
                continue
            if resp.status_code in (404, 410):
                raise NotFound(url)
            if resp.status_code in (403, 429) and _is_rate_limited(resp):
                wait = _rate_limit_wait(resp)
                log.warning("rate limited, sleeping %.0fs", wait)
                time.sleep(wait)
                continue
            if resp.status_code >= 500:
                log.warning("server error %s on %s, retrying", resp.status_code, url)
                time.sleep(2**attempt)
                continue
            resp.raise_for_status()
            return resp
        raise RuntimeError(f"giving up on {url} after {self.max_retries} attempts")


def _is_rate_limited(resp: httpx.Response) -> bool:
    return (
        resp.headers.get("x-ratelimit-remaining") == "0"
        or "retry-after" in resp.headers
        or "rate limit" in resp.text.lower()
    )


def _rate_limit_wait(resp: httpx.Response) -> float:
    if "retry-after" in resp.headers:
        return float(resp.headers["retry-after"])
    if "x-ratelimit-reset" in resp.headers:
        return max(float(resp.headers["x-ratelimit-reset"]) - time.time(), 0) + 1
    return 60.0
