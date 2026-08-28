"""The costing engine: a pure function from ExtractedFacts to CostedQuotation.

No file is opened here, no model is called, no clock is read and nothing is random. Given
the same intermediate.json this returns the same bytes, which is the only reason any of the
numbers below can be argued with rather than merely re-run.

Every booked line ends in exactly one CostedLine. There is no third outcome and, in
particular, no line that quietly disappears because nothing matched it. A line is priced,
or it is unpriced with a reason and a question attached - and an unpriced line is never
worth 0.00, because 0.00 is a price and "we do not know" is not.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

from costing.resolve_rate import (
    _key,
    find_override,
    match_accommodation,
    match_service,
    rate_for_night,
)
from costing.units import describe, occurrences_for, parse_unit, quantity_for
from domain import (
    ZERO,
    BookedLine,
    Condition,
    Confidence,
    CostedLine,
    CostedQuotation,
    ExtractedFacts,
    Money,
    Period,
    RateRecord,
    ReviewItem,
    RateResolution,
    RuleKey,
    SourceRef,
    Totals,
    weakest,
)
from rules import RULES, Charge, Context

# A line total counts as firm at or above this rank: the price came from a document that
# stands behind it. ASSUMED still counts, because an assumed line is priced at the lower of
# the readings available and the difference is carried separately as exposure - the money
# in the total is owed either way. EXPIRED does not, because the pack itself says that
# number may be wrong and gives no bound on by how much.
FIRM_AT_LEAST = Confidence.ASSUMED

_SECTION_SUPPLIER = {
    "2": "Cape Town Touring & Transfers",
    "3": "Marula River Lodge",
    "4": "Kudu Ridge Private Game Reserve",
    "5": "Transfers & Tours",
    "6": "Ilha Azul Beach Lodge",
    "7": "Mozambique Air & Sea Transfers",
}


# --------------------------------------------------------------------------- routing


_ROUTE = re.compile(r"^(?P<from>.+?)\s+to\s+(?P<to>.+)$", re.IGNORECASE)


def _place_tokens(text: str | None) -> set[str]:
    return {t for t in _key(text).split() if len(t) > 2}


def routing_contradiction(line: BookedLine) -> str | None:
    """Does the line's own label disagree with the line's own pickup and dropoff?

    This needs no supplier vocabulary at all: the contradiction is inside the quotation,
    between what the service is called and where the vehicle actually goes. Two lines here
    are wrong in that way, and both would price cleanly against the pack:

      "HDS to Kudu Sands Lodges"  picked up at Marula River Lodge  - HDS is not in the
      movement at all, and the label matches a pack routing at 1.000.
      "KMIA to Kudu Sands Lodges" picked up at Kudu Ridge          - Kudu Sands is the
      label's destination and the movement's origin. The label reads backwards.

    The check only fires when one endpoint is supported by the routing and the other is
    contradicted. When neither endpoint has any lexical support - "Transfer CIA to Zone 1"
    against a pickup of "CPT" - the two strings are simply written in different vocabularies
    and this says nothing, because a checker that fires on unfamiliar wording is a checker
    people learn to ignore.
    """
    if line.service_type != "transfer" or not line.pickup or not line.dropoff:
        return None
    route = _ROUTE.match(line.description)
    if not route:
        return None

    pickup, dropoff = _place_tokens(line.pickup), _place_tokens(line.dropoff)
    origin, destination = _place_tokens(route.group("from")), _place_tokens(route.group("to"))

    def side(endpoint: set[str]) -> str:
        if endpoint & pickup and endpoint & dropoff:
            return "both"
        if endpoint & pickup:
            return "pickup"
        if endpoint & dropoff:
            return "dropoff"
        return "absent"

    at_origin, at_destination = side(origin), side(destination)
    if at_origin == "absent" and at_destination == "absent":
        return None

    if at_destination == "pickup":
        return (
            f"The service is labelled {line.description!r}, but {line.pickup!r} is where the "
            f"movement starts: the label's destination is the movement's origin. The label "
            f"reads backwards against the routing on its own line."
        )
    if at_origin == "dropoff":
        return (
            f"The service is labelled {line.description!r}, but the movement ends at "
            f"{line.dropoff!r}: the label's origin is the movement's destination."
        )
    if at_origin == "absent" and at_destination in ("dropoff", "both"):
        return (
            f"The service is labelled {line.description!r}, but the movement is "
            f"{line.pickup!r} to {line.dropoff!r}. The label's origin appears nowhere in the "
            f"routing, so the priced service is not the service being performed."
        )
    if at_destination == "absent" and at_origin in ("pickup", "both"):
        return (
            f"The service is labelled {line.description!r}, but the movement is "
            f"{line.pickup!r} to {line.dropoff!r}. The label's destination appears nowhere in "
            f"the routing."
        )
    return None


# --------------------------------------------------------------------------- stays


def _nights_at(line: BookedLine, all_lines: list[BookedLine]) -> int | None:
    """How long the party stays where this transfer drops them.

    The complimentary transfer clause is conditional on the length of the stay, and the
    transfer line does not carry one. The stay is on a different line of the same quotation,
    identified by the property the transfer names as its destination.
    """
    if not line.dropoff:
        return None
    wanted = _place_tokens(line.dropoff)
    for other in all_lines:
        if other.service_type != "accommodation" or other.nights is None:
            continue
        if wanted and wanted & _place_tokens(other.supplier_or_property):
            return other.nights
    return None


def _supplier_of(clause: Condition) -> str | None:
    return _SECTION_SUPPLIER.get(clause.source.section or "")


# --------------------------------------------------------------------------- pricing


class _Priced:
    """The outcome of pricing one line, before the rules touch it."""

    def __init__(self) -> None:
        self.total: Money | None = None
        self.unit_price: Money | None = None
        self.units_charged: int | None = None
        self.calculation: str | None = None
        self.confidence: Confidence = Confidence.UNRESOLVED
        self.reason: str | None = None
        self.provenance: list[SourceRef] = []
        self.ambiguities: list[tuple[date, Money, str]] = []


def _price_line(
    line: BookedLine,
    rates: list[RateRecord],
    resolution: RateResolution,
    override,
    travellers: int,
) -> _Priced:
    """Rate x quantity x occurrences, resolved per night where the pack has seasons."""
    out = _Priced()
    if resolution.winner is None and override is None:
        return out

    unit_raw = override.unit_raw if override else next(r.unit_raw for r in rates if r.rate_id == resolution.winner)
    unit = parse_unit(unit_raw)
    if unit is None:
        out.reason = f"the unit {unit_raw!r} is not one this system can read"
        return out

    quantity = quantity_for(unit, travellers=travellers, rooms=line.quantity, nights=line.nights)
    occurrences = occurrences_for(unit, nights=line.nights)
    if quantity is None or occurrences is None:
        out.reason = (
            f"the rate is charged {unit_raw!r} and this line does not state "
            f"{'a quantity' if quantity is None else 'a length of stay'}"
        )
        return out

    # Which dates the charge lands on. A nightly rate is resolved night by night because
    # the season it falls in - and therefore the price - can change inside a single stay.
    if unit.period is Period.NIGHT and line.nights:
        days = [line.date_start + timedelta(days=i) for i in range(line.nights)]
    else:
        days = [line.date_start]

    running, confidences, notes = ZERO, [], []
    for day in days:
        if override is not None:
            price, confidence, note = override.price, Confidence.SUPERSEDED_BY_CORRESPONDENCE, None
            out.provenance.append(override.source)
        else:
            found, confidence, note = rate_for_night(rates, resolution.winner, day)
            if not found:
                out.reason = note
                return out
            if len(found) > 1:
                # Two published rates cover this night and the document decides between
                # them nowhere. Priced at the lower, with the difference carried as
                # exposure: it is the only reading that cannot overstate what is owed, and
                # the gap is stated rather than absorbed.
                cheapest = min(found, key=lambda r: r.price.amount)
                dearest = max(found, key=lambda r: r.price.amount)
                gap = Money(amount=dearest.price.amount - cheapest.price.amount, currency=dearest.price.currency)
                out.ambiguities.append(
                    (day, gap.times(quantity), f"{note} Priced at {cheapest.price}; "
                     f"{dearest.season.name if dearest.season else 'the other band'} would add "
                     f"{gap.times(quantity)}.")
                )
                found = [cheapest]
            price = found[0].price
            out.provenance.append(found[0].source)
        confidences.append(confidence)
        if note and not out.ambiguities:
            notes.append(note)
        running = running + price.times(quantity)

    out.unit_price = price
    out.units_charged = quantity * len(days)
    out.total = running
    out.confidence = weakest(*confidences)
    out.reason = " ".join(notes) or None
    if unit.period is Period.NIGHT and line.nights:
        out.calculation = f"{quantity} x {line.nights} nights, {unit_raw}"
    else:
        out.calculation = f"{describe(unit, quantity, occurrences)} x {price}, {unit_raw}"
    return out


# --------------------------------------------------------------------------- the engine


def cost_quotation(facts: ExtractedFacts) -> CostedQuotation:
    reviews: list[ReviewItem] = []
    costed: list[CostedLine] = []

    def ask(
        line_id: str | None,
        field: str,
        question: str,
        evidence: list[SourceRef],
        severity: str = "blocking",
        exposure: Money | None = None,
    ) -> str:
        # The pack states the carried-forward tariff in three separate clauses, and each of
        # them fires on the same line. Three identical questions is not three findings, and
        # a review list padded with repeats is one a human stops reading.
        for existing in reviews:
            if (existing.line_id, existing.question) == (line_id, question):
                return existing.review_id
        item = ReviewItem(
            review_id=f"Q{len(reviews) + 1:02d}",
            severity=severity,
            line_id=line_id,
            field=field,
            question=question,
            evidence=evidence,
            exposure=exposure,
        )
        reviews.append(item)
        return item.review_id

    no_rate_clause = next(
        (c for c in facts.conditions if c.rule_key is RuleKey.NO_RATE_REQUOTE), None
    )

    for line in facts.booked_lines:
        refs: list[str] = []
        notes: list[str] = []

        # 1. Does the line contradict itself before any rate is consulted?
        unmatched: list[SourceRef] | None = None
        contradiction = routing_contradiction(line)
        if contradiction:
            resolution = RateResolution(
                winner=None,
                winner_source=None,
                rule="the line's label and its own routing describe different movements",
                rejected=[],
                reason=contradiction,
            )
            refs.append(
                ask(
                    line.line_id,
                    "routing",
                    f"{contradiction} Confirm which movement is being sold, then re-price it.",
                    [line.source],
                )
            )
            priced = _Priced()
            priced.reason = "not priced: the service named is not the service routed"
        else:
            # 2. Correspondence first, then the pack.
            override = find_override(line, facts.overrides)
            resolution = (
                match_accommodation(line, facts.rates)
                if line.service_type == "accommodation"
                else match_service(line, facts.rates)
            )
            if override is not None:
                resolution = RateResolution(
                    winner=resolution.winner,
                    winner_source=override.source,
                    rule="superseded by written supplier correspondence (pack section 9)",
                    rejected=resolution.rejected,
                    reason=(
                        f"{override.supplier} wrote on {override.sent_at:%d %b %Y}: "
                        f"{override.supersession_claim} Priced at {override.price}, not at the "
                        f"pack rate."
                    ),
                )
            priced = _price_line(line, facts.rates, resolution, override, facts.travellers)

            if priced.total is None and resolution.winner is None and override is None:
                # Held back until after the rules have run. A clause may yet price this line
                # - the complimentary transfer is worth a contracted 0.00 - or answer for it
                # more precisely than this can, as section 8 does for the air sectors. Asking
                # now would leave a stale question attached to a line that was resolved two
                # steps later, and a review list nobody trusts is a review list nobody reads.
                unmatched = [line.source] + ([no_rate_clause.source] if no_rate_clause else [])
            elif priced.total is None:
                refs.append(
                    ask(line.line_id, "rate",
                        f"A rate was matched for {line.description!r} but it could not be "
                        f"applied: {priced.reason}. Confirm the basis of charge.",
                        [line.source, *( [resolution.winner_source] if resolution.winner_source else [] )])
                )

        # 3. Season ambiguity: priced at the lower bound, difference carried as exposure.
        for day, gap, explanation in priced.ambiguities:
            refs.append(
                ask(
                    line.line_id,
                    "season",
                    f"The night of {day:%d %b %y} falls in two published season bands and the "
                    f"pack does not say which applies. {explanation} Confirm the band with "
                    f"{line.supplier_or_property}.",
                    priced.provenance,
                    exposure=gap,
                )
            )

        # 4. The clauses. Every condition is offered to every line; each rule answers for
        #    itself, and a rule that declines says why.
        charges: list[Charge] = []
        context = Context(
            travellers=facts.travellers,
            nights=line.nights,
            rooms=line.quantity,
            unit_price=priced.unit_price,
            line_total=priced.total,
            supplier_of_clause=None,
            nights_at_destination=_nights_at(line, facts.booked_lines),
            priced_from_override=resolution.rule.startswith("superseded"),
            other_conditions=facts.conditions,
        )
        for clause in facts.conditions:
            rule = RULES.get(clause.rule_key)
            if rule is None:
                continue
            outcome = rule(line, clause, _with_supplier(context, _supplier_of(clause)))
            if outcome.not_applicable_because:
                continue
            for charge in outcome.charges:
                if charge.replaces_line_total:
                    priced.total = charge.amount
                    priced.calculation = charge.calculation
                    priced.confidence = charge.confidence
                    priced.reason = charge.reason
                    priced.provenance.extend(charge.provenance)
                    continue
                if priced.total is None:
                    # The clause applies but the service it attaches to has no price, so
                    # the charge cannot be added to anything. It is money the run has not
                    # billed, which makes it exposure rather than a silent omission.
                    refs.append(
                        ask(line.line_id, "modifier",
                            f"{charge.reason} This is payable on a line that could not be "
                            f"priced; confirm it once the underlying service is re-quoted.",
                            charge.provenance, exposure=charge.amount)
                    )
                    continue
                charges.append(charge)
                priced.total = priced.total + charge.amount
                # The arithmetic in the output has to add up to the number beside it. A
                # calculation showing only the base rate next to a total that includes a
                # supplement is the quiet kind of wrong this system exists to avoid.
                priced.calculation = f"{priced.calculation} + {charge.calculation}"
                notes.append(f"{charge.calculation} = {charge.amount} ({charge.reason})")
                priced.provenance.extend(charge.provenance)
            for flag in outcome.flags:
                refs.append(
                    ask(line.line_id, clause.rule_key.value, flag.question, flag.provenance,
                        severity=flag.severity, exposure=flag.exposure)
                )
                notes.append(flag.reason)

        if unmatched is not None and priced.total is None:
            answered = any(
                item.severity == "blocking"
                for item in reviews
                if item.review_id in refs and item.field != "rate"
            )
            if not answered:
                refs.append(
                    ask(
                        line.line_id,
                        "rate",
                        f"No rate in the pack covers {line.description!r}"
                        + (f" at {line.supplier_or_property}" if line.supplier_or_property else "")
                        + ". Re-quote this service with the supplier before the quotation is "
                        "issued.",
                        unmatched,
                    )
                )

        confidence = weakest(priced.confidence, *(c.confidence for c in charges))
        costed.append(
            CostedLine(
                line_id=line.line_id,
                description=line.description,
                region=line.region,
                date_start=line.date_start,
                date_end=line.date_end,
                quantity=line.quantity,
                unit_price=priced.unit_price,
                units_charged=priced.units_charged,
                calculation=priced.calculation,
                total=priced.total,
                confidence=confidence if priced.total is not None else Confidence.UNRESOLVED,
                confidence_reason=" ".join(filter(None, [priced.reason, *notes])) or None,
                resolution=resolution,
                provenance=priced.provenance,
                review_refs=list(dict.fromkeys(refs)),
            )
        )

    return CostedQuotation(
        quotation_ref=facts.quotation_ref,
        travellers=facts.travellers,
        costed_lines=costed,
        needs_review=reviews,
        totals=_totals(costed, reviews),
        system_warnings=facts.system_warnings,
    )


def _with_supplier(context: Context, supplier: str | None) -> Context:
    return Context(
        travellers=context.travellers,
        nights=context.nights,
        rooms=context.rooms,
        unit_price=context.unit_price,
        line_total=context.line_total,
        supplier_of_clause=supplier,
        nights_at_destination=context.nights_at_destination,
        priced_from_override=context.priced_from_override,
        other_conditions=context.other_conditions,
    )


def _totals(lines: list[CostedLine], reviews: list[ReviewItem]) -> Totals:
    """Three numbers, and never one.

    firm      - totals resting on a rate the supplier stands behind for these dates.
    exposure  - money not billed above that may still become payable: the deltas on
                ambiguous nights, the levies the pack makes compulsory beyond what was
                quoted, plus any line priced from a rate its own document calls indicative.
    unpriced  - services with no defensible number at all, counted and named, never zeroed.

    A single grand total would have to put these in the same column, and the moment it did
    the reader would stop being able to tell which parts of it are true.
    """
    firm, exposure = ZERO, ZERO
    firm_count = exposure_count = 0
    unpriced: list[str] = []

    for line in lines:
        if line.total is None:
            unpriced.append(f"{line.line_id} {line.description}: {line.resolution.reason}")
            continue
        if line.confidence.rank >= FIRM_AT_LEAST.rank:
            firm, firm_count = firm + line.total, firm_count + 1
        else:
            exposure, exposure_count = exposure + line.total, exposure_count + 1

    for item in reviews:
        if item.exposure is not None:
            exposure = exposure + item.exposure

    return Totals(
        firm=firm,
        firm_line_count=firm_count,
        exposure=exposure,
        exposure_line_count=exposure_count,
        unpriced_line_count=len(unpriced),
        unpriced_reasons=unpriced,
    )
