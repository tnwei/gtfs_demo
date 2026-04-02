import asyncio
import io
import zipfile
import csv
from datetime import datetime

import niquests
from google.transit import gtfs_realtime_pb2
from prompt_toolkit import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.filters import Condition
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout, HSplit, VSplit, Window, FloatContainer, Float
from prompt_toolkit.layout.controls import FormattedTextControl, BufferControl
from prompt_toolkit.layout.dimension import D
from prompt_toolkit.widgets import Frame, TextArea, SearchToolbar

STATIC_URL = "https://api.data.gov.my/gtfs-static/prasarana?category=rapid-bus-kl"
REALTIME_URL = "https://api.data.gov.my/gtfs-realtime/vehicle-position/prasarana?category=rapid-bus-kl"
REFRESH_INTERVAL = 30

# ── data fetching ────────────────────────────────────────────────────────────

def load_static_routes() -> dict[str, str]:
    resp = niquests.get(STATIC_URL, timeout=30)
    resp.raise_for_status()
    routes = {}
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        with zf.open("routes.txt") as f:
            reader = csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig"))
            for row in reader:
                routes[row["route_id"]] = (
                    row.get("route_short_name") or row.get("route_long_name", row["route_id"])
                )
    return routes


def fetch_vehicles(routes: dict[str, str]) -> list[dict]:
    resp = niquests.get(REALTIME_URL, timeout=10)
    resp.raise_for_status()
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.ParseFromString(resp.content)
    vehicles = []
    for entity in feed.entity:
        if not entity.HasField("vehicle"):
            continue
        v = entity.vehicle
        route_id = v.trip.route_id if v.HasField("trip") else ""
        vehicles.append({
            "id":     v.vehicle.id if v.HasField("vehicle") else entity.id,
            "route":  routes.get(route_id, route_id or "?"),
            "lat":    round(v.position.latitude,  5) if v.HasField("position") else None,
            "lon":    round(v.position.longitude, 5) if v.HasField("position") else None,
            "speed":  round(v.position.speed * 3.6, 1) if v.HasField("position") and v.position.speed else None,
            "status": gtfs_realtime_pb2.VehiclePosition.VehicleStopStatus.Name(v.current_status),
        })
    return vehicles

# ── rendering ────────────────────────────────────────────────────────────────

COL_WIDTHS = [20, 12, 11, 12, 12, 18]
COL_HEADERS = ["Vehicle ID", "Route", "Latitude", "Longitude", "Speed km/h", "Status"]
STATUS_ANSI = {
    "IN_TRANSIT_TO": "ansigreen",
    "STOPPED_AT":    "ansiyellow",
    "INCOMING_AT":   "ansicyan",
}


def _row(cells: list[str], styles: list[str | None] = None) -> HTML:
    parts = []
    for i, (cell, width) in enumerate(zip(cells, COL_WIDTHS)):
        text = cell[:width].ljust(width)
        style = (styles or [])[i] if styles and i < len(styles) else None
        if style:
            parts.append(f"<{style}>{text}</{style}>")
        else:
            parts.append(text)
    return HTML("  ".join(parts))


def render_table(vehicles: list[dict], filter_text: str) -> list:
    """Return a list of (style, text) tuples for FormattedTextControl."""
    lines: list = []

    def add(html_line: str):
        lines.append(("", html_line + "\n"))

    # header
    header = "  ".join(h[:w].ljust(w) for h, w in zip(COL_HEADERS, COL_WIDTHS))
    lines.append(("bold", header + "\n"))
    lines.append(("", "─" * sum(COL_WIDTHS + [2 * (len(COL_WIDTHS) - 1)]) + "\n"))

    f = filter_text.lower()
    shown = [v for v in vehicles if not f or f in v["route"].lower() or f in v["id"].lower()]
    shown.sort(key=lambda v: v["route"])

    if not shown:
        lines.append(("", "  (no vehicles match)\n"))
    else:
        for v in shown:
            cells = [
                v["id"],
                v["route"],
                str(v["lat"]) if v["lat"] is not None else "-",
                str(v["lon"]) if v["lon"] is not None else "-",
                str(v["speed"]) if v["speed"] is not None else "-",
                v["status"],
            ]
            # build styled line
            row_parts: list = []
            for i, (cell, width) in enumerate(zip(cells, COL_WIDTHS)):
                text = cell[:width].ljust(width)
                if i == 5:  # status column
                    color = STATUS_ANSI.get(v["status"], "")
                    if color:
                        row_parts.append((color, text))
                    else:
                        row_parts.append(("", text))
                else:
                    row_parts.append(("", text))
                if i < len(cells) - 1:
                    row_parts.append(("", "  "))
            row_parts.append(("", "\n"))
            lines.extend(row_parts)

    lines.append(("", "\n"))
    lines.append(("italic", f"  {len(shown)} vehicle(s) shown\n"))
    return lines

# ── app ──────────────────────────────────────────────────────────────────────

class Dashboard:
    def __init__(self):
        self._routes: dict[str, str] = {}
        self._vehicles: list[dict] = []
        self._status = "Starting…"
        self._filter = ""
        self._app: Application | None = None

        self.filter_area = TextArea(
            text="",
            multiline=False,
            height=1,
            prompt="Filter: ",
        )
        self.filter_area.buffer.on_text_changed += self._on_filter_change

        self.table_control = FormattedTextControl(
            text=self._get_table_text,
            focusable=False,
        )

        self.status_control = FormattedTextControl(
            text=self._get_status_text,
            focusable=False,
        )

        kb = KeyBindings()

        @kb.add("c-c")
        @kb.add("q")
        def _quit(event):
            event.app.exit()

        @kb.add("c-r")
        def _refresh(event):
            if self._app:
                asyncio.ensure_future(self._fetch_vehicles())

        layout = Layout(
            HSplit([
                Window(
                    FormattedTextControl(HTML("<b>  Rapid Bus KL — Live Vehicles</b>")),
                    height=1,
                    style="bg:#004488 #ffffff",
                ),
                Window(height=1),  # spacer
                Frame(
                    Window(self.table_control, wrap_lines=False),
                    title="Vehicles",
                ),
                Window(height=1),  # spacer
                VSplit([
                    Frame(self.filter_area, title="Filter (type to search)", width=40),
                    Window(self.status_control, style="italic"),
                ], height=3),
                Window(
                    FormattedTextControl(HTML(
                        "  <b>q</b> quit   <b>Ctrl-R</b> refresh now   "
                        "<b>Tab</b> focus filter"
                    )),
                    height=1,
                    style="bg:#222222 #aaaaaa",
                ),
            ])
        )

        self._app = Application(
            layout=layout,
            key_bindings=kb,
            full_screen=True,
        )

    def _on_filter_change(self, _):
        self._filter = self.filter_area.text
        if self._app:
            self._app.invalidate()

    def _get_table_text(self):
        return render_table(self._vehicles, self._filter)

    def _get_status_text(self):
        return self._status

    async def _load_static(self):
        self._status = "Downloading static route data…"
        self._app.invalidate()
        loop = asyncio.get_event_loop()
        try:
            self._routes = await loop.run_in_executor(None, load_static_routes)
            self._status = f"Loaded {len(self._routes)} routes. Fetching vehicles…"
        except Exception as e:
            self._status = f"Error loading static data: {e}"
        self._app.invalidate()

    async def _fetch_vehicles(self):
        try:
            loop = asyncio.get_event_loop()
            self._vehicles = await loop.run_in_executor(None, fetch_vehicles, self._routes)
            now = datetime.now().strftime("%H:%M:%S")
            self._status = (
                f"  {len(self._vehicles)} vehicles active   "
                f"last updated {now}   "
                f"(Ctrl-R to refresh, auto in {REFRESH_INTERVAL}s)"
            )
        except Exception as e:
            self._status = f"Fetch error: {e}"
        self._app.invalidate()

    async def _poll_loop(self):
        await self._load_static()
        while True:
            await self._fetch_vehicles()
            await asyncio.sleep(REFRESH_INTERVAL)

    def run(self):
        async def _main():
            asyncio.ensure_future(self._poll_loop())
            await self._app.run_async()

        asyncio.run(_main())


def main():
    Dashboard().run()


if __name__ == "__main__":
    main()
