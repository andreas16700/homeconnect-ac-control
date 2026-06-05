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
) -> dict:
    """Refresh access token using raw params."""
    data: dict[str, str] = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    }
    # PKCE clients (app auth) don't have a client_secret
    if client_secret:
        data["client_secret"] = client_secret
    else:
        data["client_id"] = client_id

    async with httpx.AsyncClient() as client:
        resp = await client.post(TOKEN_URL, data=data)
        resp.raise_for_status()
        return resp.json()
