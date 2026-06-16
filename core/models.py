from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ScraperResult:
    success: bool
    data: Any | None
    source: str
    error: str | None = None
    coverage_pct: float | None = None


@dataclass
class Coordinates:
    lat: float
    lon: float


@dataclass
class DataQuality:
    osm_poi_count: int
    ferry_routes_found: int
    weather_months_complete: int
    coverage_warning: str | None = None


@dataclass
class IslandMeta:
    island: str
    slug: str
    generated_at: str
    data_quality: DataQuality


@dataclass
class IslandIdentity:
    name: str
    name_hr: str | None = None
    region: str | None = None
    coordinates: Coordinates | None = None
    area_km2: float | None = None
    population: int | None = None
    car_free: bool | None = None
    description: str | None = None
    best_for: list[str] = field(default_factory=list)


@dataclass
class FerryRoute:
    origin: str
    destination: str
    operator: str | None = None
    duration_minutes: int | None = None
    frequency_peak: int | None = None
    frequency_low: int | None = None
    bikes_allowed: bool | None = None
    cars_allowed: bool | None = None
    booking_url: str | None = None


@dataclass
class GettingThere:
    ferry_routes: list[FerryRoute] = field(default_factory=list)


@dataclass
class MonthlyWeather:
    month: int
    month_name: str
    avg_temp_c: float | None = None
    avg_rain_days: float | None = None
    avg_sunshine_hours: float | None = None
    sea_temp_c: float | None = None
    peak_season: bool = False


@dataclass
class WeatherSummary:
    monthly: list[MonthlyWeather] = field(default_factory=list)
    best_months: list[str] = field(default_factory=list)
    peak_season_months: list[str] = field(default_factory=list)


@dataclass
class PointOfInterest:
    name: str | None = None
    coordinates: Coordinates | None = None
    osm_tags: dict = field(default_factory=dict)
    notes: str | None = None


@dataclass
class PointsOfInterest:
    beaches: list[PointOfInterest] = field(default_factory=list)
    restaurants: list[PointOfInterest] = field(default_factory=list)
    atms: list[PointOfInterest] = field(default_factory=list)
    medical: list[PointOfInterest] = field(default_factory=list)
    accommodation: list[PointOfInterest] = field(default_factory=list)
    landmarks: list[PointOfInterest] = field(default_factory=list)


@dataclass
class PracticalInfo:
    atm_available: bool = False
    atm_count: int = 0
    medical_facility: bool = False
    cash_only_warning: bool = False
    day_trip_viable: bool = False
    nearby_islands: list[str] = field(default_factory=list)
    recommended_stay_nights: int | None = None


@dataclass
class IslandGuide:
    meta: IslandMeta
    identity: IslandIdentity
    getting_there: GettingThere
    weather: WeatherSummary
    points_of_interest: PointsOfInterest
    practical: PracticalInfo
