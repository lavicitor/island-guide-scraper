from __future__ import annotations

import logging
import re

from bs4 import BeautifulSoup

from core.http_client import HttpClient
from core.models import ScraperResult

logger = logging.getLogger(__name__)

JADROLINIJA_BASE = "https://www.jadrolinija.hr"
JADROLINIJA_TRAVELS = "https://www.jadrolinija.hr/en/travels"
KRILO_BASE = "https://www.krilo.hr"
KRILO_ROUTES = "https://www.krilo.hr/en/"
DIRECTFERRIES_URL = "https://www.directferries.co.uk/croatia_ferries.htm"

_DURATION_RE = re.compile(r"(\d+)\s*h(?:r|our)?s?\s*(?:(\d+)\s*m(?:in)?)?", re.IGNORECASE)
_FREQ_RE = re.compile(r"(\d+)\s*(?:x|times?|sailings?)?(?:\s*(?:per|a)\s*(?:week|day))?", re.IGNORECASE)
_CARS_YES_RE = re.compile(r"\b(?:vehicle|car ferry|ro[- ]ro|passenger and car|cars?\s+(?:and|&)\s+bikes?)\b", re.IGNORECASE)
_CARS_NO_RE = re.compile(r"\b(?:catamaran|fast\s+ferry|passenger[- ]only|no\s+vehicles?|no\s+cars?)\b", re.IGNORECASE)
_BIKES_NO_RE = re.compile(r"\b(?:no\s+bicycles?|no\s+bikes?|bicycles?\s+not\s+(?:allowed|permitted))\b", re.IGNORECASE)


def scrape_jadrolinija(island_name: str, client: HttpClient) -> ScraperResult:
    all_routes: list[dict] = []

    # Tier 1: Jadrolinija state ferries
    if client.can_fetch(JADROLINIJA_BASE, "/en/travels"):
        routes = _scrape_jadrolinija_html(island_name, client)
        if routes:
            all_routes.extend(routes)
    else:
        logger.warning("Jadrolinija robots.txt disallows scraping /en/travels")

    # Tier 2: Krilo fast catamarans
    krilo_routes = _scrape_krilo(island_name, client)
    if krilo_routes:
        all_routes.extend(krilo_routes)

    # Tier 3: directferries fallback if nothing found
    if not all_routes:
        logger.info("No routes from primary sources — trying directferries.com fallback")
        df_routes = _scrape_directferries(island_name, client)
        if df_routes:
            all_routes.extend(df_routes)

    if not all_routes:
        return ScraperResult(
            success=False,
            data=None,
            source="jadrolinija",
            error="No ferry data found from any source",
        )

    # Dedup by (origin, destination), preferring earlier (Jadrolinija) entries
    seen: set[tuple[str, str]] = set()
    deduped: list[dict] = []
    for r in all_routes:
        key = (r.get("origin", "").lower(), r.get("destination", "").lower())
        if key not in seen:
            seen.add(key)
            deduped.append(r)

    coverage = min(len(deduped) / 2.0, 1.0) * 100
    return ScraperResult(
        success=True,
        data=deduped,
        source="jadrolinija",
        coverage_pct=round(coverage, 1),
    )


def _scrape_jadrolinija_html(island_name: str, client: HttpClient) -> list[dict] | None:
    try:
        html = client.get_text(JADROLINIJA_TRAVELS)
    except Exception as exc:
        logger.warning("Jadrolinija HTML fetch failed: %s", exc)
        return None

    try:
        soup = BeautifulSoup(html, "lxml")
        routes = _parse_jadrolinija_travels(soup, island_name, client)
        if routes:
            logger.info("Found %d Jadrolinija routes for '%s'", len(routes), island_name)
            return routes
        return None
    except Exception as exc:
        logger.warning("Jadrolinija HTML parse failed: %s", exc)
        return None


def _parse_jadrolinija_travels(soup: BeautifulSoup, island_name: str, client: HttpClient) -> list[dict]:
    """Parse the /en/travels listing page; fetch each matching route's detail page."""
    routes = []
    island_lower = island_name.lower()

    for link in soup.find_all("a", class_="table__link", href=True):
        text = link.get_text(strip=True)
        if island_lower not in text.lower():
            continue
        href = link["href"]
        booking_url = href if href.startswith("http") else JADROLINIJA_BASE + href
        route = _build_route_from_detail(text, booking_url, island_name, client)
        if route:
            routes.append(route)

    return routes


def _build_route_from_detail(title: str, booking_url: str, island_name: str, client: HttpClient) -> dict | None:
    """Fetch the route detail page and enrich the basic listing data."""
    clean = re.sub(r"^\d+\s*", "", title).strip()
    ports = [p.strip() for p in clean.split(" - ")]
    if not ports:
        return None
    origin = ports[0]

    bikes_allowed: bool | None = None
    cars_allowed: bool | None = None
    duration: int | None = None

    try:
        detail_html = client.get_text(booking_url)
        detail_soup = BeautifulSoup(detail_html, "lxml")
        page_text = detail_soup.get_text(separator=" ", strip=True)

        duration = _parse_duration(page_text)
        cars_allowed = _parse_cars_allowed(page_text)
        bikes_allowed = _parse_bikes_allowed(page_text, cars_allowed)
    except Exception as exc:
        logger.debug("Jadrolinija route detail fetch failed for %s: %s", booking_url, exc)

    return {
        "origin": origin,
        "destination": island_name,
        "operator": "Jadrolinija",
        "duration_minutes": duration,
        "frequency_peak": None,
        "frequency_low": None,
        "bikes_allowed": bikes_allowed,
        "cars_allowed": cars_allowed,
        "booking_url": booking_url,
    }


def _parse_cars_allowed(text: str) -> bool | None:
    if _CARS_NO_RE.search(text):
        return False
    if _CARS_YES_RE.search(text):
        return True
    return None


def _parse_bikes_allowed(text: str, cars_allowed: bool | None) -> bool | None:
    if _BIKES_NO_RE.search(text):
        return False
    # Catamarans that explicitly say no vehicles typically don't take bikes either
    if cars_allowed is False and _CARS_NO_RE.search(text):
        return False
    # State ferries accept bikes by default unless explicitly stated otherwise
    return True


def _scrape_krilo(island_name: str, client: HttpClient) -> list[dict] | None:
    """Scrape Krilo Kapetan Luka fast catamaran routes."""
    if not client.can_fetch(KRILO_BASE, "/en/"):
        logger.debug("Krilo robots.txt disallows scraping")
        return None
    try:
        html = client.get_text(KRILO_ROUTES)
        soup = BeautifulSoup(html, "lxml")
        return _parse_krilo_routes(soup, island_name)
    except Exception as exc:
        logger.debug("Krilo scrape failed: %s", exc)
        return None


def _parse_krilo_routes(soup: BeautifulSoup, island_name: str) -> list[dict] | None:
    island_lower = island_name.lower()
    routes = []

    # Krilo lists routes as links or table rows containing port names
    for link in soup.find_all("a", href=True):
        text = link.get_text(strip=True)
        if island_lower not in text.lower():
            continue
        href = link["href"]
        booking_url = href if href.startswith("http") else KRILO_BASE + href
        parts = [p.strip() for p in re.split(r"\s*[-–]\s*", text)]
        if len(parts) < 2:
            continue
        origin = parts[0]
        if not origin:
            continue
        routes.append({
            "origin": origin,
            "destination": island_name,
            "operator": "Krilo Kapetan Luka",
            "duration_minutes": None,
            "frequency_peak": None,
            "frequency_low": None,
            "bikes_allowed": False,
            "cars_allowed": False,
            "booking_url": booking_url,
        })

    return routes if routes else None


def _scrape_directferries(island_name: str, client: HttpClient) -> list[dict] | None:
    try:
        html = client.get_text(DIRECTFERRIES_URL)
    except Exception as exc:
        logger.warning("directferries.com fetch failed: %s", exc)
        return None

    try:
        soup = BeautifulSoup(html, "lxml")
        return _parse_directferries_routes(soup, island_name)
    except Exception as exc:
        logger.warning("directferries.com parse failed: %s", exc)
        return None


def _parse_directferries_routes(soup: BeautifulSoup, island_name: str) -> list[dict] | None:
    island_lower = island_name.lower()
    routes = []

    for row in soup.find_all(["tr", "li"]):
        text = row.get_text(separator=" ", strip=True)
        if island_lower not in text.lower():
            continue
        link = row.find("a", href=True)
        booking_url = link["href"] if link else DIRECTFERRIES_URL
        if not booking_url.startswith("http"):
            booking_url = "https://www.directferries.co.uk" + booking_url

        parts = text.split("to")
        if len(parts) >= 2:
            origin = parts[0].strip()
            destination_part = parts[1].strip().split()[0] if parts[1].strip() else island_name
        else:
            origin = "Split"
            destination_part = island_name

        duration = _parse_duration(text)
        routes.append({
            "origin": origin,
            "destination": destination_part,
            "operator": "Various",
            "duration_minutes": duration,
            "frequency_peak": None,
            "frequency_low": None,
            "bikes_allowed": None,
            "cars_allowed": None,
            "booking_url": booking_url,
        })

    return routes if routes else None


def _parse_duration(text: str) -> int | None:
    m = _DURATION_RE.search(text)
    if not m:
        return None
    hours = int(m.group(1))
    minutes = int(m.group(2)) if m.group(2) else 0
    total = hours * 60 + minutes
    return total if total > 0 else None
