"""Punkt wejścia FlightWatch.

Użycie:
    python -m flightwatch.main            # jedno skanowanie
    python -m flightwatch.main --loop 6   # skanuj co 6 godzin, bez końca
    python -m flightwatch.main --dry-run  # skanuj i wypisz, bez maila
    python -m flightwatch.main --report   # najtańsza cena na trasę z ostatnich 24 h
    python -m flightwatch.main --test-email
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from datetime import date, timedelta
from pathlib import Path

from .travelpayouts import TravelpayoutsClient, TravelpayoutsError
from .config import Config, ConfigError, env, load_config
from .detector import evaluate
from .models import Alert
from .notifier import render_text, send_email
from .storage import Storage

log = logging.getLogger("flightwatch")


def load_dotenv(path: Path) -> None:
    """Minimalny loader .env (KEY=VALUE w każdej linii). Nie nadpisuje istniejących zmiennych."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        os.environ.setdefault(k, v)


def months_to_scan(cfg: Config, today: date | None = None) -> list[str]:
    """Lista miesięcy YYYY-MM: bieżący + kolejne, łącznie cfg.months_ahead."""
    today = today or date.today()
    out = []
    y, m = today.year, today.month
    for _ in range(cfg.months_ahead):
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m > 12:
            y, m = y + 1, 1
    return out


def offer_in_window(o, cfg: Config, today: date | None = None) -> bool:
    today = today or date.today()
    length = (o.return_date - o.depart_date).days
    return (o.depart_date >= today + timedelta(days=cfg.min_days_ahead)
            and cfg.trip_length_min_days <= length <= cfg.trip_length_max_days)


def scan(cfg: Config, client: TravelpayoutsClient, storage: Storage) -> list[Alert]:
    months = months_to_scan(cfg)
    total_queries = len(months) * len(cfg.destinations)
    used = storage.api_calls_this_month()
    log.info("planuję %d zapytań (%d celów x %d miesięcy); wykorzystano %d/%d zapytań w tym miesiącu",
             total_queries, len(cfg.destinations), len(months), used, cfg.monthly_max_calls)

    alerts: list[Alert] = []
    for dest in cfg.destinations:
        dest_alerts: list[Alert] = []
        for month in months:
            if storage.api_calls_this_month() >= cfg.monthly_max_calls:
                log.warning("osiągnięto miesięczny limit API (%d) – przerywam skanowanie", cfg.monthly_max_calls)
                alerts.extend(dest_alerts)
                alerts.sort(key=lambda a: a.offer.price)
                return alerts
            storage.record_api_call()
            try:
                offers = client.search_month(cfg.origin, dest.code, month, currency=cfg.currency,
                                             limit=cfg.max_results_per_query)
            except TravelpayoutsError as exc:
                log.error("%s-%s %s: %s", cfg.origin, dest.code, month, exc)
                if "token" in str(exc):
                    raise
                continue
            offers = [o for o in offers if offer_in_window(o, cfg)]
            if not offers:
                log.debug("brak ofert %s-%s %s", cfg.origin, dest.code, month)
                continue

            best = offers[0]  # parse_offers sortuje po cenie
            log.info("%s-%s %s najtaniej %.0f %s (%s..%s, %d przesiadek)", cfg.origin, dest.code, month,
                     best.price, best.currency, best.depart_date, best.return_date, best.outbound_stops)

            # Oceń PRZED zapisaniem, żeby oferty nie zaniżały własnej historii.
            for o in offers:
                alert = evaluate(o, dest, cfg, storage)
                if alert and not storage.recently_alerted(Storage.alert_key(o), cfg.alert_cooldown_hours):
                    dest_alerts.append(alert)
            storage.record_offers(offers)
            time.sleep(0.2)  # nie zalewaj API
        dest_alerts.sort(key=lambda a: a.offer.price)
        if len(dest_alerts) > cfg.max_alerts_per_destination:
            log.info("%s: %d alertów, wysyłam %d najtańszych", dest.code, len(dest_alerts), cfg.max_alerts_per_destination)
        alerts.extend(dest_alerts[:cfg.max_alerts_per_destination])
    alerts.sort(key=lambda a: a.offer.price)
    return alerts


def deliver(cfg: Config, storage: Storage, alerts: list[Alert], dry_run: bool) -> None:
    if not alerts:
        log.info("brak okazji w tym skanowaniu")
        return
    print(render_text(alerts))
    if dry_run:
        log.info("dry-run: mail pominięty, alerty niezapisane")
        return
    if cfg.email_enabled:
        try:
            send_email(alerts, cfg.smtp_host, cfg.smtp_port,
                       env("FW_EMAIL_USER"), env("FW_EMAIL_PASSWORD"), env("FW_EMAIL_TO"))
        except Exception as exc:  # problemy SMTP/logowania/sieci nie mogą zatrzymać pętli
            log.error("wysyłka maila nie powiodła się, ponowię przy następnym skanowaniu: %s", exc)
            return
    for a in alerts:
        storage.record_alert(Storage.alert_key(a.offer), a.reason, a.offer.price)


def report(storage: Storage) -> None:
    rows = storage.cheapest_per_route(since_hours=24)
    if not rows:
        print("Brak obserwacji z ostatnich 24 godzin.")
        return
    print(f"{'Trasa':10} {'Daty':25} {'Cena':>12}  Połączenie")
    for r in rows:
        print(f"{r['origin']}-{r['destination']:<6} {r['depart_date']} .. {r['return_date']}  "
              f"{r['price']:>8.0f} {r['currency']}  {r['outbound_path']}")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Szuka błędnych taryf / dużych spadków cen lotów z Gdańska.")
    p.add_argument("--config", default="config.json")
    p.add_argument("--loop", type=float, metavar="HOURS", help="działaj bez końca, co N godzin")
    p.add_argument("--dry-run", action="store_true", help="skanuj i wypisz, ale nie wysyłaj maila")
    p.add_argument("--report", action="store_true", help="wypisz najtańsze ceny z ostatnich 24 h i zakończ")
    p.add_argument("--test-email", action="store_true", help="wyślij testowego maila i zakończ")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    load_dotenv(Path(".env"))

    try:
        cfg = load_config(args.config)
    except ConfigError as exc:
        log.error("błąd konfiguracji: %s", exc)
        return 2

    storage = Storage(cfg.database)
    try:
        if args.report:
            report(storage)
            return 0

        if args.test_email:
            from datetime import date as _d
            from .models import Offer, Segment
            seg = Segment("XX", "1", cfg.origin, "TST", "2000-01-01T10:00", "2000-01-01T12:00")
            fake = Offer(cfg.origin, "TST", _d(2000, 1, 1), _d(2000, 1, 8), 1.0, cfg.currency, [seg], [seg])
            try:
                send_email([Alert(fake, "test", 1.0, None, None)], cfg.smtp_host, cfg.smtp_port,
                           env("FW_EMAIL_USER"), env("FW_EMAIL_PASSWORD"), env("FW_EMAIL_TO"))
            except Exception as exc:
                log.error("wysyłka testowego maila nie powiodła się: %s", exc)
                log.error("Sprawdź FW_EMAIL_USER / FW_EMAIL_PASSWORD (hasło aplikacji Gmail) w pliku .env")
                return 1
            print("Testowy mail wysłany.")
            return 0

        try:
            client = TravelpayoutsClient(env("TRAVELPAYOUTS_TOKEN"), market=cfg.market)
        except (ConfigError, TravelpayoutsError) as exc:
            log.error("%s", exc)
            return 2

        while True:
            try:
                alerts = scan(cfg, client, storage)
            except TravelpayoutsError as exc:
                log.error("skanowanie przerwane: %s", exc)
                if not args.loop:
                    return 1
                alerts = []
            deliver(cfg, storage, alerts, args.dry_run)
            if not args.loop:
                return 0
            log.info("czekam %.1f h", args.loop)
            time.sleep(args.loop * 3600)
    except KeyboardInterrupt:
        log.info("zatrzymano")
        return 0
    finally:
        storage.close()


if __name__ == "__main__":
    sys.exit(main())
