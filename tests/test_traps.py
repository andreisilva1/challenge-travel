"""The documents' own traps, asserted one by one against a real run.

Each test is named after what the documents do to a reader who is not paying attention, and
each one asserts the number the exercise is actually about. Where a test asserts an absence
- no rate matched, no line priced - the absence is the finding, and a later change that
"fixes" it by producing a number has broken this system rather than improved it.
"""

from __future__ import annotations

from decimal import Decimal

from domain import Confidence


def _total(costed) -> Decimal:
    assert costed.total is not None, f"{costed.line_id} was expected to be priced"
    return costed.total.amount


def _exposure(items) -> Decimal:
    return sum((i.exposure.amount for i in items if i.exposure), Decimal("0"))


# --------------------------------------------------------------- correspondence beats pack


def test_camissa_email_supersedes_the_pack(line, questions):
    """The email is older than the pack and still wins: precedence is by document class.

    Pack rates are 340.00 and 210.00; the email revises them to 375.00 and 230.00. Four
    nights each, so a system that sorted by date would be 220.00 short and say nothing.
    """
    assert _total(line("L03-1")) == Decimal("1500.00")  # 375 x 4
    assert _total(line("L03-2")) == Decimal("920.00")   # 230 x 4
    assert line("L03-1").confidence is Confidence.SUPERSEDED_BY_CORRESPONDENCE
    assert any("correspondence" in q.field for q in questions("L03-1"))


# --------------------------------------------------------------- the overlapping night


def test_09_july_falls_in_two_season_bands(line, questions):
    """Green Season ends 09 Jul and Peak Season starts 09 Jul. The party checks in that day.

    Priced at the lower band, with the difference carried as exposure and named: 270.00 on
    the Family Chalet and 220.00 on the Tented Pool Suite.
    """
    seasons = [q for q in questions("L10-1") + questions("L10-2") if q.field == "season"]
    assert len(seasons) == 2
    assert _exposure(seasons) == Decimal("490.00")
    assert all("09 Jul 27" in q.question for q in seasons)
    # 620 + 890 nightly, plus the levy as booked
    assert _total(line("L10-1")) == Decimal("1622.00")
    assert _total(line("L10-2")) == Decimal("1300.00")


# --------------------------------------------------------------- the 2025 tariff


def test_helicopter_is_priced_from_an_expired_tariff(line, result):
    """395.00 is a real published rate - for 2025. The pack says so and says to reconfirm.

    The number is usable, so it is used; the confidence is EXPIRED, so the whole 3,950.00
    lands in exposure rather than in firm. A system that reported this as contracted would
    be confidently wrong about the largest single uncertainty in the quotation.
    """
    assert _total(line("L15")) == Decimal("1975.00")  # 395 x 5 pax
    assert _total(line("L17")) == Decimal("1975.00")
    assert line("L15").confidence is Confidence.EXPIRED
    assert result.totals.exposure_line_count == 2


# --------------------------------------------------------------- air


def test_three_air_sectors_have_no_fare_anywhere(line, questions, result):
    """Section 8: airfares are not carried in this pack. So none is invented.

    The temptation is to reach for the helicopter rate two sections up. These lines stay
    unpriced, and unpriced is counted separately from zero.
    """
    for line_id in ("L08", "L14", "L18"):
        assert line(line_id).total is None
        assert line(line_id).confidence is Confidence.UNRESOLVED
        assert any(q.field == "air_not_carried" for q in questions(line_id))
    assert result.totals.unpriced_line_count >= 3


# --------------------------------------------------------------- the villa that is not sold


def test_beach_villa_grande_is_not_a_beach_villa(line, questions):
    """Ilha Azul sells a Beach Villa. The quotation books a Beach Villa Grande.

    difflib scores those two at 0.759, above any usable fuzzy floor, so the only defence is
    to require room types to match exactly. The Beach Villa prices; the Grande does not, and
    three nights stay open.
    """
    assert _total(line("L16-1")) == Decimal("2340.00")  # 780 x 3 nights
    assert line("L16-2").total is None
    assert "Grande" in line("L16-2").description
    assert any(q.severity == "blocking" for q in questions("L16-2"))


# --------------------------------------------------------------- occupancy


def test_five_guests_in_two_suites_on_a_per_person_sharing_rate(line, questions):
    """890.00 per person sharing, five people, three nights: 13,350.00 plus the levy.

    "Max 2 adults" would cap two suites at four guests. Note 4a releases the third bed and
    the quotation books a Triple, so the constraint is reconciled by the pack itself and the
    finding is informational, not blocking.
    """
    assert _total(line("L12-1")) == Decimal("13665.00")  # 5 x 3 x 890 + levy 315
    capacity = [q for q in questions("L12-1") if q.field == "capacity_constraint"]
    assert capacity and all(q.severity == "informational" for q in capacity)
    assert any("4a" in q.question for q in capacity)


# --------------------------------------------------------------- compulsory levies


def test_conservation_levies_are_charged_as_quoted_and_the_gap_is_named(questions):
    """Marula books 2 levies and Kudu 3, for a party of 5. Both clauses say compulsory.

    The charge stays at what was sold and the shortfall is exposure: 168.00 and 210.00.
    Raising the charge silently would be this system deciding the client owes more.
    """
    marula = [q for q in questions("L10-1") if q.field == "conservation_levy"]
    kudu = [q for q in questions("L12-1") if q.field == "conservation_levy"]
    assert _exposure(marula) == Decimal("168.00")   # 28 x 3 pax x 2 nights
    assert _exposure(kudu) == Decimal("210.00")     # 35 x 2 pax x 3 nights


def test_the_levy_alias_is_recognised(facts):
    """Marula's line says "Consv Levy" and Kudu's says "Conservation Levy". Same clause."""
    labels = {m.label for l in facts.booked_lines for m in l.modifiers}
    assert {"Consv Levy", "Conservation Levy"} <= labels


# --------------------------------------------------------------- the trailer


def test_trailer_supplement_is_booked_for_a_party_below_its_own_threshold(line, questions):
    """The clause triggers at 6 pax or on luggage volume. The party is 5.

    Charged as booked, on both transfers, and questioned: 65.00 x 2 turns on an answer the
    documents do not contain.
    """
    for line_id in ("L02", "L07"):
        assert _total(line(line_id)) == Decimal("275.00")  # 210 transfer + 65 trailer
        flagged = [q for q in questions(line_id) if q.field == "trailer_supplement"]
        assert flagged and flagged[0].severity == "blocking"
        assert "party of 5" in flagged[0].question


# --------------------------------------------------------------- routing


def test_transfer_label_contradicts_its_own_pickup(line, questions):
    """Labelled "HDS to Kudu Sands Lodges"; picked up at Marula River Lodge.

    The label matches a pack routing at 1.000 and would have priced cleanly at 165.00 per
    person. The contradiction is inside the quotation, between two fields of one line.
    """
    assert line("L11").total is None
    routing = [q for q in questions("L11") if q.field == "routing"]
    assert routing and "Marula River Lodge" in routing[0].question


def test_transfer_label_reads_backwards(line, questions):
    """Labelled "KMIA to Kudu Sands Lodges"; the movement starts at Kudu Ridge."""
    assert line("L13").total is None
    routing = [q for q in questions("L13") if q.field == "routing"]
    assert routing and "origin" in routing[0].question


def test_a_transfer_written_in_another_vocabulary_is_not_flagged(questions):
    """"Transfer CIA to Zone 1" against a pickup of "CPT" shares no words with the routing.

    Nothing can be concluded from that, so nothing is. A check that fires on unfamiliar
    wording is one people learn to ignore, which costs more than it catches.
    """
    assert not [q for q in questions("L02") if q.field == "routing"]


# --------------------------------------------------------------- the honest zero


def test_the_complimentary_transfer_is_a_contracted_zero(line, questions):
    """The stay is exactly two nights and the clause's threshold is two nights.

    This is the one 0.00 in the output and it is contracted, quoted and cited. Every other
    unknown is unpriced. In a system that used 0 as a fallback these two would look alike.
    """
    costed = line("L09")
    assert costed.total is not None and costed.total.amount == Decimal("0")
    assert costed.confidence is Confidence.CONTRACTED
    assert "two nights or more" in (costed.confidence_reason or "")
    assert not [q for q in questions("L09") if q.severity == "blocking"]


# --------------------------------------------------------------- gate fees


def test_gate_fees_are_charged_and_their_applicability_is_questioned(questions):
    """The amounts are contracted; whether they are levied is decided on the day.

    Both fees sit on transfers that could not be priced at all, so neither is billed and
    both become exposure - plus the per-pax shortfall, because the pack prices the Entrance
    Fee per pax and the quotation books one.
    """
    gate = [q for q in questions("L11") + questions("L13") if q.field in ("gate_fees", "modifier")]
    assert _exposure(gate) == Decimal("205.00")  # 40 + 40 + 25 + (25 x 4)
    assert any("confirmed by the transfer operator" in q.question.lower()
               or "levied at the gate" in q.question for q in gate)


# --------------------------------------------------------------- meet and greet


def test_meet_and_greet_is_per_person_per_movement(line):
    """45.00 per person per movement, five people, two movements: 450.00.

    Reading the unit as per group would produce 90.00 and look entirely reasonable.
    """
    assert _total(line("L01")) == Decimal("225.00")
    assert _total(line("L19")) == Decimal("225.00")


# --------------------------------------------------------------- the shape of the answer


def test_the_output_never_states_a_single_total(result):
    """Three numbers, and no field that adds them up.

    firm is what the documents contract, exposure is what may still land, unpriced is what
    has no defensible number at all. Collapsing them is the "clean, confident, wrong" answer
    the brief calls worse than useless.
    """
    assert not hasattr(result.totals, "total")
    assert result.totals.firm.amount > 0
    assert result.totals.exposure.amount > 0
    assert result.totals.unpriced_line_count > 0
    assert len(result.totals.unpriced_reasons) == result.totals.unpriced_line_count


def test_every_booked_line_is_accounted_for_exactly_once(facts, result):
    booked = sorted(l.line_id for l in facts.booked_lines)
    costed = sorted(l.line_id for l in result.costed_lines)
    assert booked == costed
    totals = result.totals
    assert (
        totals.firm_line_count + totals.exposure_line_count + totals.unpriced_line_count
        == len(costed)
    )
