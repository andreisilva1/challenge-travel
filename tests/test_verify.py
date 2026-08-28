"""The two checks that decide whether a number is evidence or a claim.

Both of these were written after a check that passed while proving nothing. Substring
search reported 95.00 as present on a page whose only occurrence was the tail of 295.00,
and reported 210.00 as present inside 1,210.00 - manufacturing confidence, which is worse
than failing, because a failure is visible.
"""

from __future__ import annotations

from ingestion.verify import (
    amount_span,
    check_amount,
    digits_in_order,
    spans_present,
    unclaimed_numbers,
    verbatim_span,
)

PAGE = """Room type Rate Unit Validity
Family Suite 340.00 per room per night 01 Jan 27 - 31 Dec 27
Deluxe Sea Facing 295.00 per room per night 01 Jan 27 - 31 Dec 27
Airport transfer 1,210.00 per vehicle one way
SLOW Walks & Hikes - Rise & Climb, Table Mou9n5t.a0i0n per person"""


# --------------------------------------------------------------------------- boundaries


def test_an_amount_never_matches_inside_a_longer_number():
    """95.00 is not on this page. 295.00 is, and they are not the same money."""
    assert amount_span("95.00", PAGE) is None
    assert amount_span("210.00", PAGE) is None       # only 1,210.00 is there
    assert amount_span("1,210.00", PAGE) is not None
    assert amount_span("295.00", PAGE) is not None


def test_a_verified_amount_returns_the_line_a_human_can_read():
    """A boolean would prove the check ran and nothing else."""
    assert "Deluxe Sea Facing" in amount_span("295.00", PAGE)


def test_verbatim_span_finds_the_quote_across_dash_variants():
    """Quotes keep the document's own en dash; matching normalises. Both must work."""
    assert verbatim_span("01 Jan 27 – 31 Dec 27", PAGE) is not None
    assert verbatim_span("01 Jan 27 - 31 Dec 27", PAGE) is not None
    assert verbatim_span("01 Jan 28 - 31 Dec 28", PAGE) is None


# --------------------------------------------------------------------------- the repair


def test_interleaved_text_is_repaired_only_on_an_exact_digit_sequence():
    """The pack's rate column collides with the service name: 95.00 arrives as 9n5t.a0i0n.

    The amount is provable but not literal, so the weaker check applies - every digit of
    the claimed amount, in order, and no other digit. Anything less exact is a guess.
    """
    assert digits_in_order("95.00", "Table Mou9n5t.a0i0n")
    assert not digits_in_order("95.00", "Table Mou9n5t.a0i0n 2")   # a digit that is not claimed
    assert not digits_in_order("95.00", "Table Mountain")          # nothing to prove
    assert not digits_in_order("59.00", "Table Mou9n5t.a0i0n")     # right digits, wrong order


def test_check_amount_falls_back_to_the_cell_and_still_cites_the_row():
    ok, evidence = check_amount("95.00", PAGE, cell="Mou9n5t.a0i0n")
    assert ok and "SLOW Walks" in evidence


def test_near_narrows_the_citation_to_the_right_product():
    """Without `near`, 340.00 would verify against whichever line carried it first.

    The number would be right and the citation would point at an unrelated row - a reviewer
    checking the wrong line while believing the system had shown its work.
    """
    ok, evidence = check_amount("340.00", PAGE, near="Family Suite")
    assert ok and "Family Suite" in evidence


def test_an_amount_that_is_not_there_is_rejected_not_corrected():
    ok, evidence = check_amount("999.00", PAGE)
    assert not ok and evidence is None


# --------------------------------------------------------------------------- the sweep


def test_unclaimed_numbers_finds_what_nothing_accounted_for():
    """The check that catches silent loss: a clause nobody parsed always looks like this."""
    claimed = {"340.00", "295.00", "1210.00"}
    left = unclaimed_numbers({1: PAGE}, claimed)
    assert left == []

    left = unclaimed_numbers({1: PAGE}, {"340.00", "295.00"})
    assert [token for _, token, _ in left] == ["1,210.00"]


# --------------------------------------------------------------------------- model output


def test_model_output_must_be_a_literal_span_of_what_it_was_given():
    """A room type the model wrote is a room type that will match no rate."""
    source = "1 x Beach Villa & 1 x Beach Villa Grande on a Fully Inclusive basis"
    assert spans_present(["Beach Villa", "Beach Villa Grande"], source) == []
    assert spans_present(["Beach Villa Deluxe"], source) == ["Beach Villa Deluxe"]
