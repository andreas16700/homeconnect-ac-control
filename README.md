# Home Connect AC — Home Assistant integration

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)

A Home Assistant custom integration that exposes Bosch / Pitsos (BSH Group) air
conditioners as full **climate entities** via the Home Connect cloud API, with
read/write control of mode, target temperature, fan speed, swing, boost and
vane — and **real-time updates over Server-Sent Events (SSE)** with a REST poll
as a safety fallback.

It is fully self-contained: the only runtime dependency is `httpx`, which is
declared in the manifest and installed automatically by Home Assistant.

## Features

- One `climate` entity per AC, with `suggested_area` derived from the device name
- HVAC modes: Cool / Heat / Auto / Dry / Fan / Off
- Target temperature, fan speed (percentage **or** discrete levels — auto-detected
  per model), boost, horizontal/vertical swing, vane direction
- Real-time push via SSE; REST poll only as a 5-min/25-min safety fallback
- Proactive token refresh (before expiry) plus reactive refresh on `401`
- Rate-limit aware: persists the `Retry-After` cooldown across restarts and
  backs off instead of hammering the daily quota
- Re-auth flow in the UI when the refresh token finally dies

## Requirements

You need Home Connect OAuth credentials with **write** scopes
(`Control`, `WriteAppliance`, `Settings`, `Monitor`, …). The developer-portal
read-only scopes are **not** enough to set temperature/swing/boost. These are
produced by the companion [`acctl`](https://github.com/andreas16700/ac-local) CLI
via `acctl app-auth` (the official app's PKCE client), which gives you a
`client_id`, `access_token`, and `refresh_token`. Leave **Client Secret** empty
for app-auth (PKCE) credentials.

## Installation (HACS)

1. In HACS → **⋮** → **Custom repositories**, add
   `https://github.com/andreas16700/homeconnect-ac-control` with category
   **Integration**.
2. Install **Home Connect AC** and restart Home Assistant.
3. **Settings → Devices & Services → Add Integration → Home Connect AC**.
4. Paste your `client_id`, `access_token`, and `refresh_token` (and
   `client_secret` only if you have a non-PKCE developer client).

### Manual installation

Copy `custom_components/homeconnect_ac/` into your Home Assistant
`config/custom_components/` directory and restart.

## Re-authentication

Access tokens last ~24h and are refreshed automatically using the refresh
token. If the refresh token itself expires, the integration triggers a re-auth
flow — run `acctl app-auth` again to get fresh tokens and paste them when
prompted.

## Notes

- The Home Connect cloud enforces a **per-account daily quota** (~1000 calls)
  with aggressively escalating `Retry-After`. SSE is used for real-time updates
  precisely to stay well under it.
- Programs use the `selectonly` model (selected, not started/stopped); power is
  On/Standby only.

## License

MIT — see [LICENSE](LICENSE).
