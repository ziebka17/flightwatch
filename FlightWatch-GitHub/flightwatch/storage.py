"""Baza SQLite: obserwacje cen, dziennik alertów, licznik zapytań API."""
from __future__ import annotations

import sqlite3
import statistics
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .models import Offer

SCHEMA = """
CREATE TABLE IF NOT EXISTS observations (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    observed_at   TEXT NOT NULL,          -- UTC ISO timestamp
    origin        TEXT NOT NULL,
    destination   TEXT NOT NULL,
    depart_date   TEXT NOT NULL,
    return_date   TEXT NOT NULL,
    price         REAL NOT NULL,
    currency      TEXT NOT NULL,
    outbound_path TEXT NOT NULL,
    inbound_path  TEXT NOT NULL,
    stops_out     INTEGER NOT NULL,
    stops_in      INTEGER NOT NULL,
    airlines      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_obs_route ON observations(origin, destination, observed_at);

CREATE TABLE IF NOT EXISTS alerts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    sent_at     TEXT NOT NULL,
    alert_key   TEXT NOT NULL,
    reason      TEXT NOT NULL,
    price       REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_alert_key ON alerts(alert_key, sent_at);

CREATE TABLE IF NOT EXISTS api_calls (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    called_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_api_called_at ON api_calls(called_at);
"""


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Storage:
    def __init__(self, path: str | Path):
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    def close(self) -> None:
        self.conn.close()

    # ---- observations -----------------------------------------------------
    def record_offers(self, offers: list[Offer]) -> None:
        now = _now().isoformat()
        rows = [(
            now, o.origin, o.destination, o.depart_date.isoformat(), o.return_date.isoformat(),
            o.price, o.currency, o.describe_path(o.outbound), o.describe_path(o.inbound),
            o.outbound_stops, o.inbound_stops, ",".join(o.validating_airlines),
        ) for o in offers]
        with self.conn:
            self.conn.executemany(
                "INSERT INTO observations (observed_at, origin, destination, depart_date, return_date, price, "
                "currency, outbound_path, inbound_path, stops_out, stops_in, airlines) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", rows)

    def route_stats(self, origin: str, destination: str, lookback_days: int,
                    exclude_after: datetime | None = None) -> tuple[int, float | None]:
        """Zwraca (liczba_próbek, mediana) najtańszej ceny z każdego
        (skanowania, daty wylotu, daty powrotu) na tej trasie w oknie czasowym.
        Minimum z zapytania zapobiega zawyżaniu mediany przez drogie oferty."""
        since = (_now() - timedelta(days=lookback_days)).isoformat()
        until = (exclude_after or _now()).isoformat()
        rows = self.conn.execute(
            "SELECT MIN(price) AS p FROM observations "
            "WHERE origin=? AND destination=? AND observed_at>=? AND observed_at<? "
            "GROUP BY observed_at, depart_date, return_date",
            (origin, destination, since, until)).fetchall()
        prices = [r["p"] for r in rows]
        if not prices:
            return 0, None
        return len(prices), float(statistics.median(prices))

    # ---- alerts -----------------------------------------------------------
    @staticmethod
    def alert_key(o: Offer) -> str:
        return f"{o.origin}-{o.destination}|{o.depart_date}|{o.return_date}|{round(o.price)}"

    def recently_alerted(self, key: str, cooldown_hours: int) -> bool:
        since = (_now() - timedelta(hours=cooldown_hours)).isoformat()
        row = self.conn.execute(
            "SELECT 1 FROM alerts WHERE alert_key=? AND sent_at>=? LIMIT 1", (key, since)).fetchone()
        return row is not None

    def record_alert(self, key: str, reason: str, price: float) -> None:
        with self.conn:
            self.conn.execute("INSERT INTO alerts (sent_at, alert_key, reason, price) VALUES (?,?,?,?)",
                              (_now().isoformat(), key, reason, price))

    # ---- API budget -------------------------------------------------------
    def record_api_call(self) -> None:
        with self.conn:
            self.conn.execute("INSERT INTO api_calls (called_at) VALUES (?)", (_now().isoformat(),))

    def api_calls_this_month(self) -> int:
        start = _now().replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()
        return int(self.conn.execute(
            "SELECT COUNT(*) FROM api_calls WHERE called_at>=?", (start,)).fetchone()[0])

    # ---- reporting --------------------------------------------------------
    def cheapest_per_route(self, since_hours: int = 24) -> list[sqlite3.Row]:
        since = (_now() - timedelta(hours=since_hours)).isoformat()
        return self.conn.execute(
            "SELECT origin, destination, depart_date, return_date, MIN(price) AS price, currency, "
            "outbound_path, inbound_path FROM observations WHERE observed_at>=? "
            "GROUP BY origin, destination ORDER BY price", (since,)).fetchall()
