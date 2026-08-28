"""The invariants, each one shown catching the output it exists to stop.

An invariant nobody has ever seen fire is a comment. So every check in invariants.py is
tested twice here: the real run passes it, and a deliberately damaged copy of that same run
trips it. The damage in each test is the smallest edit that produces the failure - which is
also, in each case, roughly what the bug would look like if it were real.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from domain import CURRENCY, Confidence, Money, ReviewItem, SourceRef
from invariants import InvariantViolated, check


def broken(result, **update):
    """A copy of the real output with one thing wrong."""
    return result.model_copy(update=update)


def lines_with(result, line_id, **update):
    return [l.model_copy(update=update) if l.line_id == line_id else l
            for l in result.costed_lines]


def a_priced_line(result):
    return next(l for l in result.costed_lines if l.total is not None and l.total.amount > 0)


def an_unpriced_line(result):
    return next(l for l in result.costed_lines if l.total is None)


# --------------------------------------------------------------------------- the real run


def test_the_real_output_satisfies_every_invariant(facts, result):
    """The one that matters. Everything below only proves the checks have teeth."""
    check(facts, result)


# --------------------------------------------------------------------------- 1. loss


def test_a_line_that_disappears_is_caught(facts, result):
    """Top of the failure hierarchy: sold, not costed, not mentioned.

    Nothing in a total's arithmetic reveals this. Only counting the lines does.
    """
    dropped = [l for l in result.costed_lines if l.line_id != "L05"]
    with pytest.raises(InvariantViolated, match="L05"):
        check(facts, broken(result, costed_lines=dropped))


def test_a_line_that_appears_from_nowhere_is_caught(facts, result):
    invented = result.costed_lines + [a_priced_line(result).model_copy(update={"line_id": "L99"})]
    with pytest.raises(InvariantViolated, match="L99"):
        check(facts, broken(result, costed_lines=invented))


# --------------------------------------------------------------------------- 2/3. unpriced


def test_a_price_that_drifts_away_from_its_confidence_is_caught(facts, result):
    """A total with UNRESOLVED beside it, or the reverse, is a system contradicting itself.

    This is what a refactor that updates one field and forgets the other produces.
    """
    line = a_priced_line(result)
    with pytest.raises(InvariantViolated, match=line.line_id):
        check(facts, broken(result,
                            costed_lines=lines_with(result, line.line_id,
                                                    confidence=Confidence.UNRESOLVED)))


def test_an_unpriced_line_with_no_question_is_caught(facts, result):
    """"We could not price this" and nothing else is a dead end for the reader.

    Here the review item still exists but is downgraded to informational - the realistic
    version of this bug, where a question survives but stops stopping anything.
    """
    line = an_unpriced_line(result)
    softened = [
        item.model_copy(update={"severity": "informational"})
        if item.review_id in line.review_refs else item
        for item in result.needs_review
    ]
    with pytest.raises(InvariantViolated, match="no blocking review"):
        check(facts, broken(result, needs_review=softened))


# --------------------------------------------------------------------------- 4. the zero


def test_a_zero_that_no_document_states_is_caught(facts, result):
    """The invariant the exercise is really about.

    A line priced at 0.00 because nothing matched is indistinguishable, in a total, from the
    complimentary transfer that is genuinely free. So a zero has to arrive CONTRACTED with
    the clause quoted, or not at all.
    """
    line = an_unpriced_line(result)
    zeroed = lines_with(result, line.line_id,
                        total=Money(amount=Decimal("0.00"), currency=CURRENCY),
                        confidence=Confidence.ASSUMED,
                        confidence_reason=None)
    with pytest.raises(InvariantViolated, match="must come from a clause"):
        check(facts, broken(result, costed_lines=zeroed))


def test_the_contracted_zero_still_needs_its_reason_quoted(facts, result):
    """Being CONTRACTED is not enough; the clause has to be readable in the output."""
    stripped = lines_with(result, "L09", confidence_reason=None)
    with pytest.raises(InvariantViolated, match="L09"):
        check(facts, broken(result, costed_lines=stripped))


# --------------------------------------------------------------------------- 5/6. numbers


def test_a_number_with_no_citation_is_caught(facts, result):
    line = a_priced_line(result)
    with pytest.raises(InvariantViolated, match="no provenance"):
        check(facts, broken(result, costed_lines=lines_with(result, line.line_id, provenance=[])))


def test_a_foreign_currency_slipping_into_the_quotation_is_caught(facts, result):
    """Money.__add__ refuses this, so reaching the invariant means something bypassed it."""
    line = a_priced_line(result)
    swapped = lines_with(result, line.line_id,
                         total=Money(amount=line.total.amount, currency="ZAR"))
    with pytest.raises(InvariantViolated, match="ZAR"):
        check(facts, broken(result, costed_lines=swapped))


# --------------------------------------------------------------------------- 7/8. the trail


def test_a_reference_to_a_question_that_does_not_exist_is_caught(facts, result):
    line = a_priced_line(result)
    with pytest.raises(InvariantViolated, match="unknown Q999"):
        check(facts, broken(result,
                            costed_lines=lines_with(result, line.line_id, review_refs=["Q999"])))


def test_a_question_with_no_document_behind_it_is_caught(facts, result):
    """An assertion a supplier cannot check is an opinion this system is stating."""
    unevidenced = ReviewItem(
        review_id="Q900", severity="blocking", line_id=None,
        field="test", question="Is this rate still valid?", evidence=[],
    )
    with pytest.raises(InvariantViolated, match="no document behind it"):
        check(facts, broken(result, needs_review=result.needs_review + [unevidenced]))


def test_an_unknown_severity_is_caught(facts, result):
    """"warning" reads fine and means the exit code stops working."""
    mislabelled = [result.needs_review[0].model_copy(update={"severity": "warning"})]
    with pytest.raises(InvariantViolated, match="unknown severity"):
        check(facts, broken(result, needs_review=mislabelled + result.needs_review[1:]))


# --------------------------------------------------------------------------- 9. the totals


def test_totals_that_do_not_account_for_every_line_are_caught(facts, result):
    """Three numbers that add up to fewer lines than exist is a fourth, invisible bucket."""
    off_by_one = result.totals.model_copy(update={"firm_line_count": result.totals.firm_line_count - 1})
    with pytest.raises(InvariantViolated, match="lines out of"):
        check(facts, broken(result, totals=off_by_one))


def test_an_unpriced_line_missing_from_the_reasons_is_caught(facts, result):
    """The count is the headline; the reasons are the only part anyone can act on."""
    silent = result.totals.model_copy(update={"unpriced_reasons": result.totals.unpriced_reasons[:-1]})
    with pytest.raises(InvariantViolated, match="unpriced_reasons"):
        check(facts, broken(result, totals=silent))


# --------------------------------------------------------------------------- 10. double count


def test_the_same_money_counted_twice_is_caught(facts, result):
    """Exposure means money this run did NOT charge. The double count has a shape.

    A rule that charges an amount and then also exposes it makes the quotation look riskier
    than it is, and the reviewer chasing that exposure finds it already billed. The check is
    that no review exposure equals the total of the line it hangs on.
    """
    line = a_priced_line(result)
    doubled = ReviewItem(
        review_id="Q901", severity="blocking", line_id=line.line_id,
        field="test", question="Was this charged twice?",
        evidence=[SourceRef(document="test", page=1, quote="test")],
        exposure=line.total,
    )
    with pytest.raises(InvariantViolated, match="counted twice"):
        check(facts, broken(result, needs_review=result.needs_review + [doubled]))
