"""
GTFSService — facade over data.gov.my GTFS static + realtime feeds.

All data fetching, parsing, and quality rules live here.
Consumers (TUI, web API) work only with the dataclasses defined below.
"""

from __future__ import annotations

import asyncio
import csv
import io
import math
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

import niquests
from google.transit import gtfs_realtime_pb2

# ── Malaysia bounding box ────────────────────────────────────────────────────
_MY_LAT = (0.8, 7.4)
_MY_LON = (99.5, 119.5)

# Vehicle GPS is considered stale after this many seconds
STALE_AFTER_SECONDS = 300


# ── Domain types ─────────────────────────────────────────────────────────────

@dataclass
class Route:
    route_id: str
    short_name: str
    long_name: str


@dataclass
class Stop:
    stop_id: str
    name: str
    lat: float
    lon: float
    distance_m: float | None = None  # populated by get_nearest_stops


@dataclass
class Vehicle:
    vehicle_id: str
    route_id: str
    route_name: str
    lat: float | None
    lon: float | None
    speed_kmh: float | None
    status: str
    timestamp: datetime | None
    is_stale: bool = False


# ── Helpers ───────────────────────────────────────────────────────────────────

def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Distance in metres between two lat/lon points."""
    R = 6_371_000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return R * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _in_malaysia(lat: float, lon: float) -> bool:
    return _MY_LAT[0] <= lat <= _MY_LAT[1] and _MY_LON[0] <= lon <= _MY_LON[1]


# ── Service ───────────────────────────────────────────────────────────────────

class GTFSService:
    """
    One instance per (agency, category) pair.

    Usage:
        svc = GTFSService("prasarana", "rapid-bus-kl")
        await svc.load_static()
        vehicles = await svc.get_vehicles()
    """

    BASE = "https://api.data.gov.my"

    def __init__(self, agency: str, category: str | None = None):
        self.agency = agency
        self.category = category

        self._routes: dict[str, Route] = {}
        self._stops: list[Stop] = []
        self._static_loaded = False

        qs = f"?category={category}" if category else ""
        self._static_url = f"{self.BASE}/gtfs-static/{agency}{qs}"
        self._vehicles_url = f"{self.BASE}/gtfs-realtime/vehicle-position/{agency}{qs}"
        self._trips_url = f"{self.BASE}/gtfs-realtime/trip-updates/{agency}{qs}"

    # ── static data ───────────────────────────────────────────────────────────

    async def load_static(self) -> None:
        """Download and cache static GTFS data (routes + stops)."""
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(None, self._load_static_sync)
        self._static_loaded = True

    def _load_static_sync(self) -> None:
        resp = niquests.get(self._static_url, timeout=30)
        resp.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            self._routes = self._parse_routes(zf)
            self._stops = self._parse_stops(zf)

    def _parse_routes(self, zf: zipfile.ZipFile) -> dict[str, Route]:
        routes: dict[str, Route] = {}
        with zf.open("routes.txt") as f:
            for row in csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig")):
                routes[row["route_id"]] = Route(
                    route_id=row["route_id"],
                    short_name=row.get("route_short_name", ""),
                    long_name=row.get("route_long_name", ""),
                )
        return routes

    def _parse_stops(self, zf: zipfile.ZipFile) -> list[Stop]:
        stops: list[Stop] = []
        with zf.open("stops.txt") as f:
            for row in csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig")):
                try:
                    lat, lon = float(row["stop_lat"]), float(row["stop_lon"])
                except (ValueError, KeyError):
                    continue
                stops.append(Stop(
                    stop_id=row["stop_id"],
                    name=row.get("stop_name", row["stop_id"]),
                    lat=lat,
                    lon=lon,
                ))
        return stops

    # ── realtime data ─────────────────────────────────────────────────────────

    async def get_vehicles(self) -> list[Vehicle]:
        """Fetch current vehicle positions. Returns only quality-checked vehicles."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._fetch_vehicles_sync)

    def _fetch_vehicles_sync(self) -> list[Vehicle]:
        resp = niquests.get(self._vehicles_url, timeout=10)
        resp.raise_for_status()
        feed = gtfs_realtime_pb2.FeedMessage()
        feed.ParseFromString(resp.content)

        now = datetime.now(timezone.utc)
        vehicles: list[Vehicle] = []

        for entity in feed.entity:
            if not entity.HasField("vehicle"):
                continue
            v = entity.vehicle

            lat = v.position.latitude if v.HasField("position") else None
            lon = v.position.longitude if v.HasField("position") else None

            # bounding box quality check
            if lat is not None and lon is not None:
                if not _in_malaysia(lat, lon):
                    continue
                lat = round(lat, 5)
                lon = round(lon, 5)

            # staleness check
            ts: datetime | None = None
            is_stale = False
            if v.timestamp:
                ts = datetime.fromtimestamp(v.timestamp, tz=timezone.utc)
                age = (now - ts).total_seconds()
                is_stale = age > STALE_AFTER_SECONDS

            route_id = v.trip.route_id if v.HasField("trip") else ""
            route = self._routes.get(route_id)
            route_name = route.short_name or route.long_name if route else (route_id or "?")

            vehicles.append(Vehicle(
                vehicle_id=v.vehicle.id if v.HasField("vehicle") else entity.id,
                route_id=route_id,
                route_name=route_name,
                lat=lat,
                lon=lon,
                speed_kmh=round(v.position.speed * 3.6, 1) if v.HasField("position") and v.position.speed else None,
                status=gtfs_realtime_pb2.VehiclePosition.VehicleStopStatus.Name(v.current_status),
                timestamp=ts,
                is_stale=is_stale,
            ))

        return vehicles

    # ── stop queries ──────────────────────────────────────────────────────────

    def get_nearest_stops(self, lat: float, lon: float, n: int = 5) -> list[Stop]:
        """Return n nearest stops to (lat, lon), with distance_m populated."""
        if not self._stops:
            return []
        ranked = sorted(
            self._stops,
            key=lambda s: _haversine_m(lat, lon, s.lat, s.lon),
        )[:n]
        for stop in ranked:
            stop.distance_m = round(_haversine_m(lat, lon, stop.lat, stop.lon))
        return ranked

    # ── properties ────────────────────────────────────────────────────────────

    @property
    def routes(self) -> dict[str, Route]:
        return self._routes

    @property
    def stops(self) -> list[Stop]:
        return self._stops

    @property
    def label(self) -> str:
        return f"{self.agency}/{self.category}" if self.category else self.agency
