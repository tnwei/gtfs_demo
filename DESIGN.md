# DESIGN.md

## Architecture

This is a two-layer polling app with no persistence layer.

```
[data.gov.my GTFS Static API]      [data.gov.my GTFS Realtime API]
        |                                       |
  download once at startup               poll every 30s
  (ZIP → routes.txt CSV)               (Protobuf binary)
        |                                       |
        └──────────────┬────────────────────────┘
                       ↓
              in-memory dict
              route_id → name
                       ↓
              fetch_vehicles()
              joins realtime feed
              against static routes
                       ↓
              Rich Live table
              printed to terminal
```

**Static data** (`load_static_routes`): Downloaded once on startup. The GTFS ZIP contains schedule CSVs; we only use `routes.txt` to build a `route_id → route_name` lookup dict held in memory.

**Realtime data** (`fetch_vehicles`): Fetched on every tick. The protobuf `FeedMessage` is parsed into a list of dicts — each representing one vehicle with position, speed, and stop status.

**Display** (`build_table`): Pure function — takes the vehicle list and renders a Rich table. Filtered by an optional route substring argument.

**No persistence.** There is no database, no cache, no history. Each fetch is independent.

---

## What's available in the dataset that we haven't used yet

The static ZIP also contains:

| File | What's in it |
|---|---|
| `stops.txt` | Every bus stop — name, lat/lon |
| `trips.txt` | trip_id → route_id + service_id mapping |
| `stop_times.txt` | Scheduled arrival/departure at each stop per trip |
| `calendar.txt` / `calendar_dates.txt` | Which days each service runs |
| `shapes.txt` | Route polylines (lat/lon sequences for drawing routes) |

The realtime API also has a **trip updates** feed (not yet used):

```
https://api.data.gov.my/gtfs-realtime/trip-updates/prasarana?category=rapid-bus-kl
```

This gives predicted arrival times at each stop — the ingredient for a "next bus" board.

Other agencies available: `rapid-bus-penang`, `rapid-bus-kuantan`, `mrt-feeder`, `ktmb`, `bas` (covers 10+ other cities).

---

## Ideas for making this more useful

### Explore the dataset further
- **Next bus at stop**: Cross-reference `stop_times.txt` (schedule) + trip updates realtime feed → "next 3 buses arriving at stop X". Closest thing to a real transit app.
- **Route coverage map**: Parse `shapes.txt` and render an ASCII/map of route paths.
- **Fleet health dashboard**: Track how many vehicles are reporting per agency over time — gaps could indicate GPS dropouts or service suspensions.
- **Historical vehicle tracking**: Log positions to SQLite every 30s and replay or analyse dwell times, speed distributions.

### As a public service
A useful public-facing product would be a **next-bus web app** (think: moovit/citymapper for MY). The data is free and public, the GTFS format is standard, and there's no equivalent well-known local product. The real work would be:
- A backend that caches static data and polls realtime, exposing a clean REST/WebSocket API
- A frontend map (Leaflet/MapLibre) showing live bus dots + stop search
- Hosting costs are very low — the feeds are small and free

Risk: GPS accuracy on some bus feeds is noted as poor. Worth building a data quality layer early.

### As a workshop vehicle
This project is well-suited for a multi-track internal workshop:

**AI-assisted coding track**: The project is small enough to understand end-to-end in 30 minutes, but has enough real moving parts (network, binary formats, external API, data joining) that AI assistance makes a meaningful difference. Participants can extend it — add a new agency, a new feature, a new view — and compare how they'd do it with vs. without AI help.

**Git track**: Intentionally set up two branches with conflicting changes to the same functions (e.g. one branch adds stop lookup, another changes how routes are loaded). Participants practice resolving merge conflicts and interactive rebase. The code is short enough that conflicts are readable, not overwhelming.

**Architectural thinking track**: Use the current design as a starting point and ask participants to redesign it for scale: What if 1000 users are querying simultaneously? What if you want to store 30 days of history? What if you need to support 5 agencies with different update cadences? Walk through tradeoffs of polling vs. streaming, SQLite vs. Postgres, monolith vs. microservice.

---

## Alternative frontends

**Rich is functional but ugly.** Better options, roughly ordered by effort:

| Option | What it looks like | Effort | Notes |
|---|---|---|---|
| **Textual** | Full TUI with panels, maps, live updates, mouse support | Low–Medium | Python-native, same ecosystem, much better looking than Rich tables |
| **Streamlit** | Auto-generated web dashboard, runs locally | Low | Add `st.map()` for a live vehicle map in ~10 lines. Great for demos. |
| **Folium / Leaflet** | Static or auto-refreshing HTML map with bus markers | Low | Export to HTML, open in browser. No server needed. |
| **FastAPI + HTMX** | Lightweight web app, server-side rendered, live-polling | Medium | Clean, no JS framework, easy to deploy |
| **FastAPI + MapLibre/Deck.gl** | Proper interactive map, animated vehicle movement | Medium–High | Best end-user experience; what a real public service would use |
| **Datasette** | Auto-UI over SQLite; just log data and browse/query it | Low | Best for exploration and the workshop — zero frontend code |

**Recommendation for the workshop**: Textual for the TUI track (low barrier, visually impressive), Streamlit for the web track (fastest path to a map). Both are Python-only, no JS knowledge required.

**Recommendation for a public service**: FastAPI backend + MapLibre frontend. MapLibre is open-source, handles large numbers of animated markers well, and the combination is a standard modern stack.
