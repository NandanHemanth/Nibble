"""The live-world layer on the globe, from keyless public feeds, plus ZIP lookup.

USGS: earthquakes of magnitude 4.5+ in the last day.
NASA EONET: open natural events (wildfires, severe storms, volcanoes).
Zippopotam: ZIP code to coordinates.
"""
from __future__ import annotations

import re
import time

import httpx

from .obs import set_result, tool_span

USGS_URL = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/4.5_day.geojson"
EONET_URL = "https://eonet.gsfc.nasa.gov/api/v3/events?status=open&limit=60&days=14"
ZIP_URL = "https://api.zippopotam.us/us/{zip}"
GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search?count=1&language=en&format=json&name={name}"

_cache: dict[str, tuple[float, object]] = {}

# Used only when the ZIP lookup is unreachable.
ZIP_FALLBACK = {
    "0": (41.8, -72.7, "Northeast US"), "1": (40.7, -74.0, "New York area"), "2": (38.9, -77.0, "Mid-Atlantic US"),
    "3": (33.7, -84.4, "Southeast US"), "4": (39.8, -86.2, "Great Lakes US"), "5": (41.6, -93.6, "Upper Midwest US"),
    "6": (41.9, -87.6, "Central US"), "7": (32.8, -96.8, "South Central US"), "8": (39.7, -105.0, "Mountain US"),
    "9": (37.8, -122.4, "West Coast US"),
}


async def _get_json(url: str, ttl: int, tool: str) -> object:
    hit = _cache.get(url)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    with tool_span(tool, {"url": url}, "World Watch", "Public data feed") as span:
        async with httpx.AsyncClient(timeout=12, follow_redirects=True) as http:
            resp = await http.get(url)
            resp.raise_for_status()
            data = resp.json()
        set_result(span, {"bytes": len(resp.content)})
    _cache[url] = (time.time(), data)
    return data


async def locate_zip(zip_code: str) -> dict:
    zip_code = "".join(ch for ch in zip_code if ch.isdigit())[:5]
    if len(zip_code) == 5:
        try:
            data = await _get_json(ZIP_URL.format(zip=zip_code), 86400, "geo.zip_lookup")
            place = data["places"][0]  # type: ignore[index]
            return {
                "zip": zip_code,
                "lat": float(place["latitude"]),
                "lng": float(place["longitude"]),
                "label": f'{place["place name"]}, {place["state abbreviation"]}',
                "approximate": False,
            }
        except Exception:
            pass
    lat, lng, label = ZIP_FALLBACK.get(zip_code[:1], (39.5, -98.35, "United States"))
    return {"zip": zip_code, "lat": lat, "lng": lng, "label": label, "approximate": True}


async def live_events() -> dict:
    """Events for the globe. Each feed fails independently."""
    events: list[dict] = []
    errors: list[str] = []
    try:
        quakes = await _get_json(USGS_URL, 300, "usgs.earthquakes")
        for f in quakes.get("features", [])[:60]:  # type: ignore[union-attr]
            lng, lat = f["geometry"]["coordinates"][:2]
            p = f["properties"]
            events.append({"kind": "earthquake", "lat": lat, "lng": lng, "title": p.get("title") or "Earthquake",
                           "size": p.get("mag") or 4.5, "url": p.get("url"), "time": p.get("time")})
    except Exception as exc:
        errors.append(f"USGS: {type(exc).__name__}")
    try:
        eonet = await _get_json(EONET_URL, 900, "nasa.eonet")
        for e in eonet.get("events", []):  # type: ignore[union-attr]
            geo = (e.get("geometry") or [])[-1:] or []
            if not geo or geo[0].get("type") != "Point":
                continue
            lng, lat = geo[0]["coordinates"][:2]
            cat = ((e.get("categories") or [{}])[0].get("id") or "event").lower()
            kind = {"wildfires": "wildfire", "severestorms": "storm", "volcanoes": "volcano", "floods": "flood",
                    "drought": "drought", "seaLakeIce": "ice"}.get(cat, cat)
            events.append({"kind": kind, "lat": lat, "lng": lng, "title": e.get("title") or kind.title(),
                           "size": 5, "url": (e.get("sources") or [{}])[0].get("url") or e.get("link"),
                           "time": geo[0].get("date")})
    except Exception as exc:
        errors.append(f"NASA EONET: {type(exc).__name__}")
    return {"events": events, "errors": errors}


async def geocode(place: str) -> tuple[float, float] | None:
    """Coordinates for a place name ('Minas Gerais, Brazil') via Open-Meteo's
    keyless geocoder. Tries the most specific part first, then the next."""
    from urllib.parse import quote

    parts = [p.strip() for p in re.split(r",| and |\(|\)|/", place) if p.strip()]
    for name in parts[:3]:
        name = re.sub(r"^(south|north|east|west|southwest|southeast|northwest|northeast|central|port of)\s+", "", name, flags=re.I)
        try:
            data = await _get_json(GEOCODE_URL.format(name=quote(name)), 86400, "geo.place_lookup")
            hit = (data.get("results") or [None])[0]  # type: ignore[union-attr]
            if hit:
                return float(hit["latitude"]), float(hit["longitude"])
        except Exception:
            continue
    return None
