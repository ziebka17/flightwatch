"""Klient Travelpayouts / Aviasales Data API (prices_for_dates).

Zwraca najtańsze znalezione ceny biletów (w tym z przesiadkami) z pamięci podręcznej
Aviasales. Jedno zapytanie pokrywa cały miesiąc wylotów, więc skanowanie jest tanie.
Dokumentacja: https://support.travelpayouts.com/hc/en-us/articles/203956163-Aviasales-Data-API
"""
from __future__ import annotations

import json
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

from .models import Offer, Segment

log = logging.getLogger(__name__)

BASE_URL = "https://api.travelpayouts.com/aviasales/v3/prices_for_dates"
AVIASALES_WEB = "https://www.aviasales.com"


class TravelpayoutsError(Exception):
    pass


class TravelpayoutsClient:
    def __init__(self, token: str, market: str = "pl", opener=None, timeout: int = 30):
        if not token:
            raise TravelpayoutsError("brak tokenu Travelpayouts")
        self.token = token
        self.market = market
        self.opener = opener or _http_get   # podmienialne w testach
        self.timeout = timeout
        self.calls_made = 0

    def search_month(self, origin: str, destination: str, month: str, currency: str = "PLN",
                     limit: int = 100, retries: int = 3) -> list[Offer]:
        """Najtańsze bilety powrotne dla wylotów w danym miesiącu (YYYY-MM)."""
        params = {
            "origin": origin,
            "destination": destination,
            "departure_at": month,
            "one_way": "false",
            "direct": "false",
            "currency": currency.lower(),
            "limit": limit,
            "sorting": "price",
            "market": self.market,
            "token": self.token,
        }
        url = BASE_URL + "?" + urllib.parse.urlencode(params)
        last_err: Exception | None = None
        for attempt in range(1, retries + 1):
            try:
                status, text = self.opener(url, self.timeout)
            except OSError as exc:
                last_err = exc
                log.warning("błąd sieci (%s/%s) %s-%s %s: %s", attempt, retries, origin, destination, month, exc)
                time.sleep(2 * attempt)
                continue
            self.calls_made += 1
            if status == 200:
                try:
                    body = json.loads(text)
                except ValueError as exc:
                    raise TravelpayoutsError(f"niepoprawny JSON: {exc}")
                if not body.get("success", True):
                    raise TravelpayoutsError(f"API zwróciło błąd: {str(body)[:300]}")
                return parse_offers(body, origin, destination, currency)
            if status in (401, 403):
                raise TravelpayoutsError(f"token odrzucony ({status}): {text[:200]}")
            if status == 429 or status >= 500:
                wait = 5 * attempt
                log.warning("HTTP %s, czekam %ss", status, wait)
                time.sleep(wait)
                last_err = TravelpayoutsError(f"HTTP {status}")
                continue
            raise TravelpayoutsError(f"nieoczekiwany HTTP {status}: {text[:300]}")
        log.error("poddaję się: %s-%s %s po %s próbach: %s", origin, destination, month, retries, last_err)
        return []


def _http_get(url: str, timeout: int) -> tuple[int, str]:
    """GET przez bibliotekę standardową. Zwraca (status, treść)."""
    req = urllib.request.Request(url, headers={"User-Agent": "FlightWatch/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")


_ROUTE_RE = re.compile(r"\d((?:[A-Z]{3}){2,})(?=\d|_|$)")


def route_from_link(link: str, origin_ap: str, dest_ap: str) -> tuple[list[str], list[str]]:
    """Wyciąga lotniska z linku Aviasales, np. ...1995GDNHAMCDGJFK1791...0865JFKAMSHAMGDN_...
    Zwraca (lotniska_tam, lotniska_powrót) lub ([], []) gdy nie da się odczytać."""
    try:
        t = urllib.parse.parse_qs(urllib.parse.urlparse(link).query).get("t", [""])[0]
    except Exception:
        return [], []
    runs = [m.group(1) for m in _ROUTE_RE.finditer(t)]
    paths = [[r[i:i + 3] for i in range(0, len(r), 3)] for r in runs]
    out = next((p for p in paths if p[0] == origin_ap and p[-1] == dest_ap), [])
    back = next((p for p in paths if p[0] == dest_ap and p[-1] == origin_ap), [])
    return out, back


def _parse_dt(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def parse_offers(payload: dict, origin: str, destination: str, currency: str) -> list[Offer]:
    """Zamienia odpowiedź prices_for_dates na obiekty Offer. Pomija uszkodzone wpisy."""
    offers: list[Offer] = []
    for item in payload.get("data", []) or []:
        try:
            price = float(item["price"])
            dep = _parse_dt(item.get("departure_at"))
            ret = _parse_dt(item.get("return_at"))
            if price <= 0 or dep is None or ret is None or ret.date() <= dep.date():
                continue
            airline = str(item.get("airline", "") or "?")
            fnum = str(item.get("flight_number", "") or "")
            transfers = int(item.get("transfers", 0) or 0)
            return_transfers = int(item.get("return_transfers", transfers) or 0)
            from_ap = str(item.get("origin_airport", origin) or origin)
            to_ap = str(item.get("destination_airport", destination) or destination)
            link = str(item.get("link", "") or "")
            # Lotniska pośrednie próbujemy odczytać z linku; gdy się nie da, oznaczamy je "?".
            path_out, path_back = route_from_link(link, from_ap, to_ap)
            if len(path_out) != transfers + 2:
                path_out = [from_ap] + ["?"] * transfers + [to_ap]
            if len(path_back) != return_transfers + 2:
                path_back = [to_ap] + ["?"] * return_transfers + [from_ap]
            outbound = [Segment(airline, fnum if i == 0 else "", path_out[i], path_out[i + 1],
                                dep.isoformat() if i == 0 else "", "") for i in range(len(path_out) - 1)]
            inbound = [Segment(airline, "", path_back[i], path_back[i + 1],
                               ret.isoformat() if i == 0 else "", "") for i in range(len(path_back) - 1)]
            offers.append(Offer(
                origin=origin, destination=destination,
                depart_date=dep.date(), return_date=ret.date(),
                price=price, currency=currency.upper(),
                outbound=outbound, inbound=inbound,
                validating_airlines=[airline] + ([str(item["gate"])] if item.get("gate") else []),
                offer_id=(AVIASALES_WEB + link) if link.startswith("/") else link,
            ))
        except (KeyError, TypeError, ValueError) as exc:
            log.debug("pomijam uszkodzony wpis: %s", exc)
    offers.sort(key=lambda o: o.price)
    return offers
