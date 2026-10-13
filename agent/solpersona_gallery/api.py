"""The SolPersona device API (SPEC §3–§11b), and nothing else.

Every call goes through `DeviceApi.request`, which times it and records it in
a short log for the debug overlay. The access token is passed in per call and
never stored here, never logged, and never sent anywhere but the configured
base URL: authenticated calls do not follow redirects, so a redirect to
another host cannot carry it away.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import deque
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
from typing import Any, Callable, Optional
from urllib.parse import quote

import aiohttp

from .util import mono_ms

log = logging.getLogger(__name__)


class NetworkError(Exception):
    """No answer: no network, DNS, TLS, timeout. The server never saw it — or we never saw its reply."""


@dataclass
class ApiResult:
    status: int
    data: Any = None
    body: Optional[bytes] = None
    headers: dict = field(default_factory=dict)
    sent_at: float = 0.0
    received_at: float = 0.0

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 300

    @property
    def error(self) -> dict:
        if isinstance(self.data, dict) and isinstance(self.data.get("error"), dict):
            return self.data["error"]
        return {}

    @property
    def code(self) -> str:
        return str(self.error.get("code") or "")

    @property
    def message(self) -> str:
        return str(self.error.get("message") or "")

    @property
    def retry_after_ms(self) -> Optional[float]:
        return parse_retry_after(self.headers.get("Retry-After"))


def parse_retry_after(value: Optional[str], now: Optional[float] = None) -> Optional[float]:
    """`Retry-After` in ms: delta-seconds, or an HTTP date."""
    if value is None:
        return None
    value = value.strip()
    if value.isdigit():
        return int(value) * 1000.0
    try:
        when = parsedate_to_datetime(value).timestamp()
    except (TypeError, ValueError, IndexError):
        return None
    return max(0.0, (when - (now if now is not None else time.time())) * 1000.0)


class DeviceApi:
    def __init__(self, session: aiohttp.ClientSession, base_url: str, mono: Callable[[], float] = mono_ms) -> None:
        self.session = session
        self.base_url = base_url.rstrip("/")
        self._mono = mono
        self.calls: deque[dict] = deque(maxlen=60)  # for the debug overlay; never holds a header

    async def request(
        self,
        method: str,
        path: str,
        *,
        body: Any = None,
        token: Optional[str] = None,
        binary: bool = False,
        timeout: float = 15.0,
    ) -> ApiResult:
        headers = {"Accept": "application/json"}
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body, separators=(",", ":"))
        if token:
            headers["Authorization"] = f"Bearer {token}"
        sent_at = self._mono()
        try:
            async with self.session.request(
                method,
                self.base_url + path,
                data=data,
                headers=headers,
                allow_redirects=token is None,
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as res:
                raw = await res.read()
                received_at = self._mono()
                result = ApiResult(
                    status=res.status,
                    headers={k: v for k, v in res.headers.items()},
                    sent_at=sent_at,
                    received_at=received_at,
                )
                is_json = "json" in (res.headers.get("Content-Type") or "")
                if binary and result.ok:
                    result.body = raw
                elif is_json and raw:
                    try:
                        result.data = json.loads(raw.decode("utf-8"))
                    except ValueError:
                        result.data = None
        except (aiohttp.ClientError, asyncio.TimeoutError, OSError) as error:
            self._record(method, path, "ERR", sent_at, "timeout" if isinstance(error, asyncio.TimeoutError) else "network")
            raise NetworkError(type(error).__name__) from None
        self._record(method, path, result.status, sent_at, result.code)
        return result

    def _record(self, method: str, path: str, status: Any, sent_at: float, note: str) -> None:
        self.calls.appendleft(
            {"at": time.time() * 1000.0, "method": method, "path": path, "status": status, "ms": round(self._mono() - sent_at), "note": note}
        )

    # The endpoints ---------------------------------------------------------------

    async def register(self, body: dict) -> ApiResult:
        return await self.request("POST", "/api/v1/device/register", body=body)

    async def token(self, device_code: str) -> ApiResult:
        return await self.request("POST", "/api/v1/device/token", body={"deviceCode": device_code})

    async def sync(self, token: str, body: dict) -> ApiResult:
        return await self.request("POST", "/api/v1/device/sync", body=body, token=token)

    async def qr(self, token: str, item_id: str) -> ApiResult:
        return await self.request("GET", "/api/v1/device/qr?item=" + quote(item_id, safe=""), token=token, binary=True)

    async def forget(self, token: str) -> ApiResult:
        return await self.request("DELETE", "/api/v1/device", token=token)

    async def sample(self) -> ApiResult:
        return await self.request("GET", "/api/v1/device/sample")

    async def sample_qr(self, item_id: str) -> ApiResult:
        return await self.request("GET", "/api/v1/device/sample/qr?item=" + quote(item_id, safe=""), binary=True)
