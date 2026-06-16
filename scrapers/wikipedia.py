from __future__ import annotations

import logging
import re
from typing import Any

from core.http_client import HttpClient
from core.models import ScraperResult

logger = logging.getLogger(__name__)

_WIKI_SUMMARY = "https://en.wikipedia.org/api/rest_v1/page/summary/{}"
_WIKI_SUMMARY_HR = "https://hr.wikipedia.org/api/rest_v1/page/summary/{}"
_WIKIDATA_ENTITY = "https://www.wikidata.org/wiki/Special:EntityData/{}.json"
_WIKIDATA_LABELS = "https://www.wikidata.org/w/api.php?action=wbgetentities&ids={}&props=labels&languages=en&format=json"

_COUNTY_RE = re.compile(r"([A-Z][a-z]+(?: [A-Z][a-z]+)* County)", re.UNICODE)


def _truncate_at_sentence(text: str, max_chars: int = 500) -> str:
    if len(text) <= max_chars:
        return text
    cut = text[:max_chars]
    for punct in (".", "!", "?"):
        pos = cut.rfind(punct)
        if pos > max_chars // 2:
            return cut[: pos + 1]
    return cut


def scrape_wikipedia(island_name: str, client: HttpClient) -> ScraperResult:
    try:
        data = _fetch_and_parse(island_name, client)
        if data is None:
            return ScraperResult(
                success=False,
                data=None,
                source="wikipedia",
                error=f"No Wikipedia page found for '{island_name}'",
            )
        non_null = sum(1 for v in data.values() if v is not None)
        coverage = round(non_null / len(data) * 100, 1)
        return ScraperResult(success=True, data=data, source="wikipedia", coverage_pct=coverage)
    except Exception as exc:
        logger.error("Wikipedia scraper failed: %s", exc)
        return ScraperResult(success=False, data=None, source="wikipedia", error=str(exc))


def _fetch_and_parse(island_name: str, client: HttpClient) -> dict | None:
    summary = _fetch_summary(island_name, client)
    if summary is None:
        return None

    coords = None
    if "coordinates" in summary:
        c = summary["coordinates"]
        coords = {"lat": c.get("lat"), "lon": c.get("lon")}

    extract = summary.get("extract", "") or ""
    description = summary.get("description", "")
    wikibase_item = summary.get("wikibase_item")

    region = _extract_region(extract)
    area_km2, population, wikidata_region = None, None, None

    if wikibase_item:
        area_km2, population, wikidata_region = _fetch_wikidata(wikibase_item, client)

    if region is None:
        region = wikidata_region

    data = {
        "name": summary.get("title", island_name),
        "name_hr": island_name,
        "description": description,
        "extract": _truncate_at_sentence(extract, 500) if extract else None,
        "coordinates": coords,
        "area_km2": area_km2,
        "population": population,
        "region": region,
        "wikibase_item": wikibase_item,
    }
    return data


def _fetch_summary(island_name: str, client: HttpClient) -> dict | None:
    encoded = island_name.replace(" ", "_")
    candidates = [
        encoded,
        f"{encoded}_(island)",
        f"{encoded}_island",
    ]
    for slug in candidates:
        try:
            data = client.get(_WIKI_SUMMARY.format(slug))
            if data.get("type") == "disambiguation":
                logger.debug("Wikipedia: '%s' is a disambiguation page, trying next", slug)
                continue
            if data.get("extract"):
                return data
        except Exception as exc:
            logger.debug("English Wikipedia failed for '%s': %s", slug, exc)

    logger.info("English Wikipedia exhausted candidates for '%s' — trying Croatian", island_name)
    try:
        return client.get(_WIKI_SUMMARY_HR.format(encoded))
    except Exception as exc:
        logger.warning("Croatian Wikipedia also failed for '%s': %s", island_name, exc)
        return None


def _fetch_wikidata(qid: str, client: HttpClient) -> tuple[float | None, int | None, str | None]:
    try:
        raw = client.get(_WIKIDATA_ENTITY.format(qid))
        entities = raw.get("entities", {})
        entity = entities.get(qid) or next(iter(entities.values()), {})
        area = _safe_wikidata_value(entity, "P2046")
        pop = _safe_wikidata_value(entity, "P1082")
        area_km2 = float(area) if area is not None else None
        population = int(float(pop)) if pop is not None else None
        region = _fetch_wikidata_region(entity, client)
        return area_km2, population, region
    except Exception as exc:
        logger.debug("Wikidata fetch failed for %s: %s", qid, exc)
        return None, None, None


def _fetch_wikidata_region(entity: dict, client: HttpClient) -> str | None:
    p131_qid = _safe_wikidata_qid(entity, "P131")
    if not p131_qid:
        return None
    try:
        label = _fetch_entity_label(p131_qid, client)
        if label and "County" in label:
            return label
        # P131 may point to municipality — try one level up
        raw = client.get(_WIKIDATA_ENTITY.format(p131_qid))
        entities = raw.get("entities", {})
        parent_entity = entities.get(p131_qid) or next(iter(entities.values()), {})
        parent_qid = _safe_wikidata_qid(parent_entity, "P131")
        if parent_qid:
            parent_label = _fetch_entity_label(parent_qid, client)
            if parent_label and "County" in parent_label:
                return parent_label
        return label
    except Exception as exc:
        logger.debug("Wikidata region fetch failed: %s", exc)
        return None


def _fetch_entity_label(qid: str, client: HttpClient) -> str | None:
    try:
        data = client.get(_WIKIDATA_LABELS.format(qid))
        return data["entities"][qid]["labels"]["en"]["value"]
    except (KeyError, Exception):
        return None


def _safe_wikidata_value(entity: dict, prop: str) -> Any:
    try:
        claims = entity["claims"][prop]
        # Prefer "preferred" rank, accept "normal", skip "deprecated"
        rank_order = {"preferred": 0, "normal": 1, "deprecated": 2}
        ranked = sorted(claims, key=lambda c: rank_order.get(c.get("rank", "normal"), 1))
        for claim in ranked:
            if claim.get("rank") == "deprecated":
                continue
            snak = claim.get("mainsnak", {})
            if snak.get("snaktype") != "value":
                continue
            val = snak["datavalue"]["value"]
            if isinstance(val, dict):
                amount = val.get("amount", "")
                return str(amount).lstrip("+")
            return val
    except (KeyError, IndexError, TypeError):
        pass
    return None


def _safe_wikidata_qid(entity: dict, prop: str) -> str | None:
    try:
        claims = entity["claims"][prop]
        rank_order = {"preferred": 0, "normal": 1, "deprecated": 2}
        ranked = sorted(claims, key=lambda c: rank_order.get(c.get("rank", "normal"), 1))
        for claim in ranked:
            if claim.get("rank") == "deprecated":
                continue
            snak = claim.get("mainsnak", {})
            if snak.get("snaktype") != "value":
                continue
            val = snak["datavalue"]["value"]
            if isinstance(val, dict) and val.get("entity-type") == "item":
                return val.get("id")
    except (KeyError, IndexError, TypeError):
        pass
    return None


def _extract_region(text: str) -> str | None:
    if not text:
        return None
    m = _COUNTY_RE.search(text)
    return m.group(1) if m else None
