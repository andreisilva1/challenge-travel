"""Money, confidence and units: the three places an error becomes invisible.

These are the smallest tests in the suite and the ones most worth having. A float in a
currency amount, a silently mixed currency, or a unit read one word too early does not
produce an error message - it produces a total that is wrong by a fraction of a cent, or by
a factor of five, and looks exactly like a total that is right.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from costing.units import describe, occurrences_for, parse_unit, quantity_for
from domain import CURRENCY, Confidence, Denominator, Money, Period, weakest


# --------------------------------------------------------------------------- money


def test_a_float_can_never_enter_an_amount():
    """0.1 + 0.2 is not 0.3, and a quotation is not the place to discover that."""
    with pytest.raises(TypeError):
        Money(amount=340.00, currency=CURRENCY)


def test_decimal_and_string_amounts_are_both_exact():
    assert Money(amount=Decimal("340.00"), currency=CURRENCY).amount == Decimal("340.00")
    assert Money(amount="340.00", currency=CURRENCY).amount == Decimal("340.00")


def test_currencies_are_never_added():
    """The pack is USD throughout, so this can only fire if something changed upstream.

    Which is exactly when it should: a silent conversion at an implied rate is the kind of
    invented money that no reviewer would ever spot in a total.
    """
    with pytest.raises(ValueError):
        Money(amount=Decimal("1"), currency="USD") + Money(amount=Decimal("1"), currency="ZAR")


def test_times_keeps_the_currency_and_the_precision():
    assert (Money(amount=Decimal("28.00"), currency=CURRENCY).times(5)).amount == Decimal("140.00")


# --------------------------------------------------------------------------- confidence


def test_a_line_is_only_as_strong_as_its_weakest_fact():
    assert weakest(Confidence.CONTRACTED, Confidence.EXPIRED) is Confidence.EXPIRED
    assert weakest(Confidence.ASSUMED, Confidence.CONTRACTED) is Confidence.ASSUMED
    assert Confidence.CONTRACTED.rank > Confidence.SUPERSEDED_BY_CORRESPONDENCE.rank
    assert Confidence.UNRESOLVED.rank == min(c.rank for c in Confidence)


# --------------------------------------------------------------------------- units


def test_person_sharing_is_read_before_person():
    """The vocabulary is ordered longest-first, and this is why.

    "per person sharing per night" contains "per person". Matched in the wrong order, every
    per-person-sharing rate in the pack becomes a per-person rate - same multiplier here,
    but a different commercial meaning and a different answer to the occupancy question.
    """
    unit = parse_unit("per person sharing per night")
    assert unit.denominator is Denominator.PERSON_SHARING
    assert unit.period is Period.NIGHT


@pytest.mark.parametrize(
    "raw, denominator, period",
    [
        ("per room per night", Denominator.ROOM, Period.NIGHT),
        ("per chalet per night", Denominator.CHALET, Period.NIGHT),
        ("per person per movement", Denominator.PERSON, Period.MOVEMENT),
        ("per vehicle one way", Denominator.VEHICLE, Period.ONE_WAY),
        ("per person per way", Denominator.PERSON, Period.ONE_WAY),
        ("per group per entry", Denominator.GROUP, Period.ENTRY),
        ("per pax per entry", Denominator.PERSON, Period.ENTRY),
        ("per vehicle", Denominator.VEHICLE, Period.FLAT),
        ("per person", Denominator.PERSON, Period.FLAT),
    ],
)
def test_every_unit_the_pack_actually_uses(raw, denominator, period):
    unit = parse_unit(raw)
    assert (unit.denominator, unit.period) == (denominator, period)


def test_an_unreadable_unit_is_none_and_never_a_guess():
    """None sends the line to needs_review. A default would send it to a client."""
    assert parse_unit("per fortnight, negotiable") is None
    assert parse_unit("") is None
    assert parse_unit(None) is None


def test_quantity_follows_the_denominator_not_the_line():
    unit = parse_unit("per person sharing per night")
    assert quantity_for(unit, travellers=5, rooms=2, nights=3) == 5
    room = parse_unit("per room per night")
    assert quantity_for(room, travellers=5, rooms=2, nights=3) == 2
    group = parse_unit("per group per entry")
    assert quantity_for(group, travellers=5, rooms=2, nights=3) == 1


def test_a_nightly_rate_without_a_stay_length_has_no_honest_answer():
    """Returning 1 would price a four-night stay as one night and never say so."""
    assert occurrences_for(parse_unit("per room per night"), nights=None) is None
    assert occurrences_for(parse_unit("per room per night"), nights=4) == 4
    assert occurrences_for(parse_unit("per vehicle one way"), nights=None) == 1


def test_the_arithmetic_reads_as_english():
    assert describe(parse_unit("per person per night"), 5, 3) == "5 persons x 3 nights"
    assert describe(parse_unit("per vehicle"), 1, 1) == "1 vehicle"
