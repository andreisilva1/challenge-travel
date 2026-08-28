"""Unit algebra: what a rate is charged per, and how often.

A rate is a number over a denominator over a period. Multiply by the wrong denominator and
the total is wrong by a factor of five on this quotation; multiply by the wrong period and
it is wrong by a factor of four. Neither error looks like an error in the output.

So an unparsed unit is never a guess. It is None, and the line goes to needs_review with
the raw text the supplier wrote, which is a question a supplier can answer.
"""

from __future__ import annotations

import re

from domain import Denominator, Period, Unit

# Longest phrase first: "person sharing" must be read before "person", or every
# per-person-sharing rate silently becomes a per-person rate.
_DENOMINATORS: list[tuple[str, Denominator]] = [
    ("person sharing", Denominator.PERSON_SHARING),
    ("pax sharing", Denominator.PERSON_SHARING),
    ("person", Denominator.PERSON),
    ("pax", Denominator.PERSON),          # the pack uses both words for the same thing
    ("adult", Denominator.PERSON),
    ("chalet", Denominator.CHALET),
    ("suite", Denominator.SUITE),
    ("villa", Denominator.VILLA),
    ("vehicle", Denominator.VEHICLE),
    ("group", Denominator.GROUP),
    ("room", Denominator.ROOM),
    ("unit", Denominator.ROOM),
]

_PERIODS: list[tuple[str, Period]] = [
    ("night", Period.NIGHT),
    ("one way", Period.ONE_WAY),
    ("per way", Period.ONE_WAY),
    ("movement", Period.MOVEMENT),
    ("entry", Period.ENTRY),
    ("transfer", Period.TRANSFER),
]


def parse_unit(raw: str | None) -> Unit | None:
    """'per person sharing per night' -> PERSON_SHARING / NIGHT. None when unreadable.

    A missing period word is not a missing value: "per person" and "per vehicle" are
    complete units in this pack, and they mean the charge happens once for the service.
    That reading is Period.FLAT and it is stated here rather than defaulted silently
    somewhere downstream.
    """
    if not raw:
        return None
    text = re.sub(r"\s+", " ", raw.replace("–", "-").replace("—", "-")).lower()

    denominator = next((d for phrase, d in _DENOMINATORS if phrase in text), None)
    if denominator is None:
        return None

    period = next((p for phrase, p in _PERIODS if phrase in text), Period.FLAT)
    return Unit(denominator=denominator, period=period, raw=raw)


def quantity_for(unit: Unit, *, travellers: int, rooms: int, nights: int | None) -> int | None:
    """How many of `unit.denominator` this line is charged for.

    PERSON_SHARING returns the head count deliberately. A per-person-sharing rate is a per
    person rate whose *price* assumes two to a room; the multiplier is still people. The
    occupancy question it raises belongs to rules.py, which can cite note 4a, not here.
    """
    if unit.denominator in (Denominator.PERSON, Denominator.PERSON_SHARING):
        return travellers
    if unit.denominator in (
        Denominator.ROOM,
        Denominator.CHALET,
        Denominator.SUITE,
        Denominator.VILLA,
    ):
        return rooms
    if unit.denominator is Denominator.GROUP:
        return 1
    if unit.denominator is Denominator.VEHICLE:
        # Vehicles are not derivable from head count: 5 pax may be one vehicle or two, and
        # the pack's own "Max 7 pax per vehicle" is the constraint, not the answer. The
        # quotation books one transfer line per movement, so one line is one vehicle.
        return 1
    return None


def occurrences_for(unit: Unit, *, nights: int | None) -> int | None:
    """How many times the period repeats on this line.

    NIGHT needs a stay length. If the line has no In:/Out: dates there is no honest number
    here, and returning 1 would quietly price a four-night stay as one night.
    """
    if unit.period is Period.NIGHT:
        return nights
    if unit.period in (Period.ONE_WAY, Period.MOVEMENT, Period.ENTRY, Period.TRANSFER):
        # The quotation states each movement as its own line, so a line is one occurrence.
        return 1
    if unit.period is Period.FLAT:
        return 1
    return None


def describe(unit: Unit, quantity: int, occurrences: int) -> str:
    """The arithmetic in words, for the reviewer who will not read this file."""
    noun = unit.denominator.value.replace("_", " ")
    if quantity != 1:
        noun += "s"
    if unit.period is Period.FLAT:
        return f"{quantity} {noun}"
    period = unit.period.value.replace("_", " ")
    if occurrences != 1:
        period += "s"
    return f"{quantity} {noun} x {occurrences} {period}"
