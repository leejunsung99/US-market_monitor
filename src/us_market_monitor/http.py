import math
import random
import threading
import time
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

import requests


class FetchError(RuntimeError):
    """Sanitized error: never includes URLs, headers, tokens or response bodies."""

    def __init__(self, code: str, retry_after: float = 0):
        super().__init__(code)
        self.retry_after = retry_after


class Stopped(RuntimeError):
    pass


def retry_delay(value) -> float:
    try:
        delay = float(value)
        return max(0, delay) if math.isfinite(delay) else 0
    except (ValueError, TypeError):
        try:
            return max(0, (parsedate_to_datetime(value) - datetime.now(UTC)).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return 0


class HttpClient:
    def __init__(self, *, timeout=20, attempts=3, spacing=0.2, headers=None, stop=None):
        self.timeout, self.attempts, self.spacing = timeout, attempts, spacing
        self.headers = headers or {}
        self.stop = stop or threading.Event()
        self._lock = threading.Lock()
        self._next_request = 0.0

    def wait(self, seconds):
        if self.stop.wait(max(0, seconds)):
            raise Stopped()

    def _throttle(self):
        with self._lock:
            self.wait(self._next_request - time.monotonic())
            self._next_request = time.monotonic() + self.spacing

    def get(self, url, *, params=None):
        for attempt in range(self.attempts):
            self._throttle()
            try:
                response = requests.get(
                    url, params=params, headers=self.headers, timeout=(self.timeout, self.timeout)
                )
            except requests.RequestException:
                error = FetchError("network_error")
            else:
                if response.status_code == 200:
                    return response
                delay = retry_delay(response.headers.get("Retry-After"))
                error = FetchError(f"http_{response.status_code}", delay)
                if response.status_code == 429 and delay:
                    with self._lock:
                        self._next_request = max(self._next_request, time.monotonic() + delay)
                if response.status_code not in {408, 429} and response.status_code < 500:
                    raise error
                if delay > 60:
                    raise error
            if attempt + 1 == self.attempts:
                raise error
            self.wait(max(error.retry_after, 2**attempt + random.uniform(0, 0.3)))
        raise FetchError("retry_exhausted")

    def json(self, url, *, params=None):
        try:
            return self.get(url, params=params).json()
        except ValueError:
            raise FetchError("invalid_json") from None
