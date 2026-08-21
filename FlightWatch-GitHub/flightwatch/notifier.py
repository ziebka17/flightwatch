"""Powiadomienia mailowe (Gmail SMTP z hasłem aplikacji lub dowolny serwer SMTP)."""
from __future__ import annotations

import html
import logging
import smtplib
from email.message import EmailMessage

from .models import Alert

log = logging.getLogger(__name__)


def _fmt_money(v: float, cur: str) -> str:
    return f"{v:,.0f} {cur}".replace(",", " ")


def _booking_link(a: Alert) -> str:
    o = a.offer
    if o.offer_id.startswith("http"):
        return o.offer_id  # bezpośredni link do wyszukiwania w Aviasales
    # Link do Google Flights; obsługuje kody miast (NYC, TYO...) i pokaże przesiadki.
    return (f"https://www.google.com/travel/flights?q=Flights%20from%20{o.origin}%20to%20{o.destination}"
            f"%20on%20{o.depart_date}%20returning%20{o.return_date}")


def _reasons(a: Alert, bold: bool = False) -> list[str]:
    o = a.offer
    why = []
    if a.threshold is not None:
        why.append(f"poniżej progu {_fmt_money(a.threshold, o.currency)}")
    if a.median is not None and "history_drop" in a.reason:
        pct = f"{a.drop_pct:.0f}% taniej"
        if bold:
            pct = f"<b>{pct}</b>"
        why.append(f"{pct} niż zwykle (~{_fmt_money(a.median, o.currency)})")
    return why


def render_text(alerts: list[Alert]) -> str:
    lines = ["FlightWatch znalazł nietypowo tanie bilety:\n"]
    for a in alerts:
        o = a.offer
        lines.append(f"{o.origin} -> {o.destination}  {o.depart_date} .. {o.return_date}  "
                     f"{_fmt_money(o.price, o.currency)}")
        lines.append(f"   tam:  {o.describe_path(o.outbound)}  ({o.outbound_stops} przesiadka/-ki)")
        lines.append(f"   pow.: {o.describe_path(o.inbound)}  ({o.inbound_stops} przesiadka/-ki)")
        if o.seller:
            lines.append(f"   sprzedawca: {o.seller}")
        lines.append(f"   dlaczego: {'; '.join(_reasons(a)) or a.reason}")
        lines.append(f"   sprawdź: {_booking_link(a)}\n")
    lines.append("Błędne taryfy znikają szybko – zweryfikuj cenę na stronie linii, zanim się ucieszysz.")
    return "\n".join(lines)


def render_html(alerts: list[Alert]) -> str:
    rows = []
    for a in alerts:
        o = a.offer
        rows.append(
            "<tr>"
            f"<td><b>{html.escape(o.origin)} → {html.escape(o.destination)}</b></td>"
            f"<td>{o.depart_date} – {o.return_date}</td>"
            f"<td style='color:#0a7d2b;font-weight:bold'>{_fmt_money(o.price, o.currency)}</td>"
            f"<td>{html.escape(o.describe_path(o.outbound))}<br>{html.escape(o.describe_path(o.inbound))}</td>"
            f"<td>{'; '.join(_reasons(a, bold=True)) or html.escape(a.reason)}</td>"
            f"<td><a href='{_booking_link(a)}'>sprawdź cenę</a></td>"
            "</tr>")
    return (
        "<html><body style='font-family:Arial,sans-serif'>"
        "<h2>✈️ FlightWatch: nietypowo tanie bilety</h2>"
        "<table border='1' cellpadding='6' style='border-collapse:collapse'>"
        "<tr><th>Trasa</th><th>Daty</th><th>Cena</th><th>Połączenie</th><th>Dlaczego</th><th>Link</th></tr>"
        + "".join(rows) +
        "</table><p style='color:#666'>Błędne taryfy znikają w ciągu godzin. Sprawdź cenę na stronie "
        "linii i kupuj szybko, jeśli nadal obowiązuje.</p></body></html>")


def send_email(alerts: list[Alert], smtp_host: str, smtp_port: int,
               user: str, password: str, to: str) -> None:
    if not alerts:
        return
    cheapest = min(a.offer.price for a in alerts)
    cur = alerts[0].offer.currency
    msg = EmailMessage()
    msg["Subject"] = f"✈️ FlightWatch: {len(alerts)} tania/-e oferta/-y z GDN (od {_fmt_money(cheapest, cur)})"
    msg["From"] = user
    msg["To"] = to
    msg.set_content(render_text(alerts))
    msg.add_alternative(render_html(alerts), subtype="html")

    with smtplib.SMTP(smtp_host, smtp_port, timeout=30) as smtp:
        smtp.ehlo()
        smtp.starttls()
        smtp.ehlo()
        smtp.login(user, password)
        smtp.send_message(msg)
    log.info("wysłano maila do %s z %d alertem/-ami", to, len(alerts))
