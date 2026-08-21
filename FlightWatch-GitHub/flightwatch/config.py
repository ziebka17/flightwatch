"""Wczytywanie i walidacja konfiguracji."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import json


class ConfigError(Exception):
    pass


@dataclass(frozen=True)
class Destination:
    code: str
    name: str
    region: str
    fixed_threshold: float


@dataclass
class Config:
    origin: str
    currency: str
    adults: int
    months_ahead: int
    min_days_ahead: int
    trip_length_min_days: int
    trip_length_max_days: int
    max_results_per_query: int
    max_alerts_per_destination: int
    market: str
    monthly_max_calls: int
    destinations: list[Destination]
    history_lookback_days: int
    history_min_samples: int
    history_drop_fraction: float
    alert_cooldown_hours: int
    email_enabled: bool
    smtp_host: str
    smtp_port: int
    database: str
    raw: dict[str, Any] = field(default_factory=dict)


def _require(d: dict, key: str, typ, what: str):
    if key not in d:
        raise ConfigError(f"Missing '{key}' in {what}")
    val = d[key]
    if typ is float and isinstance(val, int):
        val = float(val)
    if not isinstance(val, typ) or (typ is int and isinstance(val, bool)):
        raise ConfigError(f"'{key}' in {what} must be {typ.__name__}, got {type(val).__name__}")
    return val


def load_config(path: str | Path) -> Config:
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"Config file not found: {path}")
    with open(path, "r", encoding="utf-8") as fh:
        try:
            raw = json.load(fh) or {}
        except ValueError as exc:
            raise ConfigError(f"{path} nie jest poprawnym plikiem JSON: {exc}")

    origin = _require(raw, "origin", str, "config").upper().strip()
    if len(origin) != 3:
        raise ConfigError("origin must be a 3-letter IATA code")

    search = _require(raw, "search", dict, "config")
    months_ahead = _require(search, "months_ahead", int, "search")
    min_ahead = int(search.get("min_days_ahead", 7))
    tl_min = _require(search, "trip_length_min_days", int, "search")
    tl_max = _require(search, "trip_length_max_days", int, "search")
    if months_ahead < 1 or months_ahead > 12:
        raise ConfigError("search.months_ahead must be between 1 and 12")
    if min_ahead < 0 or tl_min < 1 or tl_max < tl_min:
        raise ConfigError("search: need min_days_ahead >= 0 and 1 <= trip_length_min_days <= trip_length_max_days")
    max_results = int(search.get("max_results_per_query", 100))
    if not (1 <= max_results <= 1000):
        raise ConfigError("search.max_results_per_query must be 1..1000")

    regions = _require(raw, "regions", dict, "config")
    destinations: list[Destination] = []
    seen: set[str] = set()
    for region_name, region in regions.items():
        if not isinstance(region, dict):
            raise ConfigError(f"region '{region_name}' must be a mapping")
        thr = _require(region, "fixed_threshold", float, f"region '{region_name}'")
        for d in _require(region, "destinations", list, f"region '{region_name}'"):
            code = _require(d, "code", str, f"destination in '{region_name}'").upper().strip()
            if len(code) != 3:
                raise ConfigError(f"destination code '{code}' must be 3 letters")
            if code == origin:
                raise ConfigError(f"destination {code} equals origin")
            if code in seen:
                raise ConfigError(f"duplicate destination {code}")
            seen.add(code)
            destinations.append(Destination(code, str(d.get("name", code)), region_name, thr))
    if not destinations:
        raise ConfigError("no destinations configured")

    hist = _require(raw, "history", dict, "config")
    drop = _require(hist, "drop_fraction", float, "history")
    if not (0 < drop < 1):
        raise ConfigError("history.drop_fraction must be between 0 and 1")

    alerts = _require(raw, "alerts", dict, "config")
    email = alerts.get("email", {}) or {}

    return Config(
        origin=origin,
        currency=str(raw.get("currency", "PLN")).upper(),
        adults=int(raw.get("adults", 1)),
        months_ahead=months_ahead,
        min_days_ahead=min_ahead,
        trip_length_min_days=tl_min,
        trip_length_max_days=tl_max,
        max_results_per_query=max_results,
        max_alerts_per_destination=int(search.get("max_alerts_per_destination", 3)),
        market=str(search.get("market", "pl")).lower(),
        monthly_max_calls=int(_require(raw, "api_budget", dict, "config").get("monthly_max_calls", 20000)),
        destinations=destinations,
        history_lookback_days=int(hist.get("lookback_days", 45)),
        history_min_samples=int(hist.get("min_samples", 8)),
        history_drop_fraction=drop,
        alert_cooldown_hours=int(alerts.get("cooldown_hours", 24)),
        email_enabled=bool(email.get("enabled", False)),
        smtp_host=str(email.get("smtp_host", "smtp.gmail.com")),
        smtp_port=int(email.get("smtp_port", 587)),
        database=str(raw.get("database", "flightwatch.sqlite3")),
        raw=raw,
    )


def env(name: str, required: bool = True) -> str:
    val = os.environ.get(name, "").strip()
    if required and not val:
        raise ConfigError(f"Environment variable {name} is not set (see .env.example)")
    return val
