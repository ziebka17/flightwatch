"""Decyduje, czy oferta wygląda na błędną taryfę / duży spadek ceny."""
from __future__ import annotations

from .config import Config, Destination
from .models import Alert, Offer
from .storage import Storage


def evaluate(offer: Offer, dest: Destination, cfg: Config, storage: Storage) -> Alert | None:
    """Zwraca Alert, jeśli oferta jest poniżej stałego progu i/lub poniżej
    historycznej mediany o skonfigurowany ułamek. W przeciwnym razie None.

    WAŻNE: wywołuj PRZED zapisaniem oferty, żeby nie liczyła się do własnej historii.
    """
    if offer.currency and offer.currency.upper() != cfg.currency.upper():
        # Nigdy nie porównuj cen w różnych walutach.
        return None

    reasons: list[str] = []
    threshold_hit: float | None = None
    median: float | None = None
    drop_pct: float | None = None

    if offer.price < dest.fixed_threshold:
        reasons.append("fixed_threshold")
        threshold_hit = dest.fixed_threshold

    n, med = storage.route_stats(offer.origin, offer.destination, cfg.history_lookback_days)
    if med is not None and n >= cfg.history_min_samples:
        median = med
        drop_pct = (1 - offer.price / med) * 100 if med > 0 else 0.0
        if offer.price < med * (1 - cfg.history_drop_fraction):
            reasons.append("history_drop")

    if not reasons:
        return None
    return Alert(offer=offer, reason="+".join(reasons), threshold=threshold_hit,
                 median=median, drop_pct=drop_pct)
