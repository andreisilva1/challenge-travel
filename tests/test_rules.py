"""Each rule, fired and declined, against the clauses actually in the pack.

A rule that declines is tested as carefully as a rule that charges. Half of what this
system is for is not firing: not charging a levy on a transfer, not zeroing a line because
some other supplier offers a complimentary transfer, not blocking a booking that a second
clause of the same pack has already reconciled.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal


from domain import BookedLine, Confidence, Modifier, RuleKey, SourceRef
from rules import RULES, Context, conservation_levy, gate_fees

SOURCE = SourceRef(document="test", page=1, quote="test")


def booked(**kwargs) -> BookedLine:
    base = dict(
        line_id="T01",
        region=None,
        service_type="accommodation",
        description="",
        date_start=date(2027, 7, 9),
        source=SOURCE,
    )
    return BookedLine(**{**base, **kwargs})


def modifier(label: str, quantity: int, hint: str | None = None) -> Modifier:
    return Modifier(label=label, quantity=quantity, denominator_hint=hint, source=SOURCE)


def context(**kwargs) -> Context:
    base = dict(travellers=5, nights=2, rooms=1, unit_price=None, line_total=None,
                supplier_of_clause=None, other_conditions=[])
    return Context(**{**base, **kwargs})


def clause_for(facts, key: RuleKey, contains: str | None = None):
    found = [c for c in facts.conditions if c.rule_key is key]
    if contains:
        found = [c for c in found if contains in c.quote]
    assert found, f"the pack has no {key.value} clause containing {contains!r}"
    return found[0]


# --------------------------------------------------------------------------- coverage


def test_every_rule_key_that_can_change_money_has_a_function():
    """A clause with no function is a clause that silently does nothing.

    Two keys are absent on purpose. UNCLASSIFIED means two anchors tied and the engine must
    ask rather than act; NO_RATE_REQUOTE is a policy the engine cites when it finds no rate
    at all, which is an absence only the engine can observe.
    """
    unhandled = set(RuleKey) - set(RULES) - {RuleKey.UNCLASSIFIED, RuleKey.NO_RATE_REQUOTE}
    assert unhandled == set()


def test_a_rule_that_does_not_fire_says_why(facts):
    """There is no way to return "nothing happened" without accounting for it."""
    levy = clause_for(facts, RuleKey.CONSERVATION_LEVY)
    outcome = conservation_levy(booked(service_type="transfer"), levy, context())
    assert outcome.charges == () and outcome.flags == ()
    assert outcome.not_applicable_because


# --------------------------------------------------------------------------- levy


def test_the_levy_charges_what_was_sold_and_exposes_the_rest(facts):
    marula = clause_for(facts, RuleKey.CONSERVATION_LEVY, "28.00")
    line = booked(supplier_or_property="Marula River Lodge",
                  modifiers=[modifier("Consv Levy", 2, "compulsory pax")])
    outcome = conservation_levy(line, marula, context(supplier_of_clause="Marula River Lodge"))

    assert outcome.charges[0].amount.amount == Decimal("112.00")   # 28 x 2 quoted x 2 nights
    assert outcome.flags[0].exposure.amount == Decimal("168.00")   # 28 x 3 missing x 2 nights
    assert outcome.charges[0].confidence is Confidence.CONTRACTED


def test_the_levy_is_silent_when_the_booked_count_already_covers_everyone(facts):
    marula = clause_for(facts, RuleKey.CONSERVATION_LEVY, "28.00")
    line = booked(supplier_or_property="Marula River Lodge",
                  modifiers=[modifier("Consv Levy", 5, "compulsory pax")])
    outcome = conservation_levy(line, marula, context(supplier_of_clause="Marula River Lodge"))
    assert outcome.charges[0].amount.amount == Decimal("280.00")
    assert outcome.flags == ()


def test_one_suppliers_levy_never_reaches_another_suppliers_stay(facts):
    """Marula charges 28.00 and Kudu 35.00. Applying either to the wrong lodge is invented
    money that would look completely ordinary in a total."""
    kudu = clause_for(facts, RuleKey.CONSERVATION_LEVY, "35.00")
    line = booked(supplier_or_property="Marula River Lodge",
                  modifiers=[modifier("Consv Levy", 2, "compulsory pax")])
    outcome = conservation_levy(line, kudu, context(supplier_of_clause="Kudu Ridge Private Game Reserve"))
    assert outcome.not_applicable_because


# --------------------------------------------------------------------------- gate fees


def test_a_gate_fee_only_fires_on_the_fee_the_quotation_booked(facts):
    """Section 5 names two fees in one sentence, at 40.00 and 25.00.

    They are separate conditions so that a rule answers for the one on the line rather than
    for whichever amount the sentence happened to state first.
    """
    vehicle = clause_for(facts, RuleKey.GATE_FEES, "40.00")
    line = booked(service_type="transfer",
                  modifiers=[modifier("Vehicle Entrance Fee", 1, "per group")])

    fired = gate_fees(line, vehicle, context())
    assert fired.charges[0].amount.amount == Decimal("40.00")

    bare = gate_fees(booked(service_type="transfer"), vehicle, context())
    assert bare.not_applicable_because


def test_a_per_pax_gate_fee_exposes_the_difference_it_did_not_charge(facts):
    per_pax = [c for c in facts.conditions
               if c.rule_key is RuleKey.GATE_FEES and c.label == "Entrance Fee"]
    assert per_pax, "the pack states an Entrance Fee of 25.00 per pax per entry"
    line = booked(service_type="transfer", modifiers=[modifier("Entrance Fee", 1, "per pax")])
    outcome = gate_fees(line, per_pax[0], context())

    assert outcome.charges[0].amount.amount == Decimal("25.00")
    shortfall = [f for f in outcome.flags if f.exposure]
    assert shortfall and shortfall[0].exposure.amount == Decimal("100.00")  # 25 x 4


# --------------------------------------------------------------------------- the zero


def test_the_complimentary_transfer_zeroes_only_at_the_threshold(facts):
    """Two nights or more. The Marula stay is exactly two, so the zero is contracted.

    One night short and the same clause produces a question instead - which is the whole
    difference between a zero that is a price and a zero that is a missing number.
    """
    clause = clause_for(facts, RuleKey.COMPLIMENTARY_TRANSFER)
    rule = RULES[RuleKey.COMPLIMENTARY_TRANSFER]
    line = booked(service_type="transfer", description="Complimentary scheduled transfer",
                  dropoff="Marula River Lodge")

    at_threshold = rule(line, clause, context(nights_at_destination=2))
    assert at_threshold.charges[0].amount.amount == Decimal("0")
    assert at_threshold.charges[0].replaces_line_total
    assert at_threshold.charges[0].confidence is Confidence.CONTRACTED

    below = rule(line, clause, context(nights_at_destination=1))
    assert below.charges == () and below.flags[0].severity == "blocking"

    unknown = rule(line, clause, context(nights_at_destination=None))
    assert unknown.not_applicable_because


def test_the_zeroing_rule_ignores_a_transfer_that_is_not_the_complimentary_one(facts):
    """The clause names a specific arrangement. Applied by service type alone it would
    zero every road transfer in the quotation."""
    clause = clause_for(facts, RuleKey.COMPLIMENTARY_TRANSFER)
    rule = RULES[RuleKey.COMPLIMENTARY_TRANSFER]
    outcome = rule(booked(service_type="transfer", description="HDS to Kudu Sands Lodges",
                          dropoff="Kudu Ridge"), clause, context(nights_at_destination=3))
    assert outcome.not_applicable_because


# --------------------------------------------------------------------------- capacity


def test_a_capacity_that_holds_is_recorded_and_not_escalated(facts):
    """5 travellers against "Max 7 pax per vehicle" is not a finding, it is a fact."""
    limits = [c for c in facts.conditions
              if c.rule_key is RuleKey.CAPACITY_CONSTRAINT and "7" in c.quote]
    assert limits
    rule = RULES[RuleKey.CAPACITY_CONSTRAINT]
    outcome = rule(booked(service_type="day_tour", description=limits[0].label or ""),
                   limits[0], context())
    assert outcome.flags == () and outcome.not_applicable_because


def test_a_capacity_reconciled_by_another_clause_is_informational(facts):
    """Two Luxury Suites at "Max 2 adults" hold four; five are booked; note 4a releases the
    third bed and the quotation books a Triple. Blocking on the arithmetic alone would be
    reading half the document."""
    limits = [c for c in facts.conditions
              if c.rule_key is RuleKey.CAPACITY_CONSTRAINT and c.label == "Luxury Suite"
              and "Max" in c.quote]
    assert limits
    triple = clause_for(facts, RuleKey.TRIPLE_OCCUPANCY)
    line = booked(quantity=2, room_type="Luxury Suite", nights=None,
                  supplier_or_property="Kudu Ridge Private Game Reserve",
                  modifiers=[modifier("Triple", 1, "per group")])
    outcome = RULES[RuleKey.CAPACITY_CONSTRAINT](
        line, limits[0], context(rooms=2, other_conditions=[triple])
    )
    assert outcome.flags and outcome.flags[0].severity == "informational"
    assert "4a" in outcome.flags[0].question


def test_a_capacity_with_nothing_to_reconcile_it_blocks(facts):
    limits = [c for c in facts.conditions
              if c.rule_key is RuleKey.CAPACITY_CONSTRAINT and c.label == "Luxury Suite"
              and "Max" in c.quote]
    line = booked(quantity=2, room_type="Luxury Suite",
                  supplier_or_property="Kudu Ridge Private Game Reserve")
    outcome = RULES[RuleKey.CAPACITY_CONSTRAINT](line, limits[0], context(rooms=2))
    assert outcome.flags and outcome.flags[0].severity == "blocking"


# --------------------------------------------------------------------------- tax


def test_tax_already_in_the_rate_adds_nothing_and_says_so(facts):
    """The rule exists so that "nothing was added here" is recorded rather than assumed."""
    clause = clause_for(facts, RuleKey.TAX_INCLUDED)
    outcome = RULES[RuleKey.TAX_INCLUDED](booked(), clause, context())
    assert outcome.charges == () and outcome.flags == ()
    assert "inside the rate" in outcome.not_applicable_because
