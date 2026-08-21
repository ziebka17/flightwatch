import os
from datetime import date, timedelta
from unittest import mock

import pytest

from flightwatch.config import ConfigError, load_config
from flightwatch.detector import evaluate
from flightwatch.main import deliver, months_to_scan, offer_in_window, scan
from flightwatch.models import Alert, Offer, Segment
from flightwatch.notifier import render_html, render_text
from flightwatch.storage import Storage
from flightwatch.travelpayouts import TravelpayoutsClient, TravelpayoutsError, parse_offers

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(HERE, "..", "config.json")
TODAY = date(2026, 8, 21)


def sample_payload(price=950, depart="2026-10-05T06:00:00+02:00", ret="2026-10-12T16:00:00-04:00",
                   transfers=1, return_transfers=1):
    return {"success": True, "data": [
        {"origin": "GDN", "destination": "NYC", "origin_airport": "GDN", "destination_airport": "JFK",
         "price": price, "airline": "LO", "flight_number": "3812", "departure_at": depart, "return_at": ret,
         "transfers": transfers, "return_transfers": return_transfers, "duration": 900,
         "link": "/search/GDN0510NYC12101?t=abc"},
        {"origin": "GDN", "destination": "NYC", "price": "oops"},                      # uszkodzony
        {"origin": "GDN", "destination": "NYC", "price": 2400, "airline": "LH", "flight_number": "1",
         "departure_at": "2026-10-20T06:00:00+02:00", "return_at": "2026-10-27T16:00:00-04:00",
         "transfers": 0, "return_transfers": 0, "link": "/search/x"},
        {"origin": "GDN", "destination": "NYC", "price": 100, "airline": "XX",       # powrót przed wylotem
         "departure_at": "2026-10-20T06:00:00+02:00", "return_at": "2026-10-19T16:00:00-04:00"},
    ]}


def make_offer(price, dest="NYC", currency="PLN", depart=date(2026, 10, 5), ret=date(2026, 10, 12)):
    s = Segment("LO", "1", "GDN", "JFK", "2026-10-05T06:00", "")
    return Offer("GDN", dest, depart, ret, price, currency, [s], [s], ["LO"])


# ---------------------------------------------------------------- config
def test_config_loads():
    cfg = load_config(CONFIG)
    assert cfg.origin == "GDN" and cfg.months_ahead == 6 and cfg.market == "pl"
    codes = [d.code for d in cfg.destinations]
    assert "NYC" in codes and "TYO" in codes and len(codes) == len(set(codes))
    nyc = next(d for d in cfg.destinations if d.code == "NYC")
    assert nyc.region == "americas" and nyc.fixed_threshold == 1200


def test_config_rejects_bad(tmp_path):
    p = tmp_path / "c.json"
    p.write_text('{"origin": "GDN", "search": {"months_ahead": 6, "trip_length_min_days": 10, "trip_length_max_days": 5},'
                 ' "regions": {}, "history": {"drop_fraction": 0.4}, "alerts": {}, "api_budget": {}}')
    with pytest.raises(ConfigError):
        load_config(p)


# ---------------------------------------------------------------- parsing
def test_parse_offers_sorted_and_skips_malformed():
    offers = parse_offers(sample_payload(), "GDN", "NYC", "pln")
    assert [o.price for o in offers] == [950.0, 2400.0]
    o = offers[0]
    assert o.currency == "PLN"
    assert o.depart_date == date(2026, 10, 5) and o.return_date == date(2026, 10, 12)
    assert o.outbound_stops == 1 and o.inbound_stops == 1
    assert o.describe_path(o.outbound) == "GDN → JFK (LO, 1 przes.)"
    assert o.offer_id == "https://www.aviasales.com/search/GDN0510NYC12101?t=abc"
    assert offers[1].outbound_stops == 0 and offers[1].describe_path(offers[1].outbound) == "GDN → NYC (LH)"


def test_parse_offers_multi_stop():
    offers = parse_offers(sample_payload(transfers=2, return_transfers=0), "GDN", "NYC", "PLN")
    assert offers[0].outbound_stops == 2 and offers[0].inbound_stops == 0


# ---------------------------------------------------------------- months / window
def test_months_to_scan():
    cfg = load_config(CONFIG)
    assert months_to_scan(cfg, date(2026, 10, 15)) == ["2026-10", "2026-11", "2026-12", "2027-01", "2027-02", "2027-03"]


def test_offer_in_window():
    cfg = load_config(CONFIG)
    assert offer_in_window(make_offer(1, depart=TODAY + timedelta(7), ret=TODAY + timedelta(12)), cfg, TODAY)
    assert not offer_in_window(make_offer(1, depart=TODAY + timedelta(6), ret=TODAY + timedelta(13)), cfg, TODAY)
    assert not offer_in_window(make_offer(1, depart=TODAY + timedelta(30), ret=TODAY + timedelta(34)), cfg, TODAY)
    assert not offer_in_window(make_offer(1, depart=TODAY + timedelta(30), ret=TODAY + timedelta(52)), cfg, TODAY)


# ---------------------------------------------------------------- detection
def test_fixed_threshold_alert(tmp_path):
    cfg = load_config(CONFIG)
    st = Storage(tmp_path / "db.sqlite3")
    nyc = next(d for d in cfg.destinations if d.code == "NYC")
    assert evaluate(make_offer(1500), nyc, cfg, st) is None
    a = evaluate(make_offer(1100), nyc, cfg, st)
    assert a is not None and a.reason == "fixed_threshold" and a.threshold == 1200
    assert evaluate(make_offer(100, currency="EUR"), nyc, cfg, st) is None   # inna waluta -> nigdy


def test_history_drop_alert(tmp_path):
    cfg = load_config(CONFIG)
    st = Storage(tmp_path / "db.sqlite3")
    nyc = next(d for d in cfg.destinations if d.code == "NYC")
    for _ in range(cfg.history_min_samples - 1):
        st.record_offers([make_offer(3000)])
    assert evaluate(make_offer(1400), nyc, cfg, st) is None       # za mało próbek
    st.record_offers([make_offer(3000)])
    a = evaluate(make_offer(1400), nyc, cfg, st)
    assert a is not None and a.reason == "history_drop"
    assert a.median == 3000 and abs(a.drop_pct - 53.3) < 0.1
    assert evaluate(make_offer(900), nyc, cfg, st).reason == "fixed_threshold+history_drop"
    assert evaluate(make_offer(1800), nyc, cfg, st) is None       # 40% < 45%


def test_route_stats_uses_per_query_minimum(tmp_path):
    st = Storage(tmp_path / "db.sqlite3")
    st.record_offers([make_offer(1000), make_offer(5000), make_offer(9000)])
    n, med = st.route_stats("GDN", "NYC", 45)
    assert n == 1 and med == 1000


# ---------------------------------------------------------------- alerts / budget
def test_alert_cooldown_and_budget(tmp_path):
    st = Storage(tmp_path / "db.sqlite3")
    key = Storage.alert_key(make_offer(999.6))
    assert key == "GDN-NYC|2026-10-05|2026-10-12|1000"
    assert not st.recently_alerted(key, 24)
    st.record_alert(key, "fixed_threshold", 999.6)
    assert st.recently_alerted(key, 24)
    assert st.api_calls_this_month() == 0
    st.record_api_call()
    assert st.api_calls_this_month() == 1


# ---------------------------------------------------------------- client
import json as _json
from urllib.parse import parse_qs, urlparse


def _resp(status, json_body=None, text=""):
    return (status, _json.dumps(json_body) if json_body is not None else text)


def test_client_search_and_retry(monkeypatch):
    monkeypatch.setattr("flightwatch.travelpayouts.time.sleep", lambda *_: None)
    opener = mock.Mock(side_effect=[_resp(500), _resp(200, sample_payload())])
    c = TravelpayoutsClient("tok", opener=opener)
    offers = c.search_month("GDN", "NYC", "2026-10", currency="PLN")
    assert len(offers) == 2 and offers[0].price == 950.0
    url = opener.call_args.args[0]
    assert url.startswith("https://api.travelpayouts.com/aviasales/v3/prices_for_dates?")
    q = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
    assert q["departure_at"] == "2026-10" and q["one_way"] == "false"
    assert q["direct"] == "false" and q["currency"] == "pln" and q["token"] == "tok" and q["market"] == "pl"


def test_client_bad_token():
    opener = mock.Mock(return_value=_resp(401, text="unauthorized"))
    with pytest.raises(TravelpayoutsError):
        TravelpayoutsClient("tok", opener=opener).search_month("GDN", "NYC", "2026-10")


REAL_LINK = ("/search/GDN0110NYC08101?t=AF17908476001790945700001995GDNHAMCDGJFK17914776001791551100000865"
             "JFKAMSHAMGDN_b76abecd459860c85daf589de0ff18fb_55017&search_date=17082026&expected_price=2413")


def test_route_from_real_link():
    from flightwatch.travelpayouts import route_from_link
    assert route_from_link(REAL_LINK, "GDN", "JFK") == (["GDN", "HAM", "CDG", "JFK"], ["JFK", "AMS", "HAM", "GDN"])
    assert route_from_link("/search/x?t=garbage", "GDN", "JFK") == ([], [])
    payload = {"data": [{"price": 2413, "airline": "W6", "gate": "Gotogate", "origin_airport": "GDN",
                         "destination_airport": "JFK", "departure_at": "2026-10-01T09:40:00+02:00",
                         "return_at": "2026-10-08T16:40:00-04:00", "transfers": 2, "return_transfers": 2,
                         "link": REAL_LINK}]}
    o = parse_offers(payload, "GDN", "NYC", "PLN")[0]
    assert o.describe_path(o.outbound) == "GDN → HAM → CDG → JFK (W6)"
    assert o.describe_path(o.inbound) == "JFK → AMS → HAM → GDN (W6)"
    assert o.seller == "Gotogate" and o.outbound_stops == 2


# ---------------------------------------------------------------- end-to-end
def test_scan_end_to_end(tmp_path, monkeypatch):
    cfg = load_config(CONFIG)
    cfg.destinations = [d for d in cfg.destinations if d.code in ("NYC", "BKK")]
    cfg.months_ahead = 1
    st = Storage(tmp_path / "db.sqlite3")
    client = mock.Mock()
    monkeypatch.setattr("flightwatch.main.date", mock.Mock(today=lambda: date(2026, 9, 20)))

    def fake_search(origin, dest, month, **kw):
        return parse_offers(sample_payload(price=999 if dest == "NYC" else 3500), origin, dest, "PLN")
    client.search_month.side_effect = fake_search
    monkeypatch.setattr("flightwatch.main.time.sleep", lambda *_: None)

    alerts = scan(cfg, client, st)
    assert [a.offer.destination for a in alerts] == ["NYC"]
    assert st.api_calls_this_month() == 2
    assert st.cheapest_per_route(24)[0]["destination"] == "NYC"

    sent = {}
    monkeypatch.setattr("flightwatch.main.send_email", lambda alerts, *a, **k: sent.setdefault("n", len(alerts)))
    monkeypatch.setenv("FW_EMAIL_USER", "u"); monkeypatch.setenv("FW_EMAIL_PASSWORD", "p"); monkeypatch.setenv("FW_EMAIL_TO", "t")
    deliver(cfg, st, alerts, dry_run=False)
    assert sent["n"] == 1
    assert scan(cfg, client, st) == []          # cooldown -> bez duplikatu


def test_max_alerts_per_destination(tmp_path, monkeypatch):
    cfg = load_config(CONFIG)
    cfg.destinations = [d for d in cfg.destinations if d.code == "NYC"]
    cfg.months_ahead = 1
    cfg.max_alerts_per_destination = 2
    st = Storage(tmp_path / "db.sqlite3")
    monkeypatch.setattr("flightwatch.main.date", mock.Mock(today=lambda: date(2026, 9, 20)))
    monkeypatch.setattr("flightwatch.main.time.sleep", lambda *_: None)
    data = [{"price": p, "airline": "LO", "departure_at": f"2026-10-{d:02d}T06:00:00+02:00",
             "return_at": f"2026-10-{d+7:02d}T06:00:00+02:00", "transfers": 1} for p, d in ((800, 10), (700, 12), (900, 14))]
    client = mock.Mock()
    client.search_month.return_value = parse_offers({"data": data}, "GDN", "NYC", "PLN")
    alerts = scan(cfg, client, st)
    assert [a.offer.price for a in alerts] == [700.0, 800.0]


def test_budget_guard_stops_scan(tmp_path, monkeypatch):
    cfg = load_config(CONFIG)
    cfg.monthly_max_calls = 1
    st = Storage(tmp_path / "db.sqlite3")
    client = mock.Mock(); client.search_month.return_value = []
    monkeypatch.setattr("flightwatch.main.time.sleep", lambda *_: None)
    scan(cfg, client, st)
    assert client.search_month.call_count == 1


# ---------------------------------------------------------------- rendering
def test_render():
    o = make_offer(950); o.offer_id = "https://www.aviasales.com/search/x"
    a = Alert(o, "fixed_threshold+history_drop", 1200, 2800, 66.1)
    txt = render_text([a]); htm = render_html([a])
    assert "GDN -> NYC" in txt and "66% taniej" in txt and "aviasales.com/search/x" in txt
    assert "950 PLN" in htm and "66% taniej" in htm
