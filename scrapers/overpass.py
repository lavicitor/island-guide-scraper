from __future__ import annotations

import logging
import re

from core.http_client import HttpClient
from core.models import ScraperResult

logger = logging.getLogger(__name__)

OVERPASS_URL = "https://overpass-api.de/api/interpreter"

_CAR_HIGHWAY_TAGS = {
    "motorway", "trunk", "primary", "secondary", "tertiary",
    "unclassified", "residential", "living_street",
}

_LANDMARK_SKIP_HISTORIC = {"wayside_shrine", "wayside_cross", "boundary_stone", "milestone"}

_POI_QUERIES = """
  node["natural"="beach"](area.a);
  way["natural"="beach"](area.a);
  node["leisure"="beach"](area.a);
  node["amenity"="restaurant"](area.a);
  node["amenity"="cafe"](area.a);
  node["amenity"="bar"](area.a);
  node["amenity"="atm"](area.a);
  node["amenity"="bank"]["atm"="yes"](area.a);
  node["amenity"="pharmacy"](area.a);
  node["amenity"="doctors"](area.a);
  node["amenity"="hospital"](area.a);
  node["tourism"="hotel"](area.a);
  node["tourism"="guest_house"](area.a);
  node["tourism"="apartment"](area.a);
  node["tourism"="hostel"](area.a);
  node["historic"~"castle|tower|monument|memorial|ruins|archaeological_site|chapel|church|fortification|city_gate"](area.a);
  node["tourism"="attraction"](area.a);
  node["amenity"="place_of_worship"](area.a);
  node["amenity"="ferry_terminal"](area.a);
"""

_POI_QUERIES_BBOX = """
  node["natural"="beach"]({bbox});
  way["natural"="beach"]({bbox});
  node["leisure"="beach"]({bbox});
  node["amenity"="restaurant"]({bbox});
  node["amenity"="cafe"]({bbox});
  node["amenity"="bar"]({bbox});
  node["amenity"="atm"]({bbox});
  node["amenity"="bank"]["atm"="yes"]({bbox});
  node["amenity"="pharmacy"]({bbox});
  node["amenity"="doctors"]({bbox});
  node["amenity"="hospital"]({bbox});
  node["tourism"="hotel"]({bbox});
  node["tourism"="guest_house"]({bbox});
  node["tourism"="apartment"]({bbox});
  node["tourism"="hostel"]({bbox});
  node["historic"~"castle|tower|monument|memorial|ruins|archaeological_site|chapel|church|fortification|city_gate"]({bbox});
  node["tourism"="attraction"]({bbox});
  node["amenity"="place_of_worship"]({bbox});
  node["amenity"="ferry_terminal"]({bbox});
"""


def scrape_overpass(
    island_name: str,
    lat: float,
    lon: float,
    client: HttpClient,
    nearby_radius_m: int = 30000,
    user_agent: str = "island-guide/1.0",
) -> ScraperResult:
    try:
        elements = _fetch_poi_elements(island_name, lat, lon, client, user_agent)
        pois = _parse_elements(elements)

        car_free = _derive_car_free(elements)
        pois["car_free"] = car_free

        nearby = _find_nearby_islands(island_name, lat, lon, client, nearby_radius_m, user_agent)
        pois["nearby_islands"] = nearby

        total = sum(
            len(pois.get(k, []))
            for k in ("beaches", "restaurants", "atms", "medical", "accommodation", "landmarks")
        )
        coverage = min(total / 10.0, 1.0) * 100
        return ScraperResult(success=True, data=pois, source="overpass", coverage_pct=round(coverage, 1))
    except Exception as exc:
        logger.error("Overpass scraper failed: %s", exc)
        return ScraperResult(success=False, data=None, source="overpass", error=str(exc))


def _fetch_poi_elements(
    island_name: str, lat: float, lon: float, client: HttpClient, user_agent: str
) -> list[dict]:
    query = _build_area_query(island_name)
    try:
        result = client.post_raw(OVERPASS_URL, form_data={"data": query}, user_agent=user_agent)
        elements = result.get("elements", [])
        if len(elements) > 0:
            logger.info("Overpass area query returned %d elements for '%s'", len(elements), island_name)
            return elements
        logger.info("Overpass area query returned 0 elements for '%s' — falling back to bbox", island_name)
    except Exception as exc:
        logger.warning("Overpass area query failed: %s — falling back to bbox", exc)

    bbox_query = _build_bbox_query(lat, lon)
    result = client.post_raw(OVERPASS_URL, form_data={"data": bbox_query}, user_agent=user_agent)
    elements = result.get("elements", [])
    logger.info("Overpass bbox query returned %d elements", len(elements))
    return elements


def _build_area_query(island_name: str) -> str:
    safe_name = island_name.replace('"', '\\"')
    return f"""[out:json][timeout:60];
area["name"="{safe_name}"]["place"="island"]->.a;
(
{_POI_QUERIES}
);
out center;"""


def _build_bbox_query(lat: float, lon: float, margin: float = 0.15) -> str:
    south = lat - margin
    north = lat + margin
    west = lon - (margin * 1.33)
    east = lon + (margin * 1.33)
    bbox = f"{south:.4f},{west:.4f},{north:.4f},{east:.4f}"
    poi_section = _POI_QUERIES_BBOX.replace("{bbox}", bbox)
    return f"""[out:json][timeout:60];
(
{poi_section}
);
out center;"""


def _parse_elements(elements: list[dict]) -> dict:
    beaches: list[dict] = []
    restaurants: list[dict] = []
    atms: list[dict] = []
    medical: list[dict] = []
    accommodation: list[dict] = []
    landmarks: list[dict] = []

    for el in elements:
        tags = el.get("tags", {})
        if not tags:
            continue
        category = _classify_element(tags)
        if category is None:
            continue

        poi = {
            "name": tags.get("name") or tags.get("name:en") or tags.get("name:hr"),
            "coordinates": _extract_coords(el),
            "osm_tags": {k: v for k, v in tags.items() if k not in ("name", "name:en", "name:hr")},
            "notes": None,
        }

        if category == "beach":
            beaches.append(poi)
        elif category == "restaurant":
            restaurants.append(poi)
        elif category == "atm":
            atms.append(poi)
        elif category == "medical":
            medical.append(poi)
        elif category == "accommodation":
            accommodation.append(poi)
        elif category == "landmark":
            landmarks.append(poi)

    return {
        "beaches": beaches,
        "restaurants": restaurants,
        "atms": atms,
        "medical": medical,
        "accommodation": accommodation,
        "landmarks": landmarks,
    }


def _classify_element(tags: dict) -> str | None:
    amenity = tags.get("amenity", "")
    natural = tags.get("natural", "")
    leisure = tags.get("leisure", "")
    tourism = tags.get("tourism", "")
    historic = tags.get("historic", "")

    if natural == "beach" or leisure == "beach":
        return "beach"
    if amenity in ("restaurant", "cafe", "bar"):
        return "restaurant"
    if amenity == "atm" or (amenity == "bank" and tags.get("atm") == "yes"):
        return "atm"
    if amenity in ("pharmacy", "doctors", "hospital"):
        return "medical"
    if tourism in ("hotel", "guest_house", "apartment", "hostel"):
        return "accommodation"
    if historic and historic not in _LANDMARK_SKIP_HISTORIC:
        return "landmark"
    if tourism == "attraction":
        return "landmark"
    if amenity in ("ferry_terminal", "place_of_worship"):
        return "landmark"
    return None


def _extract_coords(element: dict) -> dict | None:
    if "lat" in element and "lon" in element:
        return {"lat": element["lat"], "lon": element["lon"]}
    if "center" in element:
        return {"lat": element["center"]["lat"], "lon": element["center"]["lon"]}
    return None


def _derive_car_free(elements: list[dict]) -> bool:
    for el in elements:
        if el.get("type") != "way":
            continue
        highway = el.get("tags", {}).get("highway", "")
        if highway in _CAR_HIGHWAY_TAGS:
            return False
    return True


_TERMINAL_STRIP_RE = re.compile(
    r"\b(?:ferry\s+terminal|trajektna\s+luka|trajektno\s+pristanište|"
    r"pristajalište|pristanište|luka|trajekt|port|harbour|harbor)\b",
    re.IGNORECASE,
)


def _find_nearby_islands(
    current_name: str,
    lat: float,
    lon: float,
    client: HttpClient,
    radius_m: int,
    user_agent: str = "island-guide/1.0",
) -> list[str]:
    # Primary: find island areas that contain nearby ferry terminals.
    # is_in returns the enclosing OSM area objects, filtered to place=island.
    # This deduplicates sub-ports (Brbinj+Božava → one "Dugi otok" entry) and
    # excludes mainland ports whose terminals aren't inside any island area.
    primary_query = f"""[out:json][timeout:60];
node["amenity"="ferry_terminal"](around:{radius_m},{lat:.4f},{lon:.4f})->.terminals;
.terminals is_in->.enclosing;
area.enclosing["place"="island"]["name"]->.island_areas;
.island_areas out center tags;"""
    try:
        result = client.post_raw(OVERPASS_URL, form_data={"data": primary_query}, user_agent=user_agent)
        names = _extract_island_names(result.get("elements", []), current_name)
        if names:
            return names
        logger.debug("is_in island query returned 0 results — falling back to terminal names")
    except Exception as exc:
        logger.warning("Nearby islands (is_in) query failed: %s — trying fallback", exc)

    # Fallback: raw terminal names (less clean but better than nothing)
    fallback_query = f"""[out:json][timeout:30];
(
  node["amenity"="ferry_terminal"](around:{radius_m},{lat:.4f},{lon:.4f});
  way["amenity"="ferry_terminal"](around:{radius_m},{lat:.4f},{lon:.4f});
);
out center;"""
    try:
        result = client.post_raw(OVERPASS_URL, form_data={"data": fallback_query}, user_agent=user_agent)
        seen: set[str] = set()
        names_fb: list[str] = []
        current_lower = current_name.lower()
        for el in result.get("elements", []):
            raw = el.get("tags", {}).get("name") or el.get("tags", {}).get("name:en")
            if not raw:
                continue
            name = _normalise_terminal_name(raw)
            if not name or name.lower() == current_lower:
                continue
            if name not in seen:
                seen.add(name)
                names_fb.append(name)
        return sorted(names_fb)[:10]
    except Exception as exc:
        logger.warning("Nearby islands fallback query failed: %s", exc)
        return []


def _extract_island_names(elements: list[dict], current_name: str) -> list[str]:
    current_lower = current_name.lower()
    seen: set[str] = set()
    names: list[str] = []
    for el in elements:
        name = el.get("tags", {}).get("name") or el.get("tags", {}).get("name:en")
        if not name or name.lower() == current_lower:
            continue
        if name not in seen:
            seen.add(name)
            names.append(name)
    return sorted(names)[:10]


def _normalise_terminal_name(raw: str) -> str:
    # "Zadar (Gaženica)" → "Zadar (Gaženica)" — keep parenthetical for clarity
    # "Silba ferry terminal" → "Silba"
    name = _TERMINAL_STRIP_RE.sub("", raw).strip(" -–,")
    return name if name else raw.strip()
