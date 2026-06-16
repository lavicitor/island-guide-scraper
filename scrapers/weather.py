from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, timedelta

from core.http_client import HttpClient
from core.models import ScraperResult

logger = logging.getLogger(__name__)

ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
MARINE_URL = "https://marine-api.open-meteo.com/v1/marine"

MONTH_NAMES = [
    "", "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]


def scrape_weather(
    lat: float,
    lon: float,
    client: HttpClient,
    years_back: int = 5,
    peak_temp_threshold: float = 24.0,
    peak_rain_threshold: int = 5,
) -> ScraperResult:
    try:
        end_date = date.today() - timedelta(days=5)
        start_date = end_date.replace(year=end_date.year - years_back)

        climate_data = _fetch_climate(lat, lon, client, start_date, end_date)
        sea_data = _fetch_sea_temp(lat, lon, client, start_date, end_date)

        monthly = _aggregate_to_monthly(climate_data, start_date, end_date)
        monthly = _merge_sea_temps(monthly, sea_data)

        for m in monthly:
            temp = m.get("avg_temp_c")
            rain = m.get("avg_rain_days")
            if temp is not None and rain is not None:
                m["peak_season"] = temp > peak_temp_threshold and rain < peak_rain_threshold

        best_months = _derive_best_months(monthly)
        peak_months = [MONTH_NAMES[m["month"]] for m in monthly if m.get("peak_season")]

        complete = sum(1 for m in monthly if m.get("avg_temp_c") is not None)
        coverage = round(complete / 12 * 100, 1)

        return ScraperResult(
            success=True,
            data={"monthly": monthly, "best_months": best_months, "peak_season_months": peak_months},
            source="open-meteo",
            coverage_pct=coverage,
        )
    except Exception as exc:
        logger.error("Weather scraper failed: %s", exc)
        return ScraperResult(success=False, data=None, source="open-meteo", error=str(exc))


def _fetch_climate(
    lat: float, lon: float, client: HttpClient, start_date: date, end_date: date
) -> dict:
    params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "daily": "temperature_2m_mean,precipitation_sum,sunshine_duration",
        "timezone": "Europe/Zagreb",
    }
    return client.get(ARCHIVE_URL, params=params)


def _fetch_sea_temp(
    lat: float, lon: float, client: HttpClient, start_date: date, end_date: date
) -> dict | None:
    params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "hourly": "sea_surface_temperature",
        "timezone": "Europe/Zagreb",
    }
    try:
        data = client.get(MARINE_URL, params=params)
        if "hourly" not in data or "sea_surface_temperature" not in data.get("hourly", {}):
            return None
        return data
    except Exception as exc:
        logger.info("Marine sea temp fetch failed (non-fatal): %s", exc)
        return None


def _aggregate_to_monthly(climate: dict, start_date: date, end_date: date) -> list[dict]:
    daily = climate.get("daily", {})
    times = daily.get("time", [])
    temps = daily.get("temperature_2m_mean", [])
    precips = daily.get("precipitation_sum", [])
    sunshines = daily.get("sunshine_duration", [])

    # Bucket daily values by calendar month (1–12), accumulate across all years
    month_temps: dict[int, list[float]] = defaultdict(list)
    month_rain_days: dict[int, list[int]] = defaultdict(list)  # per-year rain day count
    month_sunshine: dict[int, list[float]] = defaultdict(list)

    # Track rain days per (year, month) then average across years
    year_month_rain: dict[tuple[int, int], int] = defaultdict(int)
    year_month_days: dict[tuple[int, int], int] = defaultdict(int)

    for i, t in enumerate(times):
        try:
            y, m, _ = t.split("-")
            y, m = int(y), int(m)
        except (ValueError, AttributeError):
            continue

        temp = temps[i] if i < len(temps) else None
        precip = precips[i] if i < len(precips) else None
        sunshine = sunshines[i] if i < len(sunshines) else None

        if temp is not None:
            month_temps[m].append(float(temp))
        if precip is not None:
            year_month_days[(y, m)] += 1
            if float(precip) >= 1.0:
                year_month_rain[(y, m)] += 1
        if sunshine is not None:
            month_sunshine[m].append(float(sunshine) / 3600.0)

    # Average rain days per month across years
    month_avg_rain: dict[int, float] = {}
    for month_num in range(1, 13):
        relevant_years = {y for (y, mn) in year_month_days if mn == month_num}
        if relevant_years:
            total_rain_days = sum(year_month_rain.get((y, month_num), 0) for y in relevant_years)
            month_avg_rain[month_num] = total_rain_days / len(relevant_years)

    result = []
    for month_num in range(1, 13):
        avg_temp = round(sum(month_temps[month_num]) / len(month_temps[month_num]), 1) \
            if month_temps[month_num] else None
        avg_rain = round(month_avg_rain.get(month_num, 0), 1) if month_num in month_avg_rain else None
        avg_sun = round(sum(month_sunshine[month_num]) / len(month_sunshine[month_num]), 1) \
            if month_sunshine[month_num] else None

        result.append({
            "month": month_num,
            "month_name": MONTH_NAMES[month_num],
            "avg_temp_c": avg_temp,
            "avg_rain_days": avg_rain,
            "avg_sunshine_hours": avg_sun,
            "sea_temp_c": None,
            "peak_season": False,
        })
    return result


def _merge_sea_temps(monthly: list[dict], sea_data: dict | None) -> list[dict]:
    if sea_data is None:
        return monthly

    hourly = sea_data.get("hourly", {})
    times = hourly.get("time", [])
    temps = hourly.get("sea_surface_temperature", [])

    month_sea: dict[int, list[float]] = defaultdict(list)
    for i, t in enumerate(times):
        try:
            m = int(t[5:7])
        except (ValueError, IndexError):
            continue
        val = temps[i] if i < len(temps) else None
        if val is not None:
            month_sea[m].append(float(val))

    for entry in monthly:
        m = entry["month"]
        if month_sea[m]:
            entry["sea_temp_c"] = round(sum(month_sea[m]) / len(month_sea[m]), 1)
    return monthly


def _derive_best_months(monthly: list[dict]) -> list[str]:
    best = [
        MONTH_NAMES[m["month"]]
        for m in monthly
        if (
            m.get("avg_temp_c") is not None
            and m["avg_temp_c"] >= 20.0
            and (m.get("avg_rain_days") or 0) <= 6
            and not m.get("peak_season", False)
        )
    ]
    if not best:
        # Lower threshold
        best = [
            MONTH_NAMES[m["month"]]
            for m in monthly
            if (
                m.get("avg_temp_c") is not None
                and m["avg_temp_c"] >= 18.0
                and not m.get("peak_season", False)
            )
        ]
    return best
