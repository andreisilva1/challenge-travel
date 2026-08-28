"""Matching, and the four ways it is allowed to refuse.

The thresholds in resolve_rate.py were set by measuring this pack rather than by taste, so
these tests assert the measurements. If a change moves one of them, the number in the
comment beside the constant is the thing to argue with.
"""

from __future__ import annotations

from datetime import date


from costing.resolve_rate import (
    CONTAINMENT,
    FLOOR,
    MARGIN,
    find_override,
    match_accommodation,
    match_service,
    rate_for_night,
    score,
)
from domain import BookedLine, Confidence, SourceRef


def booked(**kwargs) -> BookedLine:
    """A line with only the fields matching cares about."""
    base = dict(
        line_id="T01",
        region=None,
        service_type="transfer",
        description="",
        date_start=date(2027, 7, 5),
        source=SourceRef(document="test", page=1, quote="test"),
    )
    return BookedLine(**{**base, **kwargs})


# --------------------------------------------------------------------------- scoring


def test_containment_rescues_a_short_description_without_outranking_a_literal_match():
    """"Helicopter Transfer" scores 0.469 against the pack's much longer product name.

    Every word of the booked description is inside the product, which is the real evidence;
    the low ratio is an artefact of the length difference. The rescue is capped below the
    scores that genuine near-identical matches reach, so it can never win against one.
    """
    long_product = "Helicopter transfer VNX (Vilanculos) - Benguerra island lodges"
    assert score("Helicopter Transfer", long_product) == CONTAINMENT
    assert CONTAINMENT < 0.98  # the two CIA transfers score 0.979 and 0.875 against each other


def test_containment_never_flattens_two_products_that_contain_each_other():
    """"Transfer CIA to Zone 1, 06H00-21H00" and "...21H00-06H00" differ by 75.00.

    They contain each other's words exactly. If containment applied above the floor, the
    day rate and the night rate would become indistinguishable.
    """
    day = "Transfer CIA to Zone 1, 06H00-21H00, with Guide"
    night = "Transfer CIA to Zone 1, 21H00-06H00, with Guide"
    assert score(day, night) > FLOOR
    assert score(day, night) != CONTAINMENT


# --------------------------------------------------------------------------- rooms


def test_a_room_type_matches_exactly_or_not_at_all(facts):
    """The single strictest rule in the system, and the reason it exists.

    "Beach Villa Grande" scores 0.759 against "Beach Villa" - above any fuzzy floor that is
    still useful - and would charge three nights at 780.00 for a villa Ilha Azul has never
    quoted.
    """
    grande = booked(
        service_type="accommodation",
        description="1 x Beach Villa Grande",
        supplier_or_property="Ilha Azul Beach Lodge",
        room_type="Beach Villa Grande",
    )
    outcome = match_accommodation(grande, facts.rates)
    assert outcome.winner is None
    assert [c.product for c in outcome.rejected][:1] == ["Beach Villa"]
    assert outcome.rejected[0].score > FLOOR  # it would have matched on any fuzzy rule


def test_the_room_that_is_listed_does_match(facts):
    villa = booked(
        service_type="accommodation",
        description="1 x Beach Villa",
        supplier_or_property="Ilha Azul Beach Lodge",
        room_type="Beach Villa",
    )
    outcome = match_accommodation(villa, facts.rates)
    assert outcome.winner is not None
    assert "exactly" in outcome.rule


def test_matching_is_scoped_to_the_property_the_quotation_names(facts):
    """Without scoping, "Family Suite" competes with "Luxury Suite" across four properties."""
    elsewhere = booked(
        service_type="accommodation",
        description="1 x Luxury Suite",
        supplier_or_property="The Camissa Boutique Hotel",
        room_type="Luxury Suite",
    )
    assert match_accommodation(elsewhere, facts.rates).winner is None


# --------------------------------------------------------------------------- services


def test_a_service_the_pack_does_not_sell_is_below_the_floor(facts):
    """"Zambezi Air" scores 0.364 against its nearest product in the pack, which is a villa."""
    outcome = match_service(booked(service_type="flight", description="Zambezi Air"), facts.rates)
    assert outcome.winner is None
    assert "floor" in outcome.rule
    assert outcome.rejected[0].score < FLOOR


def test_a_weak_but_unambiguous_match_is_accepted(facts):
    """"One Way Assistance" scores only 0.720 - and clears its runner-up by 0.253.

    Absolute score is a poor signal on this pack. The gap is the evidence.
    """
    outcome = match_service(booked(service_type="meet_greet", description="One Way Assistance"), facts.rates)
    assert outcome.winner is not None
    assert outcome.rejected[0].score < 0.720 - MARGIN


def test_losing_candidates_are_output_even_when_a_winner_is_found(facts):
    """Choosing a rate is a commercial decision, so the alternatives are part of the answer."""
    outcome = match_service(booked(description="Custom Private Tour"), facts.rates)
    assert outcome.winner is not None
    assert outcome.rejected and all(c.source.document for c in outcome.rejected)


# --------------------------------------------------------------------------- precedence


def test_correspondence_wins_although_it_is_the_older_document(facts):
    """The email is dated 12 Aug 2026; the pack was compiled 14 Aug 2026.

    Precedence is by document class, per pack section 9. Any rule that sorted by recency
    would reinstate the superseded rates and be 220.00 short with nothing to show for it.
    """
    line = booked(
        service_type="accommodation",
        description="1 x Family Suite",
        supplier_or_property="The Camissa Boutique Hotel",
        room_type="Family Suite",
    )
    override = find_override(line, facts.overrides)
    assert override is not None
    assert override.price.amount > next(
        r.price.amount for r in facts.rates if r.product == "Family Suite"
    )
    assert override.sent_at < date(2026, 8, 14)


def test_no_override_exists_for_a_supplier_that_did_not_write(facts):
    line = booked(
        service_type="accommodation",
        description="1 x Luxury Suite",
        supplier_or_property="Kudu Ridge Private Game Reserve",
        room_type="Luxury Suite",
    )
    assert find_override(line, facts.overrides) is None


# --------------------------------------------------------------------------- validity


def test_a_night_inside_two_season_bands_returns_both(facts):
    """The pack prints 09 July as the end of Green Season and the start of Peak Season.

    Returning one of them would be the invisible assumption the brief warns about, so both
    come back and the caller has to decide in the open.
    """
    chalet = next(r for r in facts.rates if r.product == "Family Chalet")
    found, confidence, note = rate_for_night(facts.rates, chalet.rate_id, date(2027, 7, 9))
    assert len(found) == 2
    assert confidence is Confidence.ASSUMED
    assert "Green Season" in note and "Peak Season" in note


def test_an_unambiguous_night_returns_one_rate(facts):
    chalet = next(r for r in facts.rates if r.product == "Family Chalet")
    found, confidence, note = rate_for_night(facts.rates, chalet.rate_id, date(2027, 7, 10))
    assert len(found) == 1 and confidence is Confidence.CONTRACTED and note is None


def test_a_rate_outside_its_validity_falls_back_to_the_carried_forward_tariff(facts):
    """The pack says what it did when a supplier had not released 2027 rates.

    So the number is usable - and EXPIRED, which is what keeps it out of the firm total.
    """
    helicopter = next(r for r in facts.rates if "Helicopter" in r.product)
    found, confidence, note = rate_for_night(facts.rates, helicopter.rate_id, date(2027, 7, 14))
    assert found and confidence is Confidence.EXPIRED and note
