"""Minimal OAuth2 token refresh for the Home Connect API.

Vendored from the acctl CLI (ac_reverse.auth) so this integration is
self-contained and installable via HACS with no external package.
"""

from __future__ import annotations

import httpx

API_BASE = "https://api.home-connect.com"
TOKEN_URL = f"{API_BASE}/security/oauth/token"


async def refresh_token_raw(
    client_id: str,
    client_secret: str,
    refresh_token: str,
    client: httpx.AsyncClient | None = None,
) -> dict:
    """Refresh access token using raw params.

    Pass an existing ``client`` to avoid creating an httpx.AsyncClient on the
    event loop (the SSL-context init is a blocking call HA flags as a warning).
    When omitted, a temporary client is created (fine for CLI/off-loop use).
    """
    data: dict[str, str] = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    }
    # PKCE clients (app auth) don't have a client_secret
    if client_secret:
        data["client_secret"] = client_secret
    else:
        data["client_id"] = client_id

    if client is not None:
        # Send only form headers; don't leak the caller's Bearer/Accept defaults.
        resp = await client.post(
            TOKEN_URL,
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "*/*"},
        )
        resp.raise_for_status()
        return resp.json()

    async with httpx.AsyncClient() as temp_client:
        resp = await temp_client.post(TOKEN_URL, data=data)
        resp.raise_for_status()
        return resp.json()
