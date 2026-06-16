# 🏝️ Croatian Island Guide Scraper

A multi-source Python data pipeline that aggregates public information about Croatian islands and generates structured tourist guides. Point it at any Croatian island and it produces a clean JSON dataset and a formatted Markdown guide in seconds.

**Live demo:** [lavicitor.github.io/island-guide-data](https://lavicitor.github.io/island-guide-data) *(update once deployed)*

---

## What it does

Running the scraper against an island name pulls data from four independent sources, merges it into a typed data model, and outputs two artefacts:

- `data/<island>.json` — structured, machine-readable dataset
- `guides/<island>.md` — formatted tourist guide in Markdown

The tool handles missing data gracefully — if one source fails or has sparse coverage, the rest continue and the output flags what's missing rather than crashing.

---

## Data sources

| Source | What it provides | Method |
|---|---|---|
| **OpenStreetMap** (Overpass API) | Points of interest — beaches, restaurants, ATMs, accommodation, landmarks with coordinates | REST API, no key required |
| **Open-Meteo** | 12-month climate data — temperature, rainfall, sunshine hours, sea temperature | REST API, no key required |
| **Jadrolinija** | Ferry routes, operators, bike/car policies, booking URLs | HTML scraping with robots.txt compliance |
| **Wikipedia** | Island identity — area, region, coordinates, description | REST API |

---

## Features

- **Island-agnostic** — one command works for Silba, Hvar, Korčula, or any of Croatia's 1,200 islands
- **robots.txt compliance** — checked before any HTML scraping begins
- **Rate limiting** — randomised delays between requests, configurable in the `CONFIG` block
- **Retry logic** — exponential back-off on transient failures (3 attempts per request)
- **User-agent rotation** — cycles through realistic browser signatures
- **File-based caching** — avoids redundant fetches; 24-hour TTL, bypassable with `--refresh`
- **Graceful degradation** — partial data is better than no data; failures are logged and flagged in output
- **Data quality reporting** — every run ends with a summary of what was found and what was missing

---

## Quickstart

### Install dependencies

```bash
git clone https://github.com/lavicitor/island-guide-scraper.git
cd island-guide-scraper
pip install -r requirements.txt
```

### Run

```bash
# Generate a guide for Silba
python island_guide.py --island "Silba"

# Any other Croatian island
python island_guide.py --island "Hvar"
python island_guide.py --island "Korčula"

# Force re-fetch (bypass cache)
python island_guide.py --island "Silba" --refresh

# Verbose logging — shows every request
python island_guide.py --island "Silba" --verbose

# Custom output directory
python island_guide.py --island "Vis" --output-dir ./output
```

### Example output

```
────────────────────────────────────────────────────────
  Croatian Island Guide Scraper
  Island  : Silba
  Started : 2026-06-16 17:07:51
────────────────────────────────────────────────────────

17:07:51  INFO  [wikipedia ] Fetching island identity...
17:07:52  INFO  [wikipedia ] ✓ Silba — Zadar County, 15.0 km²
17:07:52  INFO  [overpass  ] Querying OpenStreetMap POIs...
17:07:54  INFO  [overpass  ] ✓ 29 POIs found (12 beaches, 6 restaurants, 1 ATM, 7 accommodation)
17:07:54  INFO  [weather   ] Fetching climate data...
17:07:55  INFO  [weather   ] ✓ 12 months complete
17:07:55  INFO  [jadrolinija] Checking robots.txt...
17:07:56  INFO  [jadrolinija] ✓ Crawling permitted
17:07:56  INFO  [jadrolinija] ✓ 2 ferry routes found

════════════════════════════════════════════════════════
  COMPLETE
────────────────────────────────────────────────────────
  POIs found         : 29
  Ferry routes       : 2
  Weather complete   : 12/12 months
  Time taken         : 4.2s
  Output             : data/silba.json
                       guides/silba.md
════════════════════════════════════════════════════════
```

A worked example for Silba is committed to this repo — see [`data/silba.json`](data/silba.json) and [`guides/silba.md`](guides/silba.md).

---

## Output structure

The JSON output follows a consistent schema across all islands:

```
{
  meta          →  run timestamp, data quality summary
  identity      →  name, region, coordinates, area, population, car-free flag
  getting_there →  ferry routes with operator, duration, frequency, bike/car flags
  weather       →  monthly averages for temp, rainfall, sunshine, sea temperature
  points_of_interest
    beaches       →  name (where tagged), coordinates, surface type
    restaurants   →  name, coordinates, cuisine, payment methods, opening hours
    atms          →  name, coordinates, operator
    medical       →  facilities where tagged in OSM
    accommodation →  guesthouses, hotels, apartments with coordinates
    landmarks     →  historic sites, memorials, points of interest
  practical     →  ATM count, medical availability, cash warning, nearby islands
}
```

---

## Project structure

```
island-guide-scraper/
│
├── island_guide.py        # Entry point — CLI, orchestration, summary
│
├── scrapers/
│   ├── wikipedia.py       # Island identity and coordinates
│   ├── overpass.py        # OpenStreetMap POI data via Overpass API
│   ├── weather.py         # Open-Meteo climate data
│   └── jadrolinija.py     # Ferry schedules (HTML scraping)
│
├── core/
│   ├── models.py          # Dataclasses matching the JSON schema
│   ├── guide_writer.py    # JSON → Markdown renderer
│   ├── cache.py           # File-based caching layer
│   └── http_client.py     # Shared HTTP with retry, rate limiting, UA rotation
│
├── tests/
│   ├── test_overpass.py
│   ├── test_weather.py
│   └── fixtures/          # Saved API responses for offline testing
│
├── data/silba.json        # Example output — Silba island
├── guides/silba.md        # Example output — Silba island guide
└── requirements.txt
```

---

## Configuration

All tunables live in the `CONFIG` block at the top of `island_guide.py`:

```python
CONFIG = {
    "request_delay_min":  1.0,   # seconds between requests (same domain)
    "request_delay_max":  2.5,
    "max_retries":        3,
    "backoff_factor":     2.0,   # wait = backoff_factor ^ attempt
    "cache_ttl_hours":    24,
    "osm_radius_km":      15,    # bounding box radius for POI search
}
```

---

## Requirements

- Python 3.9+
- `requests >= 2.28`
- `beautifulsoup4 >= 4.12`
- `lxml >= 5.0`

No heavy dependencies. No Scrapy, no Selenium, no Pandas.

---

## Limitations

- **Jadrolinija timetables** — journey duration and sailing frequency require JavaScript rendering which this scraper does not implement. Ferry route URLs and bike/car policies are scraped correctly; timetable detail is supplemented via a manual data layer in the companion data repository.
- **OSM coverage varies** — small or rarely-visited islands may have sparse POI data. The scraper reports coverage in the output summary so you always know what you're working with.
- **Accommodation pricing** — Booking.com and Airbnb are ToS-hostile to scraping and are out of scope.

---

## Extending to other regions

The scraper is built for Croatian islands but the architecture is region-agnostic. Swapping the Jadrolinija module for a different ferry operator and adjusting the Wikipedia lookup language would adapt it to Greek islands, Scottish islands, or any archipelago with reasonable OSM coverage.

---

## License

MIT
