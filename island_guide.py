#!/usr/bin/env python3
"""Croatian Island Guide Generator — aggregates Wikipedia, OSM, weather, and ferry data."""

from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

CONFIG = {
    "RATE_LIMIT_SECONDS": 1.0,
    "RETRY_ATTEMPTS": 3,
    "RETRY_BACKOFF_BASE": 2.0,
    "REQUEST_TIMEOUT": 30,
    "CACHE_TTL_HOURS": 24,
    "CACHE_DIR": ".cache",
    "DATA_DIR": "data",
    "GUIDES_DIR": "guides",
    "USER_AGENTS": [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15",
        "Mozilla/5.0 (X11; Linux x86_64; rv:121.0) Gecko/20100101 Firefox/121.0",
    ],
    "WEATHER_YEARS_BACK": 5,
    "PEAK_SEASON_TEMP_THRESHOLD": 24.0,
    "PEAK_SEASON_RAIN_THRESHOLD": 5,
    "NEARBY_ISLANDS_RADIUS_M": 50000,
    # Overpass rejects browser user-agents (Apache mod_security).
    # Use a neutral scraper UA for direct API calls.
    "OVERPASS_USER_AGENT": "island-guide/1.0",
}

from core.cache import CacheManager
from core.guide_writer import render_guide
from core.http_client import HttpClient
from core.models import (
    Coordinates,
    DataQuality,
    FerryRoute,
    GettingThere,
    IslandGuide,
    IslandIdentity,
    IslandMeta,
    MonthlyWeather,
    PointOfInterest,
    PointsOfInterest,
    PracticalInfo,
    ScraperResult,
    WeatherSummary,
)
from scrapers import jadrolinija, overpass, weather, wikipedia


def slugify(name: str) -> str:
    name = name.lower().strip()
    name = re.sub(r"[čć]", "c", name)
    name = re.sub(r"[šś]", "s", name)
    name = re.sub(r"[žź]", "z", name)
    name = re.sub(r"đ", "d", name)
    name = re.sub(r"[^a-z0-9]+", "-", name)
    return name.strip("-")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate a tourist guide for any Croatian island.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python island_guide.py --island "Silba"
  python island_guide.py --island "Hvar" --refresh
  python island_guide.py --island "Vis" --json-only
  python island_guide.py --island "Korčula" --verbose
        """,
    )
    parser.add_argument("--island", required=True, help="Island name (e.g. 'Silba', 'Hvar')")
    parser.add_argument("--refresh", action="store_true", help="Bypass cache and re-fetch all data")
    parser.add_argument("--json-only", action="store_true", help="Write JSON only, skip markdown guide")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose logging")
    parser.add_argument("--output-dir", default=None, help="Override output directory for guides")
    return parser.parse_args()


def setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def _cached_scrape(
    cache: CacheManager,
    key: str,
    ttl_seconds: int,
    refresh: bool,
    scrape_fn,
    *args,
) -> ScraperResult:
    if not refresh:
        cached = cache.get(key, ttl_seconds)
        if cached is not None:
            logging.getLogger(__name__).info("[CACHE] %s: using cached data", key)
            return ScraperResult(success=True, data=cached, source=key)

    result = scrape_fn(*args)
    if result.success and result.data is not None:
        cache.set(key, result.data)
    return result


def assemble_guide(
    island_name: str,
    slug: str,
    wiki_result: ScraperResult,
    osm_result: ScraperResult,
    weather_result: ScraperResult,
    ferry_result: ScraperResult,
) -> IslandGuide:
    wiki = wiki_result.data or {}
    osm = osm_result.data or {}
    wx = weather_result.data or {}
    ferry = ferry_result.data or []

    # Identity
    coords_raw = wiki.get("coordinates")
    coords = Coordinates(lat=coords_raw["lat"], lon=coords_raw["lon"]) if coords_raw else None
    identity = IslandIdentity(
        name=wiki.get("name") or island_name,
        name_hr=wiki.get("name_hr") or island_name,
        region=wiki.get("region"),
        coordinates=coords,
        area_km2=wiki.get("area_km2"),
        population=wiki.get("population"),
        car_free=osm.get("car_free"),
        description=wiki.get("extract") or wiki.get("description"),
        best_for=[],
    )

    # Ferry routes
    ferry_routes = []
    for r in ferry:
        ferry_routes.append(FerryRoute(
            origin=r.get("origin", "Unknown"),
            destination=r.get("destination", island_name),
            operator=r.get("operator"),
            duration_minutes=r.get("duration_minutes"),
            frequency_peak=r.get("frequency_peak"),
            frequency_low=r.get("frequency_low"),
            bikes_allowed=r.get("bikes_allowed"),
            cars_allowed=r.get("cars_allowed"),
            booking_url=r.get("booking_url"),
        ))

    # Weather
    monthly_entries = []
    for m in wx.get("monthly", []):
        monthly_entries.append(MonthlyWeather(
            month=m["month"],
            month_name=m["month_name"],
            avg_temp_c=m.get("avg_temp_c"),
            avg_rain_days=m.get("avg_rain_days"),
            avg_sunshine_hours=m.get("avg_sunshine_hours"),
            sea_temp_c=m.get("sea_temp_c"),
            peak_season=m.get("peak_season", False),
        ))
    weather_summary = WeatherSummary(
        monthly=monthly_entries,
        best_months=wx.get("best_months", []),
        peak_season_months=wx.get("peak_season_months", []),
    )

    # POIs
    def _make_pois(raw_list: list[dict]) -> list[PointOfInterest]:
        result = []
        for item in raw_list:
            c = item.get("coordinates")
            result.append(PointOfInterest(
                name=item.get("name"),
                coordinates=Coordinates(lat=c["lat"], lon=c["lon"]) if c else None,
                osm_tags=item.get("osm_tags", {}),
                notes=item.get("notes"),
            ))
        return result

    pois = PointsOfInterest(
        beaches=_make_pois(osm.get("beaches", [])),
        restaurants=_make_pois(osm.get("restaurants", [])),
        atms=_make_pois(osm.get("atms", [])),
        medical=_make_pois(osm.get("medical", [])),
        accommodation=_make_pois(osm.get("accommodation", [])),
        landmarks=_make_pois(osm.get("landmarks", [])),
    )

    practical = derive_practical(pois, ferry_routes, identity.area_km2, osm)

    osm_poi_count = sum([
        len(pois.beaches), len(pois.restaurants), len(pois.atms),
        len(pois.medical), len(pois.accommodation), len(pois.landmarks),
    ])

    warnings = []
    if not wiki_result.success:
        warnings.append(f"Wikipedia: {wiki_result.error}")
    if not osm_result.success:
        warnings.append(f"OSM: {osm_result.error}")
    if not weather_result.success:
        warnings.append(f"Weather: {weather_result.error}")
    if not ferry_result.success:
        warnings.append(f"Ferry: {ferry_result.error}")

    meta = IslandMeta(
        island=island_name,
        slug=slug,
        generated_at=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        data_quality=DataQuality(
            osm_poi_count=osm_poi_count,
            ferry_routes_found=len(ferry_routes),
            weather_months_complete=sum(1 for m in monthly_entries if m.avg_temp_c is not None),
            coverage_warning="; ".join(warnings) if warnings else None,
        ),
    )

    return IslandGuide(
        meta=meta,
        identity=identity,
        getting_there=GettingThere(ferry_routes=ferry_routes),
        weather=weather_summary,
        points_of_interest=pois,
        practical=practical,
    )


def derive_practical(
    pois: PointsOfInterest,
    ferry_routes: list[FerryRoute],
    area_km2: float | None,
    osm: dict,
) -> PracticalInfo:
    atm_count = len(pois.atms)
    atm_available = atm_count > 0
    medical_facility = len(pois.medical) > 0
    cash_only_warning = atm_available and atm_count == 1

    nearby = osm.get("nearby_islands", [])
    day_trip_viable = bool(nearby) or any(
        r.duration_minutes is not None and r.duration_minutes <= 180
        for r in ferry_routes
    )

    # Recommended stay heuristic
    a = area_km2 or 0
    if a < 5 or (sum([len(pois.beaches), len(pois.restaurants), len(pois.landmarks)]) < 3):
        nights = 1
    elif a < 20:
        nights = 2
    elif a < 50:
        nights = 3
    elif a < 150:
        nights = 4
    else:
        nights = 7

    return PracticalInfo(
        atm_available=atm_available,
        atm_count=atm_count,
        medical_facility=medical_facility,
        cash_only_warning=cash_only_warning,
        day_trip_viable=day_trip_viable,
        nearby_islands=nearby,
        recommended_stay_nights=nights,
    )


def print_quality_summary(guide: IslandGuide, results: dict[str, ScraperResult]) -> None:
    q = guide.meta.data_quality
    print()
    print("=" * 56)
    print(f"  {guide.identity.name} — Data Quality Summary")
    print("=" * 56)
    for source_key, label in [("wiki", "Wikipedia"), ("osm", "OpenStreetMap"), ("weather", "Open-Meteo"), ("ferry", "Jadrolinija")]:
        r = results.get(source_key)
        if r is None:
            continue
        status = "✓" if r.success else "✗"
        cov = f"  (coverage: {r.coverage_pct:.0f}%)" if r.coverage_pct is not None else ""
        err = f"  [{r.error}]" if r.error else ""
        print(f"  {status} {label:<18}{cov}{err}")
    print("-" * 56)
    print(f"  POIs found:          {q.osm_poi_count}")
    print(f"  Ferry routes:        {q.ferry_routes_found}")
    print(f"  Weather months:      {q.weather_months_complete}/12")
    if q.coverage_warning:
        print(f"  Warnings:            {q.coverage_warning}")
    print("=" * 56)


def main() -> None:
    args = parse_args()
    setup_logging(args.verbose)
    log = logging.getLogger(__name__)

    island_name = args.island.strip()
    slug = slugify(island_name)
    ttl = int(CONFIG["CACHE_TTL_HOURS"] * 3600)

    log.info("Generating guide for: %s (slug: %s)", island_name, slug)

    client = HttpClient(CONFIG)
    cache = CacheManager(CONFIG["CACHE_DIR"], slug)

    if args.refresh:
        log.info("--refresh: invalidating cache")
        cache.invalidate()

    # --- Wikipedia (must come first — provides coordinates) ---
    log.info("Fetching Wikipedia data...")
    wiki_result = _cached_scrape(
        cache, "wiki", ttl, args.refresh,
        wikipedia.scrape_wikipedia, island_name, client,
    )
    if wiki_result.success:
        log.info("Wikipedia: OK (coverage %.0f%%)", wiki_result.coverage_pct or 0)
    else:
        log.warning("Wikipedia: FAILED — %s", wiki_result.error)

    coords = None
    if wiki_result.success and wiki_result.data:
        c = wiki_result.data.get("coordinates")
        if c:
            coords = (c["lat"], c["lon"])

    # --- OpenStreetMap ---
    log.info("Fetching OpenStreetMap POI data...")
    lat = coords[0] if coords else 44.0
    lon = coords[1] if coords else 15.0
    osm_result = _cached_scrape(
        cache, "osm", ttl, args.refresh,
        overpass.scrape_overpass, island_name, lat, lon, client,
        CONFIG["NEARBY_ISLANDS_RADIUS_M"], CONFIG["OVERPASS_USER_AGENT"],
    )
    if osm_result.success:
        d = osm_result.data or {}
        poi_count = sum(len(d.get(k, [])) for k in ("beaches", "restaurants", "atms", "medical", "accommodation", "landmarks"))
        log.info("OSM: OK — %d POIs found", poi_count)
    else:
        log.warning("OSM: FAILED — %s", osm_result.error)

    # --- Weather ---
    log.info("Fetching climate data from Open-Meteo...")
    wx_result = _cached_scrape(
        cache, "weather", ttl, args.refresh,
        weather.scrape_weather, lat, lon, client,
        CONFIG["WEATHER_YEARS_BACK"],
        CONFIG["PEAK_SEASON_TEMP_THRESHOLD"],
        CONFIG["PEAK_SEASON_RAIN_THRESHOLD"],
    )
    if wx_result.success:
        log.info("Weather: OK (coverage %.0f%%)", wx_result.coverage_pct or 0)
    else:
        log.warning("Weather: FAILED — %s", wx_result.error)

    # --- Ferry ---
    log.info("Fetching ferry schedule data...")
    ferry_result = _cached_scrape(
        cache, "ferry", ttl, args.refresh,
        jadrolinija.scrape_jadrolinija, island_name, client,
    )
    if ferry_result.success:
        route_count = len(ferry_result.data or [])
        log.info("Ferry: OK — %d routes found (source: %s)", route_count, ferry_result.source)
    else:
        log.warning("Ferry: FAILED — %s", ferry_result.error)

    # --- Assemble ---
    log.info("Assembling guide...")
    guide = assemble_guide(island_name, slug, wiki_result, osm_result, wx_result, ferry_result)

    # --- Write JSON ---
    data_dir = Path(args.output_dir or CONFIG["DATA_DIR"])
    data_dir.mkdir(parents=True, exist_ok=True)
    json_path = data_dir / f"{slug}.json"
    json_path.write_text(
        json.dumps(dataclasses.asdict(guide), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    log.info("JSON written: %s", json_path)

    # --- Write Markdown ---
    if not args.json_only:
        guides_dir = Path(args.output_dir or CONFIG["GUIDES_DIR"])
        guides_dir.mkdir(parents=True, exist_ok=True)
        md_path = guides_dir / f"{slug}.md"
        md_path.write_text(render_guide(guide), encoding="utf-8")
        log.info("Guide written: %s", md_path)
        print(f"\nGuide generated: {md_path}")
    else:
        print(f"\nJSON generated: {json_path}")

    results = {
        "wiki": wiki_result,
        "osm": osm_result,
        "weather": wx_result,
        "ferry": ferry_result,
    }
    print_quality_summary(guide, results)


if __name__ == "__main__":
    main()
