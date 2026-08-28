"""One function per supplier clause, and nothing else in the system may change money.

Every function takes the same three things - the line, the clause that authorises it, and
the arithmetic context - and returns a RuleOutcome. There is no way to return "nothing
happened" by accident: a rule that does not apply must say why, and the why is kept and
printed, because "this rule did not fire" is information a reviewer needs as much as a
charge is.

Dispatch is not clever. cost.py offers every clause to every line and each rule decides for
itself. 22 clauses across 19 lines is 418 calls of pure Python, and in exchange the
precondition for a charge lives next to the charge, in the same twelve lines a human has to
read to sign it off.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from domain import (
    ZERO,
    BookedLine,
    Condition,
    Confidence,
    Money,
    RuleKey,
    SourceRef,
)

# --------------------------------------------------------------------------- outcomes


@dataclass(frozen=True)
class Charge:
    """Money this clause adds to, or subtracts from, the quotation."""

    amount: Money
    calculation: str
    reason: str
    provenance: list[SourceRef]
    confidence: Confidence = Confidence.CONTRACTED
    replaces_line_total: bool = False


@dataclass(frozen=True)
class Flag:
    """A question for a human. `question` must be answerable by the supplier as written.

    `exposure` means one thing only: money this run has NOT charged and may still owe.
    Money already inside a line total is never repeated here, or the same figure would be
    counted twice and the exposure total would stop meaning anything. Where the risk runs
    the other way - a charge that may turn out not to be due - the amount is named in the
    question and exposure stays None, because removing a charge is not an exposure.
    """

    question: str
    reason: str
    provenance: list[SourceRef]
    severity: str = "blocking"
    exposure: Money | None = None


@dataclass(frozen=True)
class RuleOutcome:
    charges: tuple[Charge, ...] = ()
    flags: tuple[Flag, ...] = ()
    not_applicable_because: str | None = None


def skip(why: str) -> RuleOutcome:
    """A rule that does not fire still has to account for itself."""
    return RuleOutcome(not_applicable_because=why)


@dataclass(frozen=True)
class Context:
    """Everything a rule may know. Nothing here comes from a model or from the clock."""

    travellers: int
    nights: int | None
    rooms: int
    unit_price: Money | None
    line_total: Money | None
    supplier_of_clause: str | None
    nights_at_destination: int | None = None
    priced_from_override: bool = False
    other_conditions: list[Condition] = field(default_factory=list)


# --------------------------------------------------------------------------- helpers


def _modifier(line: BookedLine, *aliases: str):
    """The `Included:` line matching any of these names, or None.

    The quotation writes the Marula levy as "Consv Levy" and the Kudu one as "Conservation
    Levy". Same clause, same money, two spellings; the aliases are listed because a rule
    that only knew the long form would silently drop 168.00.
    """
    wanted = {a.lower() for a in aliases}
    return next((m for m in line.modifiers if m.label.lower() in wanted), None)


def _belongs_to(line: BookedLine, context: Context) -> bool:
    """Is this clause the supplier's clause for this line?"""
    if context.supplier_of_clause is None:
        return True
    a = (line.supplier_or_property or "").lower()
    b = context.supplier_of_clause.lower()
    return bool(a) and (a in b or b in a)


# --------------------------------------------------------------------------- the rules


def conservation_levy(line: BookedLine, clause: Condition, ctx: Context) -> RuleOutcome:
    """Compulsory per-person levy, charged for everyone staying - not for whoever was typed.

    The quotation books 2 levies at Marula and 3 at Kudu for a party of 5. The clause says
    compulsory and payable on all stays, so the supplier will invoice 5 in both cases.

    The charge stays at what the quotation sold and the difference is reported as exposure.
    Silently raising it to 5 would be this system deciding, on its own reading of a sentence,
    that a client owes 378.00 more than they were shown - and it would hide that decision
    inside a total that looked no different from any other. The gap is the finding; it
    belongs where a human can see it and act on it, not buried in the arithmetic.
    """
    if line.service_type != "accommodation" or not _belongs_to(line, ctx):
        return skip("levy attaches to a stay with this supplier")
    booked = _modifier(line, "Consv Levy", "Conservation Levy")
    if booked is None:
        return skip("no levy line on this stay")
    if clause.amount is None or ctx.nights is None:
        return skip("clause states no amount, or the stay has no length")

    compulsory = ctx.travellers
    charge = clause.amount.times(booked.quantity).times(ctx.nights)
    outcome_flags: tuple[Flag, ...] = ()

    if booked.quantity != compulsory:
        shortfall = clause.amount.times(compulsory - booked.quantity).times(ctx.nights)
        outcome_flags = (
            Flag(
                question=(
                    f"The quotation books {booked.quantity} conservation levies for "
                    f"{compulsory} travellers at {line.supplier_or_property}. The rate pack "
                    f"states the levy is compulsory for all guests. Confirm the levy is "
                    f"payable for {compulsory} guests, not {booked.quantity}."
                ),
                reason=(
                    f"Charged for {booked.quantity} as quoted; the pack makes it payable for "
                    f"{compulsory}."
                ),
                provenance=[clause.source, booked.source],
                exposure=shortfall,
            ),
        )

    return RuleOutcome(
        charges=(
            Charge(
                amount=charge,
                calculation=f"{booked.quantity} pax x {ctx.nights} nights x {clause.amount}",
                reason=f"{clause.label or 'Conservation levy'}: {clause.quote}",
                provenance=[clause.source],
                confidence=Confidence.CONTRACTED,
            ),
        ),
        flags=outcome_flags,
    )


def trailer_supplement(line: BookedLine, clause: Condition, ctx: Context) -> RuleOutcome:
    """65.00 per group per transfer - booked here for a group that does not trigger it.

    The clause gives two triggers: six pax and above, OR luggage exceeding vehicle capacity.
    The party is five, so the first does not apply and only the second can explain why the
    quotation booked it. That is not something the documents answer, so the charge stands as
    booked and the question goes to the operator.
    """
    if _modifier(line, "Trailer 6+ Pax", "Trailer") is None:
        return skip("no trailer line on this service")
    if clause.amount is None:
        return skip("clause states no amount")

    charge = Charge(
        amount=clause.amount,
        calculation=f"1 group x 1 transfer x {clause.amount}",
        reason=f"Trailer supplement, charged per transfer: {clause.quote}",
        provenance=[clause.source],
        confidence=Confidence.ASSUMED,
    )
    if ctx.travellers >= 6:
        return RuleOutcome(charges=(charge,))

    return RuleOutcome(
        charges=(charge,),
        flags=(
            Flag(
                question=(
                    f"The trailer supplement of {clause.amount} is booked on this transfer "
                    f"for a party of {ctx.travellers}. The pack requires it for groups of 6 "
                    "pax and above, or where luggage exceeds vehicle capacity. Confirm the "
                    "luggage volume requires a trailer, or remove the supplement."
                ),
                reason=f"Party of {ctx.travellers} is below the pack's stated threshold of 6.",
                provenance=[clause.source, line.source],
                severity="blocking",
            ),
        ),
    )


def per_group_extra(line: BookedLine, clause: Condition, ctx: Context) -> RuleOutcome:
    """140.00 per group on the Custom Private Tour, exactly as note 2b states."""
    if _modifier(line, "Per Group Extra") is None:
        return skip("no per-group extra on this service")
    if clause.amount is None:
        return skip("clause states no amount")
    return RuleOutcome(
        charges=(
            Charge(
                amount=clause.amount,
                calculation=f"1 group x {clause.amount}",
                reason=f"Per Group Extra: {clause.quote}",
                provenance=[clause.source],
            ),
        )
    )


def complimentary_transfer(line: BookedLine, clause: Condition, ctx: Context) -> RuleOutcome:
    """A real zero, and the only kind this system will print.

    The stay at Marula River Lodge is 09 to 11 July: exactly two nights, exactly the
    threshold the clause sets. The transfer costs nothing, and that nothing is contracted -
    which is a completely different fact from a rate the system failed to find. Both would
    show as 0.00 in a naive output; here one is firm and the other is unpriced.
    """
    if line.service_type != "transfer":
        return skip("not a transfer")
    if "complimentary" not in line.description.lower():
        return skip("this transfer is not the complimentary one")
    nights = ctx.nights_at_destination
    if nights is None:
        return skip("no stay at the destination to measure against")

    if nights >= 2:
        return RuleOutcome(
            charges=(
                Charge(
                    amount=ZERO,
                    calculation=f"{nights} nights at the destination meets the 2-night threshold",
                    reason=f"No charge: {clause.quote}",
                    provenance=[clause.source],
                    replaces_line_total=True,
                ),
            )
        )

    return RuleOutcome(
        flags=(
            Flag(
                question=(
                    f"The complimentary transfer requires a stay of two nights or more; this "
                    f"stay is {nights} night(s). Confirm whether the transfer is chargeable "
                    "and at what rate."
                ),
                reason=f"Stay of {nights} night(s) is below the clause's threshold.",
                provenance=[clause.source],
            ),
        )
    )


def triple_occupancy(line: BookedLine, clause: Condition, ctx: Context) -> RuleOutcome:
    """Note 4a: no supplement, no reduction, third guest at the standard sharing rate.

    This is why five people in two Luxury Suites is arithmetically fine on a per person
    sharing rate: everyone is charged the same 890.00. It is also what reconciles the
    "Max 2 adults" note, which would otherwise cap the suites at four guests.
    """
    if line.service_type != "accommodation" or not _belongs_to(line, ctx):
        return skip("triple occupancy is a room rule for this supplier")
    if _modifier(line, "Triple") is None:
        return skip("no triple occupancy booked on this stay")
    return RuleOutcome(
        charges=(
            Charge(
                amount=ZERO,
                calculation="third guest at the standard per person sharing rate",
                reason=f"No supplement and no reduction: {clause.quote}",
                provenance=[clause.source],
            ),
        ),
        flags=(
            Flag(
                question=(
                    f"{ctx.travellers} guests are booked into {line.quantity} "
                    f"{line.room_type}(s) on a triple-occupancy basis. Confirm the supplier "
                    "has released the triple, which the pack says is on request."
                ),
                reason="Note 4a makes the third guest chargeable at the standard rate; the "
                "release itself is not something the pack confirms.",
                provenance=[clause.source, line.source],
                severity="informational",
            ),
        ),
    )


def single_occupancy(line: BookedLine, clause: Condition, ctx: Context) -> RuleOutcome:
    """No reduction for sole use, and a supplement where the supplier prices one.

    Nobody on this quotation is travelling alone - five guests across two units at every
    property - so this rule reports that it did not fire rather than staying silent.
    """
    if line.service_type != "accommodation" or not _belongs_to(line, ctx):
        return skip("single occupancy is a room rule for this supplier")
    if ctx.rooms == 0 or ctx.travellers > ctx.rooms:
        return skip(f"{ctx.travellers} guests across {ctx.rooms} unit(s): no sole use")
    return RuleOutcome(
        flags=(
            Flag(
                question=(
                    f"One guest occupies a {line.room_type}. Confirm the single occupancy "
                    "basis the supplier will invoice."
                ),
                reason=clause.quote,
                provenance=[clause.source],
            ),
        )
    )


def gate_fees(line: BookedLine, clause: Condition, ctx: Context) -> RuleOutcome:
    """Reserve gate charges: real amounts whose applicability the pack refuses to confirm.

    "Applicability depends on the gate used on the day and is confirmed by the transfer
    operator." The amounts are contracted; whether they are levied is not. So they are
    charged and flagged, and the flag carries the full amount as exposure, because the
    honest answer to "will this be charged" is that the pack does not say.
    """
    if clause.label is None or clause.amount is None:
        return skip("clause names no fee or no amount")
    booked = _modifier(line, clause.label)
    if booked is None:
        return skip(f"{clause.label} not booked on this service")

    per_pax = "pax" in (clause.unit_raw or "") or "person" in (clause.unit_raw or "")
    charge = clause.amount.times(booked.quantity)

    flags = [
        Flag(
            question=(
                f"Confirm with the transfer operator whether the {clause.label} of "
                f"{clause.amount} is levied at the gate used on {line.date_start:%d %b %y}."
            ),
            reason=f"The pack makes this fee conditional: {clause.quote}",
            provenance=[clause.source, booked.source],
        )
    ]
    if per_pax and booked.quantity < ctx.travellers:
        flags.append(
            Flag(
                question=(
                    f"The quotation books {booked.quantity} x {clause.label} for "
                    f"{ctx.travellers} travellers, and the pack prices it per pax. Confirm "
                    f"whether the fee is payable for {ctx.travellers}."
                ),
                reason=f"Charged for {booked.quantity} as quoted, on a unit of {clause.unit_raw}.",
                provenance=[clause.source, booked.source],
                exposure=clause.amount.times(ctx.travellers - booked.quantity),
            )
        )

    return RuleOutcome(
        charges=(
            Charge(
                amount=charge,
                calculation=f"{booked.quantity} x {clause.amount} ({clause.unit_raw})",
                reason=f"{clause.label}: {clause.quote}",
                provenance=[clause.source],
                confidence=Confidence.ASSUMED,
            ),
        ),
        flags=tuple(flags),
    )


def capacity_constraint(line: BookedLine, clause: Condition, ctx: Context) -> RuleOutcome:
    """"Max 2 adults", "Max 7 pax per vehicle", "Min 2 nights", "Minimum 2 pax".

    A limit that holds is worth recording and not worth interrupting anyone about. A limit
    that is exceeded is a question about whether the supplier will accept the booking at
    all, which is upstream of what it costs.

    The Kudu case is the interesting one: two Luxury Suites at "Max 2 adults" hold four, and
    five are booked. Note 4a releases the third bed in one suite, and the quotation books a
    Triple. The constraint is satisfied by another clause of the same pack, so this reports
    the reconciliation instead of blocking on arithmetic that is only half the document.
    """
    if clause.label is None:
        return skip("clause names no product")
    target = line.room_type or line.description
    if clause.label.lower() not in target.lower() and target.lower() not in clause.label.lower():
        return skip(f"{clause.label!r} is not the product on this line")

    text = clause.quote.lower()
    limit = next((int(w) for w in text.replace(".", " ").split() if w.isdigit()), None)
    if limit is None:
        return skip("clause states no number")

    if "night" in text:
        actual, subject = ctx.nights, "nights"
    elif "vehicle" in text or "pax" in text or "adult" in text:
        actual, subject = ctx.travellers, "travellers"
    else:
        return skip("clause does not name what it limits")
    if actual is None:
        return skip(f"the line does not state its {subject}")

    if text.startswith("max"):
        capacity = limit * (line.quantity if line.service_type == "accommodation" else 1)
        if actual <= capacity:
            return skip(f"{actual} {subject} within the limit of {capacity}")
        triple_released = any(
            c.rule_key is RuleKey.TRIPLE_OCCUPANCY for c in ctx.other_conditions
        ) and _modifier(line, "Triple") is not None
        if triple_released:
            return RuleOutcome(
                flags=(
                    Flag(
                        question=(
                            f"{actual} guests occupy {line.quantity} x {line.room_type} against "
                            f"a stated capacity of {capacity}. Note 4a releases the third bed "
                            "on request and the quotation books a Triple - confirm the release."
                        ),
                        reason=f"Capacity {capacity} is reconciled by note 4a, not by this clause.",
                        provenance=[clause.source, line.source],
                        severity="informational",
                    ),
                )
            )
        return RuleOutcome(
            flags=(
                Flag(
                    question=(
                        f"{actual} {subject} exceed the stated maximum of {capacity} for "
                        f"{clause.label}. Confirm the supplier accepts the booking as quoted."
                    ),
                    reason=clause.quote,
                    provenance=[clause.source, line.source],
                ),
            )
        )

    if actual >= limit:
        return skip(f"{actual} {subject} meets the minimum of {limit}")
    return RuleOutcome(
        flags=(
            Flag(
                question=(
                    f"{actual} {subject} is below the stated minimum of {limit} for "
                    f"{clause.label}. Confirm the supplier will hold the booking."
                ),
                reason=clause.quote,
                provenance=[clause.source, line.source],
            ),
        )
    )


def tax_included(line: BookedLine, clause: Condition, ctx: Context) -> RuleOutcome:
    """Tax already inside the rate. The whole point is that nothing is added."""
    if not _belongs_to(line, ctx):
        return skip("this clause belongs to another supplier")
    return skip(f"tax is inside the rate, nothing to add: {clause.quote}")


def carried_forward_tariff(line: BookedLine, clause: Condition, ctx: Context) -> RuleOutcome:
    """A 2025 tariff standing in for a 2027 price the operator has not published.

    The pack is explicit that it did this and explicit that the operator has signalled an
    increase without confirming one. So the number is usable and the exposure is the whole
    of it: there is no basis in any document for saying how much higher it will be.
    """
    if ctx.line_total is None:
        return skip("the line has no total for this tariff to be uncertain about")
    if "helicopter" not in line.description.lower() and "speedboat" not in line.description.lower():
        return skip("this line is not priced from a carried-forward tariff")
    return RuleOutcome(
        flags=(
            Flag(
                question=(
                    "This line is priced from the 2025 tariff carried forward by the pack. "
                    "Reconfirm the 2027 rate with the operator before the quotation is issued."
                ),
                reason=(
                    f"{clause.quote} The whole of {ctx.line_total} rests on that tariff, and "
                    "the pack gives no basis for saying how far the 2027 rate will move."
                ),
                provenance=[clause.source],
            ),
        )
    )


def air_not_carried(line: BookedLine, clause: Condition, ctx: Context) -> RuleOutcome:
    """Section 8 forbids the one thing a costing engine would be tempted to do here.

    There are three Zambezi Air sectors and no fare anywhere in the pack. The temptation is
    to carry a fare from somewhere - a previous quotation, a similar sector, the helicopter
    row two sections up. The pack says not to, and the exposure is genuinely unknown, so
    these lines stay unpriced and are counted as unpriced rather than as zero.
    """
    if line.service_type != "flight":
        return skip("not an air sector")
    return RuleOutcome(
        flags=(
            Flag(
                question=(
                    f"Air {line.pickup} to {line.dropoff} on {line.date_start:%d %b %y} is not "
                    "priced. The pack carries no airfares; quote this sector live with the "
                    "carrier before the quotation is issued."
                ),
                reason=clause.quote,
                provenance=[clause.source, line.source],
                exposure=None,
            ),
        )
    )


def correspondence_precedence(line: BookedLine, clause: Condition, ctx: Context) -> RuleOutcome:
    """Records that the pack was overruled, on the lines where it actually was."""
    if not ctx.priced_from_override:
        return skip("this line is priced from the pack, not from correspondence")
    return RuleOutcome(
        flags=(
            Flag(
                question=(
                    "This line is priced from supplier correspondence, not from the rate pack. "
                    "Confirm the revised rate is the one the supplier will invoice."
                ),
                reason=clause.quote,
                provenance=[clause.source],
                severity="informational",
            ),
        )
    )


# --------------------------------------------------------------------------- the registry

RuleFn = "Callable[[BookedLine, Condition, Context], RuleOutcome]"

# A literal mapping rather than a decorator. A decorator registers rules as a side effect of
# importing them, which means the set of rules that can charge money depends on which
# modules were imported; here it is a list a reviewer can read top to bottom and compare
# against the pack. RuleKey.NO_RATE_REQUOTE and RuleKey.UNCLASSIFIED are absent on purpose:
# neither is a clause that changes a price, and the engine handles both directly.
RULES: dict[RuleKey, object] = {
    RuleKey.CONSERVATION_LEVY: conservation_levy,
    RuleKey.TRAILER_SUPPLEMENT: trailer_supplement,
    RuleKey.PER_GROUP_EXTRA: per_group_extra,
    RuleKey.COMPLIMENTARY_TRANSFER: complimentary_transfer,
    RuleKey.TRIPLE_OCCUPANCY: triple_occupancy,
    RuleKey.SINGLE_OCCUPANCY: single_occupancy,
    RuleKey.GATE_FEES: gate_fees,
    RuleKey.CAPACITY_CONSTRAINT: capacity_constraint,
    RuleKey.TAX_INCLUDED: tax_included,
    RuleKey.CARRIED_FORWARD_TARIFF: carried_forward_tariff,
    RuleKey.AIR_NOT_CARRIED: air_not_carried,
    RuleKey.CORRESPONDENCE_PRECEDENCE: correspondence_precedence,
}
