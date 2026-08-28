"""Properties that must hold of any output, checked before anyone is allowed to read it.

These are not tests. Tests run on the examples someone thought of; these run on every
output, including the one produced from next year's rate pack by someone who has never read
this file. Each one is a way the system could produce a confident wrong answer, written
down as a condition rather than as a hope.

They raise. A crash is the safe failure here and it is the bottom of the hierarchy on
purpose: an output that violates one of these has already stopped being trustworthy, and
printing it anyway is how a wrong total reaches a client.
"""

from __future__ import annotations

from domain import ZERO, Confidence, CostedQuotation, ExtractedFacts


class InvariantViolated(AssertionError):
    """The output is internally inconsistent. It must not be shown to anyone."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise InvariantViolated(message)


def check(facts: ExtractedFacts, result: CostedQuotation) -> None:
    """Every invariant, in the order a reader would want them argued."""

    # 1. Nothing sold may vanish. This is the top of the failure hierarchy: a line that is
    #    silently absent from the output is money nobody will ever ask about.
    booked = [line.line_id for line in facts.booked_lines]
    costed = [line.line_id for line in result.costed_lines]
    _require(
        sorted(booked) == sorted(costed),
        f"every booked line must be costed exactly once; "
        f"missing {sorted(set(booked) - set(costed))}, extra {sorted(set(costed) - set(booked))}",
    )

    ids = {item.review_id for item in result.needs_review}

    for line in result.costed_lines:
        # 2. Unpriced and priced are the only two states, and they are told apart by the
        #    total, not by a flag that can drift away from it.
        _require(
            (line.total is None) == (line.confidence is Confidence.UNRESOLVED),
            f"{line.line_id}: an unpriced line must be UNRESOLVED and an UNRESOLVED line "
            f"must have no total (total={line.total}, confidence={line.confidence.value})",
        )

        # 3. A line with no price must carry a question. Otherwise the output says "we
        #    could not price this" and gives the reader nowhere to go.
        if line.total is None:
            _require(
                any(
                    item.severity == "blocking"
                    for item in result.needs_review
                    if item.review_id in line.review_refs
                ),
                f"{line.line_id}: unpriced with no blocking review item",
            )

        # 4. Zero is a price, and it is only ever allowed when a document says so. This is
        #    the invariant the brief is really about: the complimentary Marula transfer and
        #    an unmatched villa must never look alike in the output.
        if line.total is not None and line.total.amount == ZERO.amount:
            _require(
                line.confidence is Confidence.CONTRACTED and bool(line.confidence_reason),
                f"{line.line_id}: a total of {line.total} must come from a clause that states "
                f"it, with the clause quoted; got confidence={line.confidence.value}",
            )

        # 5. A number with no citation is a number this system made up.
        if line.total is not None:
            _require(
                bool(line.provenance),
                f"{line.line_id}: priced at {line.total} with no provenance",
            )

        # 6. Currency is never mixed. Money.__add__ already refuses, so a mismatch here
        #    would mean something bypassed it.
        if line.total is not None:
            _require(
                line.total.currency == result.currency,
                f"{line.line_id}: {line.total.currency} in a {result.currency} quotation",
            )

        # 7. Dangling references make an audit trail unfollowable.
        for ref in line.review_refs:
            _require(ref in ids, f"{line.line_id}: review_refs points at unknown {ref}")

    # 8. A review item that is not a question a supplier can answer is not a review item.
    for item in result.needs_review:
        _require(bool(item.question.strip()), f"{item.review_id}: empty question")
        _require(
            bool(item.evidence),
            f"{item.review_id}: asks a question with no document behind it",
        )
        _require(
            item.severity in ("blocking", "informational"),
            f"{item.review_id}: unknown severity {item.severity!r}",
        )

    # 9. The three totals must account for every line exactly once. If they do not, one of
    #    the three numbers is quietly wrong and the sum of them means nothing.
    totals = result.totals
    _require(
        totals.firm_line_count + totals.exposure_line_count + totals.unpriced_line_count
        == len(result.costed_lines),
        f"totals account for {totals.firm_line_count + totals.exposure_line_count + totals.unpriced_line_count} "
        f"lines out of {len(result.costed_lines)}",
    )
    _require(
        totals.unpriced_line_count == len(totals.unpriced_reasons),
        "every unpriced line must be named in unpriced_reasons",
    )
    _require(
        totals.unpriced_line_count == sum(1 for l in result.costed_lines if l.total is None),
        "unpriced_line_count disagrees with the lines that have no total",
    )

    # 10. Exposure is money that is NOT in a line total. The rule is stated in rules.Flag
    #     and it is what keeps `exposure` meaningful; the check that it was obeyed is that
    #     no review item carries an exposure equal to the total of the line it is attached
    #     to, which is the shape a double count takes.
    by_id = {line.line_id: line for line in result.costed_lines}
    for item in result.needs_review:
        if item.exposure is None or item.line_id is None:
            continue
        line = by_id.get(item.line_id)
        if line is None or line.total is None:
            continue
        _require(
            item.exposure.amount != line.total.amount,
            f"{item.review_id}: exposure of {item.exposure} equals the total of "
            f"{item.line_id}, which means the same money is counted twice",
        )
