"""
TUI — prompt_toolkit terminal dashboard backed by GTFSService.

Run: uv run python tui.py
"""

import asyncio
from datetime import datetime

from prompt_toolkit import Application
from prompt_toolkit.formatted_text import HTML
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout, HSplit, VSplit, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.widgets import Frame, TextArea, Label

from service import GTFSService, Vehicle, Stop

AGENCY = "prasarana"
CATEGORY = "rapid-bus-kl"
REFRESH_INTERVAL = 30  # seconds

# ── column layout ────────────────────────────────────────────────────────────

COLS = [
    ("Vehicle ID",   20),
    ("Route",        12),
    ("Latitude",     11),
    ("Longitude",    12),
    ("Speed km/h",   10),
    ("Status",       16),
    ("Age",           8),
]
COL_SEP = "  "

STATUS_STYLE = {
    "IN_TRANSIT_TO": "ansigreen",
    "STOPPED_AT":    "ansiyellow",
    "INCOMING_AT":   "ansicyan",
}

# ── rendering ─────────────────────────────────────────────────────────────────

def _cell(text: str, width: int) -> str:
    return text[:width].ljust(width)


def _age_str(v: Vehicle) -> str:
    if v.timestamp is None:
        return "-"
    from datetime import timezone
    age = int((datetime.now(timezone.utc) - v.timestamp).total_seconds())
    if age < 60:
        return f"{age}s"
    return f"{age // 60}m{age % 60:02d}s"


def render_vehicles(vehicles: list[Vehicle], filter_text: str) -> list:
    """Return prompt_toolkit (style, text) fragments."""
    lines: list = []

    # header
    header = COL_SEP.join(_cell(h, w) for h, w in COLS)
    lines.append(("bold", header + "\n"))
    total_width = sum(w for _, w in COLS) + len(COL_SEP) * (len(COLS) - 1)
    lines.append(("", "─" * total_width + "\n"))

    f = filter_text.strip().lower()
    shown = [
        v for v in vehicles
        if not f or f in v.route_name.lower() or f in v.vehicle_id.lower()
    ]
    shown.sort(key=lambda v: v.route_name)

    if not shown:
        lines.append(("italic", "  (no vehicles match)\n"))
    else:
        for v in shown:
            stale_prefix = "ansired" if v.is_stale else ""
            cells = [
                (v.vehicle_id,                                  None),
                (v.route_name,                                  None),
                (str(v.lat) if v.lat is not None else "-",      None),
                (str(v.lon) if v.lon is not None else "-",      None),
                (str(v.speed_kmh) if v.speed_kmh is not None else "-", None),
                (v.status,                                       STATUS_STYLE.get(v.status, "")),
                (_age_str(v),                                   "ansired" if v.is_stale else "ansibrightblack"),
            ]
            for i, ((text, style), (_, width)) in enumerate(zip(cells, COLS)):
                rendered = _cell(text, width)
                effective_style = style or stale_prefix
                lines.append((effective_style, rendered))
                if i < len(COLS) - 1:
                    lines.append(("", COL_SEP))
            lines.append(("", "\n"))

    lines.append(("", "\n"))
    suffix = f" matching '{f}'" if f else ""
    lines.append(("italic ansibrightblack", f"  {len(shown)} vehicle(s){suffix}\n"))
    return lines


# ── app ───────────────────────────────────────────────────────────────────────

class Dashboard:
    def __init__(self, service: GTFSService):
        self._service = service
        self._vehicles: list[Vehicle] = []
        self._status = "Initialising…"
        self._app: Application | None = None

        self.filter_area = TextArea(
            text="",
            multiline=False,
            height=1,
            prompt="Filter: ",
        )
        self.filter_area.buffer.on_text_changed += self._on_filter_change

        kb = KeyBindings()

        @kb.add("c-c")
        @kb.add("q")
        def _quit(event):
            event.app.exit()

        @kb.add("c-r")
        def _refresh(event):
            asyncio.ensure_future(self._fetch())

        @kb.add("tab")
        def _focus_filter(event):
            event.app.layout.focus(self.filter_area)

        layout = Layout(
            HSplit([
                Window(
                    FormattedTextControl(HTML(
                        f"<b>  {service.agency.upper()} / {service.category or ''}"
                        "  —  Live Vehicle Dashboard</b>"
                    )),
                    height=1,
                    style="bg:#003366 #ffffff",
                ),
                Window(height=1),
                Frame(
                    Window(
                        FormattedTextControl(self._get_table_text, focusable=False),
                        wrap_lines=False,
                    ),
                    title="Vehicles",
                ),
                Window(height=1),
                VSplit([
                    Frame(self.filter_area, title="Filter (Tab to focus)", width=42),
                    Window(
                        FormattedTextControl(self._get_status_text, focusable=False),
                        style="italic",
                    ),
                ], height=3),
                Window(
                    FormattedTextControl(HTML(
                        "  <b>q</b> quit  "
                        "<b>Ctrl-R</b> refresh  "
                        "<b>Tab</b> focus filter"
                    )),
                    height=1,
                    style="bg:#1a1a1a #888888",
                ),
            ])
        )

        self._app = Application(layout=layout, key_bindings=kb, full_screen=True)

    def _on_filter_change(self, _):
        if self._app:
            self._app.invalidate()

    def _get_table_text(self):
        return render_vehicles(self._vehicles, self.filter_area.text)

    def _get_status_text(self):
        return self._status

    def _set_status(self, text: str):
        self._status = text
        if self._app:
            self._app.invalidate()

    async def _fetch(self):
        self._set_status("Fetching vehicles…")
        try:
            self._vehicles = await self._service.get_vehicles()
            now = datetime.now().strftime("%H:%M:%S")
            stale = sum(1 for v in self._vehicles if v.is_stale)
            stale_note = f"  ({stale} stale)" if stale else ""
            self._set_status(
                f"  {len(self._vehicles)} vehicles{stale_note}  —  "
                f"updated {now}  —  "
                f"auto-refresh every {REFRESH_INTERVAL}s  —  "
                f"{len(self._service.routes)} routes loaded"
            )
        except Exception as e:
            self._set_status(f"Fetch error: {e}")

    async def _poll_loop(self):
        self._set_status("Downloading static data…")
        try:
            await self._service.load_static()
        except Exception as e:
            self._set_status(f"Error loading static data: {e}")
            return
        while True:
            await self._fetch()
            await asyncio.sleep(REFRESH_INTERVAL)

    def run(self):
        async def _main():
            asyncio.ensure_future(self._poll_loop())
            await self._app.run_async()

        asyncio.run(_main())


def main():
    service = GTFSService(AGENCY, CATEGORY)
    Dashboard(service).run()


if __name__ == "__main__":
    main()
