"""Proste struktury danych współdzielone przez moduły."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass(frozen=True)
class Segment:
    carrier: str
    flight_number: str
    from_airport: str
    to_airport: str
    depart_at: str   # ISO datetime string as returned by the API
    arrive_at: str


@dataclass
class Offer:
    origin: str
    destination: str          # code as queried (may be a metro code, e.g. NYC)
    depart_date: date
    return_date: date
    price: float
    currency: str
    outbound: list[Segment] = field(default_factory=list)
    inbound: list[Segment] = field(default_factory=list)
    validating_airlines: list[str] = field(default_factory=list)
    offer_id: str = ""

    @property
    def outbound_stops(self) -> int:
        return max(len(self.outbound) - 1, 0)

    @property
    def inbound_stops(self) -> int:
        return max(len(self.inbound) - 1, 0)

    @property
    def route_key(self) -> str:
        return f"{self.origin}-{self.destination}"

    @property
    def seller(self) -> str:
        return self.validating_airlines[1] if len(self.validating_airlines) > 1 else ""

    def describe_path(self, segs: list[Segment]) -> str:
        if not segs:
            return "?"
        path = [segs[0].from_airport] + [s.to_airport for s in segs]
        carriers = "/".join(dict.fromkeys(s.carrier for s in segs if s.carrier))
        if "?" in path:
            stops = len(segs) - 1
            return f"{path[0]} → {path[-1]} ({carriers}, {stops} przes.)"
        return " → ".join(path) + f" ({carriers})"


@dataclass
class Alert:
    offer: Offer
    reason: str               # "fixed_threshold" | "history_drop" | both joined by "+"
    threshold: float | None   # fixed threshold that was beaten, if any
    median: float | None      # historical median, if history was used
    drop_pct: float | None    # percentage below median, if history was used
