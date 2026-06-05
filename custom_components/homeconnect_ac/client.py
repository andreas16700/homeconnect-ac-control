"""Home Connect API client for AC control."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import httpx

from .auth import refresh_token_raw

_LOGGER = logging.getLogger(__name__)

API_BASE = "https://api.home-connect.com"
HEADERS = {
    "Accept": "application/vnd.bsh.sdk.v1+json",
    "Content-Type": "application/vnd.bsh.sdk.v1+json",
}

# Refresh the access token 5 minutes before it expires
_REFRESH_MARGIN = 300


class HomeConnectClient:
    """Async client for the Home Connect REST API."""

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        access_token: str,
        refresh_token: str,
        on_token_refresh: Callable[[str, str], Awaitable[None]] | None = None,
    ) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.access_token = access_token
        self.refresh_token = refresh_token
        self._on_token_refresh = on_token_refresh
        self._client: httpx.AsyncClient | None = None
        self._sse_client: httpx.AsyncClient | None = None
        self._token_expires_at: float = self._parse_jwt_exp(access_token)

    @staticmethod
    def _parse_jwt_exp(token: str) -> float:
        """Extract expiry timestamp from a JWT access token."""
        try:
            payload = token.split(".")[1]
            padding = 4 - len(payload) % 4
            if padding != 4:
                payload += "=" * padding
            data = json.loads(base64.urlsafe_b64decode(payload))
            return float(data.get("exp", 0))
        except Exception:
            return 0.0

    @property
    def _needs_refresh(self) -> bool:
        if self._token_expires_at == 0:
            return False
        return time.time() > (self._token_expires_at - _REFRESH_MARGIN)

    # ── Lifecycle ──

    def sync_open(self) -> None:
        """Create the underlying httpx clients (synchronous).

        Use this from HA's async_add_executor_job to avoid blocking
        the event loop with SSL context initialization.
        """
        self._client = httpx.AsyncClient(
            base_url=API_BASE,
            headers={**HEADERS, "Authorization": f"Bearer {self.access_token}"},
            timeout=30.0,
        )
        # Pre-create SSE client to avoid SSL context init on the event loop
        self._sse_client = httpx.AsyncClient(timeout=None)

    async def async_open(self) -> None:
        """Open the underlying httpx client."""
        self.sync_open()

    async def async_close(self) -> None:
        """Close the underlying httpx clients."""
        if self._sse_client:
            await self._sse_client.aclose()
            self._sse_client = None
        if self._client:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> HomeConnectClient:
        await self.async_open()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.async_close()

    # ── Token refresh ──

    async def _do_refresh(self) -> None:
        """Refresh the access token and notify the callback."""
        try:
            tokens = await refresh_token_raw(
                self.client_id, self.client_secret, self.refresh_token
            )
        except httpx.HTTPStatusError as e:
            raise RuntimeError(
                f"Token refresh failed ({e.response.status_code}). "
                "Your refresh token may have expired. "
                "Run 'acctl app-auth' to re-authenticate."
            ) from e
        self.access_token = tokens["access_token"]
        self.refresh_token = tokens.get("refresh_token", self.refresh_token)
        self._token_expires_at = self._parse_jwt_exp(self.access_token)
        self._client.headers["Authorization"] = f"Bearer {self.access_token}"
        _LOGGER.debug("Token refreshed, expires at %s", self._token_expires_at)
        if self._on_token_refresh:
            await self._on_token_refresh(self.access_token, self.refresh_token)

    async def _ensure_token(self) -> None:
        """Proactively refresh if the token is about to expire."""
        if self._needs_refresh:
            await self._do_refresh()

    # ── Core request methods ──

    async def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """Make an API request with proactive + reactive token refresh and 429 retry."""
        await self._ensure_token()
        resp = await self._client.request(method, path, **kwargs)
        if resp.status_code == 401:
            await self._do_refresh()
            resp = await self._client.request(method, path, **kwargs)
        # Retry once on 429, but only if Retry-After is short
        if resp.status_code == 429:
            delay = min(int(resp.headers.get("Retry-After", "5")), 10)
            _LOGGER.debug("Rate limited, retrying in %ds", delay)
            await asyncio.sleep(delay)
            resp = await self._client.request(method, path, **kwargs)
        return resp

    async def _get_json(self, path: str) -> dict:
        resp = await self._request("GET", path)
        resp.raise_for_status()
        return resp.json()

    async def _put(self, path: str, data: dict) -> httpx.Response:
        resp = await self._request("PUT", path, json=data)
        resp.raise_for_status()
        return resp

    # ── Discovery ──

    async def get_appliances(self) -> list[dict]:
        """List all paired Home Connect appliances."""
        data = await self._get_json("/api/homeappliances")
        return data.get("data", {}).get("homeappliances", [])

    async def get_appliance(self, ha_id: str) -> dict:
        data = await self._get_json(f"/api/homeappliances/{ha_id}")
        return data.get("data", {})

    # ── Status & Settings ──

    async def get_status(self, ha_id: str) -> list[dict]:
        data = await self._get_json(f"/api/homeappliances/{ha_id}/status")
        return data.get("data", {}).get("status", [])

    async def get_settings(self, ha_id: str) -> list[dict]:
        data = await self._get_json(f"/api/homeappliances/{ha_id}/settings")
        return data.get("data", {}).get("settings", [])

    async def get_setting(self, ha_id: str, key: str) -> dict:
        data = await self._get_json(f"/api/homeappliances/{ha_id}/settings/{key}")
        return data.get("data", {})

    async def set_setting(self, ha_id: str, key: str, value: Any) -> None:
        await self._put(
            f"/api/homeappliances/{ha_id}/settings/{key}",
            {"data": {"key": key, "value": value}},
        )

    # ── Programs ──

    async def get_programs(self, ha_id: str) -> list[dict]:
        data = await self._get_json(f"/api/homeappliances/{ha_id}/programs")
        return data.get("data", {}).get("programs", [])

    async def get_available_programs(self, ha_id: str) -> list[dict]:
        data = await self._get_json(f"/api/homeappliances/{ha_id}/programs/available")
        return data.get("data", {}).get("programs", [])

    async def get_program_details(self, ha_id: str, program_key: str) -> dict:
        data = await self._get_json(
            f"/api/homeappliances/{ha_id}/programs/available/{program_key}"
        )
        return data.get("data", {})

    async def get_active_program(self, ha_id: str) -> dict | None:
        try:
            data = await self._get_json(f"/api/homeappliances/{ha_id}/programs/active")
            return data.get("data")
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return None
            raise

    async def get_selected_program(self, ha_id: str) -> dict | None:
        """GET selected program, returns None on 404."""
        try:
            data = await self._get_json(
                f"/api/homeappliances/{ha_id}/programs/selected"
            )
            return data.get("data")
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return None
            raise

    async def select_program(
        self, ha_id: str, program_key: str, options: list[dict] | None = None
    ) -> None:
        """PUT to select a program (selectonly model)."""
        body: dict[str, Any] = {"data": {"key": program_key}}
        if options:
            body["data"]["options"] = options
        await self._put(f"/api/homeappliances/{ha_id}/programs/selected", body)

    async def set_selected_option(
        self, ha_id: str, key: str, value: Any, unit: str | None = None
    ) -> None:
        """PUT to set an option on the selected program."""
        data: dict[str, Any] = {"key": key, "value": value}
        if unit:
            data["unit"] = unit
        await self._put(
            f"/api/homeappliances/{ha_id}/programs/selected/options/{key}",
            {"data": data},
        )

    async def start_program(
        self, ha_id: str, program_key: str, options: list[dict] | None = None
    ) -> None:
        body: dict[str, Any] = {"data": {"key": program_key}}
        if options:
            body["data"]["options"] = options
        await self._put(f"/api/homeappliances/{ha_id}/programs/active", body)

    async def stop_program(self, ha_id: str) -> None:
        resp = await self._request(
            "DELETE", f"/api/homeappliances/{ha_id}/programs/active"
        )
        resp.raise_for_status()

    async def set_active_option(
        self, ha_id: str, key: str, value: Any
    ) -> None:
        await self._put(
            f"/api/homeappliances/{ha_id}/programs/active/options/{key}",
            {"data": {"key": key, "value": value}},
        )

    # ── SSE (Server-Sent Events) ──

    async def stream_events(self) -> AsyncIterator[dict]:
        """Stream real-time events from all appliances via SSE.

        Yields dicts with keys: event_type, ha_id (extracted from uri), key, value.
        Handles token refresh and reconnection internally.
        """
        await self._ensure_token()
        url = f"{API_BASE}/api/homeappliances/events"
        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Accept": "text/event-stream",
        }

        sse_client = self._sse_client or httpx.AsyncClient(timeout=None)
        async with sse_client.stream("GET", url, headers=headers) as resp:
            if resp.status_code == 401:
                await self._do_refresh()
                return  # caller should reconnect
            resp.raise_for_status()

            event_type = ""
            event_data = ""
            async for line in resp.aiter_lines():
                if line.startswith("event:"):
                    event_type = line[6:].strip()
                elif line.startswith("data:"):
                    event_data = line[5:].strip()
                elif line == "":
                    if event_type and event_data and event_type != "KEEP-ALIVE":
                        try:
                            parsed = json.loads(event_data)
                            items = parsed.get("items", [parsed])
                            for item in items:
                                # Extract haId from URI like /api/homeappliances/{haId}/...
                                uri = item.get("uri", "")
                                ha_id = ""
                                parts = uri.split("/")
                                if "homeappliances" in parts:
                                    idx = parts.index("homeappliances")
                                    if idx + 1 < len(parts):
                                        ha_id = parts[idx + 1]
                                yield {
                                    "event_type": event_type,
                                    "ha_id": ha_id,
                                    "key": item.get("key", ""),
                                    "value": item.get("value"),
                                }
                        except json.JSONDecodeError:
                            _LOGGER.debug("Unparseable SSE data: %s", event_data)
                    event_type = ""
                    event_data = ""

    # ── Commands ──

    async def get_commands(self, ha_id: str) -> list[dict]:
        data = await self._get_json(f"/api/homeappliances/{ha_id}/commands")
        return data.get("data", {}).get("commands", [])

    # ── Full dump ──

    async def dump_appliance(self, ha_id: str) -> dict:
        """Dump everything we can learn about an appliance."""
        result = {}

        result["info"] = await self.get_appliance(ha_id)

        try:
            result["status"] = await self.get_status(ha_id)
        except httpx.HTTPStatusError:
            result["status"] = "error"

        try:
            result["settings"] = await self.get_settings(ha_id)
        except httpx.HTTPStatusError:
            result["settings"] = "error"

        try:
            result["available_programs"] = await self.get_available_programs(ha_id)
        except httpx.HTTPStatusError:
            result["available_programs"] = "error"

        # Get detailed constraints for each available program
        if isinstance(result["available_programs"], list):
            result["program_details"] = {}
            for prog in result["available_programs"]:
                key = prog.get("key", "")
                try:
                    result["program_details"][key] = await self.get_program_details(
                        ha_id, key
                    )
                except httpx.HTTPStatusError:
                    result["program_details"][key] = "error"

        try:
            result["active_program"] = await self.get_active_program(ha_id)
        except httpx.HTTPStatusError:
            result["active_program"] = "error"

        try:
            result["commands"] = await self.get_commands(ha_id)
        except httpx.HTTPStatusError:
            result["commands"] = "error"

        return result
