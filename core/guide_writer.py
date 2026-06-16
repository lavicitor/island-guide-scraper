from __future__ import annotations

from core.models import IslandGuide


def render_guide(guide: IslandGuide) -> str:
    sections = [
        _render_header(guide),
        _render_intro(guide),
        _render_getting_there(guide),
        _render_weather(guide),
        _render_beaches(guide),
        _render_food(guide),
        _render_practical(guide),
        _render_nearby(guide),
        _render_footer(guide),
    ]
    return "\n\n".join(s for s in sections if s)


def _render_header(guide: IslandGuide) -> str:
    return f"# {guide.identity.name} — Complete Island Guide"


def _render_intro(guide: IslandGuide) -> str:
    parts = []
    if guide.identity.car_free:
        parts.append("Car-free island")
    if guide.identity.region:
        parts.append(guide.identity.region)
    if guide.identity.area_km2 is not None:
        parts.append(f"{guide.identity.area_km2:g} km²")
    if guide.identity.population is not None:
        parts.append(f"~{guide.identity.population:,} residents")

    lines = []
    if parts:
        lines.append("> " + " · ".join(parts))
    if guide.identity.description:
        lines.append("")
        lines.append(guide.identity.description)
    if guide.identity.best_for:
        lines.append("")
        lines.append("**Best for:** " + ", ".join(guide.identity.best_for))
    return "\n".join(lines) if lines else ""


def _render_getting_there(guide: IslandGuide) -> str:
    routes = guide.getting_there.ferry_routes
    if not routes:
        return ""

    lines = ["## Getting There", ""]
    header = "| From | Operator | Duration | Peak (sailings/wk) | Low (sailings/wk) | Bikes | Cars | Book |"
    sep =    "|------|----------|----------|--------------------|-------------------|-------|------|------|"
    lines += [header, sep]

    for r in routes:
        dur = f"{r.duration_minutes // 60}h {r.duration_minutes % 60:02d}m" if r.duration_minutes else "—"
        peak = str(r.frequency_peak) if r.frequency_peak is not None else "—"
        low = str(r.frequency_low) if r.frequency_low is not None else "—"
        bikes = "✓" if r.bikes_allowed else ("✗" if r.bikes_allowed is False else "?")
        cars = "✓" if r.cars_allowed else ("✗" if r.cars_allowed is False else "?")
        operator = r.operator or "—"
        origin = r.origin or "—"
        book = f"[Book]({r.booking_url})" if r.booking_url else "—"
        lines.append(f"| {origin} | {operator} | {dur} | {peak} | {low} | {bikes} | {cars} | {book} |")

    return "\n".join(lines)


def _render_weather(guide: IslandGuide) -> str:
    monthly = guide.weather.monthly
    if not monthly:
        return ""

    lines = ["## Best Time to Visit", ""]
    if guide.weather.best_months:
        lines.append("**Best months:** " + ", ".join(guide.weather.best_months))
    if guide.weather.peak_season_months:
        lines.append("**Peak season (busy + hot):** " + ", ".join(guide.weather.peak_season_months))
    lines.append("")

    header = "| Month | Avg Temp | Rain Days | Sunshine (h/day) | Sea Temp |"
    sep =    "|-------|----------|-----------|------------------|----------|"
    lines += [header, sep]

    for m in monthly:
        name = m.month_name
        if m.peak_season:
            name = f"**{name}**"
        temp = f"{m.avg_temp_c:.1f}°C" if m.avg_temp_c is not None else "—"
        rain = f"{m.avg_rain_days:.0f}" if m.avg_rain_days is not None else "—"
        sun = f"{m.avg_sunshine_hours:.1f}" if m.avg_sunshine_hours is not None else "—"
        sea = f"{m.sea_temp_c:.1f}°C" if m.sea_temp_c is not None else "—"
        lines.append(f"| {name} | {temp} | {rain} | {sun} | {sea} |")

    return "\n".join(lines)


def _render_beaches(guide: IslandGuide) -> str:
    beaches = guide.points_of_interest.beaches
    if not beaches:
        return ""

    lines = ["## Beaches", ""]
    for b in beaches:
        name = b.name or "Unnamed beach"
        coords_str = ""
        if b.coordinates:
            coords_str = f" ({b.coordinates.lat:.4f}, {b.coordinates.lon:.4f})"
        notes_str = f" — {b.notes}" if b.notes else ""
        lines.append(f"- **{name}**{coords_str}{notes_str}")

    return "\n".join(lines)


def _render_food(guide: IslandGuide) -> str:
    restaurants = guide.points_of_interest.restaurants
    if not restaurants:
        return ""

    lines = ["## Food & Drink", ""]
    for r in restaurants:
        name = r.name or "Unnamed"
        kind = r.osm_tags.get("amenity", "restaurant").capitalize()
        cuisine = r.osm_tags.get("cuisine", "")
        cuisine_str = f" · {cuisine}" if cuisine else ""
        lines.append(f"- **{name}** ({kind}{cuisine_str})")

    return "\n".join(lines)


def _render_practical(guide: IslandGuide) -> str:
    p = guide.practical
    pois = guide.points_of_interest
    lines = ["## Practical Information", ""]

    # ATM
    if p.atm_available:
        atm_names = [a.name for a in pois.atms if a.name]
        if atm_names:
            lines.append(f"- **ATM:** {p.atm_count} available ({', '.join(atm_names)})")
        else:
            lines.append(f"- **ATM:** {p.atm_count} available")
        if p.cash_only_warning:
            lines.append("  - ⚠ Only one ATM — bring cash as backup")
    else:
        lines.append("- **ATM:** None found — bring sufficient cash")

    # Medical
    if p.medical_facility:
        med_names = [m.name for m in pois.medical if m.name]
        if med_names:
            lines.append(f"- **Medical:** {', '.join(med_names)}")
        else:
            lines.append("- **Medical:** Facility present")
    else:
        lines.append("- **Medical:** No facility found — nearest mainland hospital")

    # Accommodation
    if pois.accommodation:
        lines.append(f"- **Accommodation:** {len(pois.accommodation)} options found (hotels, guesthouses, apartments)")

    # Stay duration
    if p.recommended_stay_nights is not None:
        if p.recommended_stay_nights <= 1:
            lines.append(f"- **Recommended stay:** Day trip or 1 night")
        else:
            lines.append(f"- **Recommended stay:** {p.recommended_stay_nights} nights")

    # Day trip
    if p.day_trip_viable:
        lines.append("- **Day trip:** Viable by ferry from the mainland")

    return "\n".join(lines)


def _render_nearby(guide: IslandGuide) -> str:
    nearby = guide.practical.nearby_islands
    if not nearby:
        return ""

    lines = ["## Nearby Islands", ""]
    for island in nearby:
        lines.append(f"- {island}")

    return "\n".join(lines)


def _render_footer(guide: IslandGuide) -> str:
    q = guide.meta.data_quality
    warnings = []
    if q.osm_poi_count == 0:
        warnings.append("OSM coverage sparse")
    if q.ferry_routes_found == 0:
        warnings.append("ferry data unavailable")
    if q.weather_months_complete < 12:
        warnings.append(f"weather data incomplete ({q.weather_months_complete}/12 months)")
    coverage_note = " · ".join(warnings) if warnings else "all sources complete"

    return (
        "---\n"
        "*Data sourced from OpenStreetMap, Open-Meteo, Jadrolinija, and Wikipedia. "
        f"Generated: {guide.meta.generated_at}. "
        f"Coverage: {coverage_note}.*"
    )
