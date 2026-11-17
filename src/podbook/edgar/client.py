"""Polite EDGAR HTTP client: User-Agent, rate limit, retries and a disk cache."""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Callable

import requests

from podbook.config import SEC_MAX_REQUESTS_PER_SECOND

log = logging.getLogger(__name__)

SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
TICKERS_EXCHANGE_URL = "https://www.sec.gov/files/company_tickers_exchange.json"
BULK_SUBMISSIONS_URL = "https://www.sec.gov/Archives/edgar/daily-index/bulkdata/submissions.zip"

RETRY_STATUS = {429, 500, 502, 503, 504}


class RateLimiter:
    """Keeps successive calls at least 1/max_per_second apart."""

    def __init__(
        self,
        max_per_second: float,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if max_per_second <= 0:
            raise ValueError("max_per_second must be positive")
        self.min_interval = 1.0 / max_per_second
        self._clock = clock
        self._sleep = sleep
        self._last: float | None = None

    def wait(self) -> None:
        now = self._clock()
        if self._last is not None:
            earliest = self._last + self.min_interval
            if now < earliest:
                self._sleep(earliest - now)
                now = earliest
        self._last = now


class EdgarClient:
    def __init__(
        self,
        user_agent: str,
        session: Any | None = None,
        max_per_second: float = SEC_MAX_REQUESTS_PER_SECOND,
        max_retries: int = 5,
        timeout: float = 60.0,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not user_agent or "@" not in user_agent:
            raise ValueError("EDGAR needs a User-Agent with a contact email")
        self.headers = {"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"}
        self.session = session if session is not None else requests.Session()
        self.limiter = RateLimiter(max_per_second, clock=clock, sleep=sleep)
        self.max_retries = max_retries
        self.timeout = timeout
        self._sleep = sleep

    def get(self, url: str, stream: bool = False) -> Any:
        """GET with rate limiting. Retries on 429 and 5xx with exponential backoff."""
        for attempt in range(self.max_retries + 1):
            self.limiter.wait()
            try:
                resp = self.session.get(url, headers=self.headers, timeout=self.timeout, stream=stream)
            except requests.ConnectionError as exc:
                if attempt == self.max_retries:
                    raise
                log.warning("Connection error on %s (%s), retrying", url, exc)
                self._sleep(2**attempt)
                continue
            if resp.status_code in RETRY_STATUS and attempt < self.max_retries:
                log.warning("HTTP %s on %s, retry %d", resp.status_code, url, attempt + 1)
                self._sleep(2**attempt)
                continue
            resp.raise_for_status()
            return resp
        raise RuntimeError("unreachable")  # pragma: no cover

    def get_json(self, url: str) -> dict:
        return self.get(url).json()

    def get_json_or_none(self, url: str) -> dict | None:
        """Like get_json but returns None on 404 (e.g. a filer with no XBRL facts)."""
        try:
            return self.get_json(url)
        except requests.HTTPError as exc:
            if exc.response is not None and exc.response.status_code == 404:
                return None
            raise

    def cached_json(self, url: str, path: Path, max_age_days: float | None = None) -> dict | None:
        """Return JSON from path if fresh enough, otherwise fetch and save it.

        A 404 is cached as an empty marker file so it is not re-requested.
        """
        path = Path(path)
        missing_marker = path.with_suffix(".404")
        if _fresh(path, max_age_days):
            return json.loads(path.read_text())
        if _fresh(missing_marker, max_age_days):
            return None
        data = self.get_json_or_none(url)
        path.parent.mkdir(parents=True, exist_ok=True)
        if data is None:
            missing_marker.touch()
            return None
        _atomic_write_text(path, json.dumps(data))
        return data

    def download(self, url: str, dest: Path, chunk_size: int = 1 << 20) -> Path:
        """Stream a large file (e.g. the bulk submissions zip) to dest."""
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".part")
        resp = self.get(url, stream=True)
        with open(tmp, "wb") as fh:
            for chunk in resp.iter_content(chunk_size=chunk_size):
                if chunk:
                    fh.write(chunk)
        os.replace(tmp, dest)
        return dest


def _fresh(path: Path, max_age_days: float | None) -> bool:
    if not path.exists():
        return False
    if max_age_days is None:
        return True
    age_days = (time.time() - path.stat().st_mtime) / 86400
    return age_days <= max_age_days


def _atomic_write_text(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".part")
    tmp.write_text(text)
    os.replace(tmp, path)
