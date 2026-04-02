# ROADMAP.md

## Goal

Build a public-good Malaysian transit dashboard — live vehicle positions, next arrivals at
stops, and data quality transparency — for all agencies on data.gov.my. Start in the
terminal. Graduate to a web frontend without rewriting the core logic.

Philosophy: **build to last, small is beautiful, efficiency is king.**

---

## Architecture overview

```
┌─────────────────────────────────────────────────────┐
│                  GTFSService (facade)                │
│                                                     │
│  • owns all data fetching and caching               │
│  • speaks in plain Python dataclasses               │
│  • knows nothing about TUI or HTTP                  │
└────────────────┬────────────────────────────────────┘
                 │ same interface, two consumers
       ┌─────────┴──────────┐
       ▼                    ▼
  tui.py              api.py (Litestar)
  prompt_toolkit      thin HTTP wrapper
  terminal views      around the same service
```

The facade is the key design decision. The TUI and the web API are both thin shells
over `GTFSService`. When we graduate from terminal to web, the service layer is
untouched — only the presentation layer changes.

---

## File layout

```
gtfs_demo/
├── main.py           # original PoC — leave alone
├── service.py        # GTFSService facade  ← stage 1
├── db.py             # SQLite persistence  ← stage 3
├── poller.py         # background workers  ← stage 4
├── tui.py            # prompt_toolkit TUI  ← stage 1
└── api.py            # Litestar web API    ← stage 5
```

---

## Stages

### Stage 1 — Facade + TUI rewrite (now)

Introduce `service.py` as the clean data layer. Rewrite the TUI in `tui.py` to call
only service methods. Validate that the facade contract is correct by running the TUI.

**service.py** exposes:
- `GTFSService(agency, category)` — one instance per agency
- `async load_static()` — downloads ZIP, populates routes + stops in memory
- `async get_vehicles()` — fetches and parses realtime vehicle positions
- `async get_nearest_stops(lat, lon, n)` — haversine nearest-stop lookup
- Returns typed dataclasses: `Vehicle`, `Stop`, `Route`

**tui.py** calls only `GTFSService` methods. No direct HTTP, no protobuf parsing.

---

### Stage 2 — Next arrivals at a stop (terminal)

Add to `service.py`:
- `async get_arrivals(stop_id)` — joins `stop_times.txt` (static schedule) with the
  realtime trip updates feed to give predicted arrival times

New terminal view in `tui.py`: type a stop name, get countdown to next 3 buses.

This is the hardest data join in the whole system. Getting it right in the terminal
before building the web API avoids the classic mistake of baking business logic into
HTTP handlers.

Requires fetching the trip updates feed:
`https://api.data.gov.my/gtfs-realtime/trip-updates/prasarana?category=rapid-bus-kl`

---

### Stage 3 — Persistence (SQLite)

Introduce `db.py`:
- Schema: `positions(ts, agency, vehicle_id, route_id, lat, lon, speed, status)`
- Schema: `static_cache(agency, category, fetched_at, routes_json, stops_json)`
- `GTFSService` writes to DB on every fetch; static data is re-downloaded weekly

New terminal view: historical query — "how many buses on route X at time T?"

SQLite with WAL mode. One file. Zero ops. Target: < 50 MB/day for all agencies.

---

### Stage 4 — All agencies + poller

Introduce `poller.py`:
- One async task per agency, running independently
- Each task calls `service.get_vehicles()` every 30s (or per-agency cadence)
- Writes to DB via `db.py`
- Tracks feed health: last successful fetch, consecutive failure count

New terminal view: agency health summary — vehicles active, last updated, GPS quality.

Agencies to cover:
| Agency | Category | Notes |
|---|---|---|
| prasarana | rapid-bus-kl | richest feed |
| prasarana | rapid-bus-penang | |
| prasarana | rapid-bus-kuantan | |
| prasarana | mrt-feeder | |
| ktmb | (none) | rail |
| bas | (many) | 10+ cities |

---

### Stage 5 — Web API (Litestar) + frontend

Introduce `api.py` (Litestar):
- `GET /agencies` — list all agencies and their health
- `GET /vehicles?agency=&category=` — current vehicle positions
- `GET /stops?lat=&lon=&radius=` — nearby stops
- `GET /stops/{stop_id}/arrivals` — next arrivals
- `GET /routes/{route_id}/vehicles` — all live vehicles on a route

All handlers call `GTFSService` methods. No business logic in the API layer.

Poller runs as a background task inside the same Litestar process (via
`on_startup` lifespan hook). No separate worker process needed at this scale.

Frontend: single static HTML file, served by Litestar's `StaticFilesConfig`.
MapLibre GL JS via CDN `<script>` tag — no build step, no node_modules.
Polls `/vehicles` every 30s, animates markers on the map.

---

## Data quality rules (apply from Stage 1)

These rules should live in `service.py` so they're enforced everywhere:

1. **Staleness**: if `vehicle.timestamp` is more than 5 minutes old, mark as stale.
   Do not show stale vehicles in the "live" view.
2. **Position sanity**: drop vehicles with lat/lon outside Malaysia bounding box
   (roughly lat 0.8–7.4, lon 99.5–119.5).
3. **Static freshness**: re-download static ZIP if older than 7 days.

---

## Rationale for key choices

**Litestar over FastAPI**: Litestar has a cleaner dependency injection system, better
typing support, and native dataclass/`attrs` integration. For a service that returns
typed dataclasses from `service.py`, Litestar's serialisation is more ergonomic.

**SQLite over Postgres**: The write load is ~1 row per vehicle per 30s. Even at 500
vehicles across all agencies that's ~1,500 rows/min — well within SQLite WAL limits.
A single file means zero ops, trivial backup (just copy the file), and easy local
development. Migrate to Postgres only if you hit actual write contention.

**prompt_toolkit over Rich/Textual**: Plain ANSI output, no async runtime surprises,
full control over layout and keybindings, minimal abstraction between you and the
terminal.

**Facade pattern**: The TUI and API are both consumers of `GTFSService`. The service
speaks in domain types (`Vehicle`, `Stop`, `Arrival`), not HTTP response dicts or
protobuf objects. This means:
- Protobuf parsing lives in exactly one place
- Business rules (staleness, bounding box) are enforced once
- Switching from one realtime feed format to another is a one-file change
- The TUI is a valid integration test for the service before the API exists
