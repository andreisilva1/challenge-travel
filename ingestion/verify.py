"""Proof that a number was read and not produced.

Two checks, in opposite directions.

  verbatim   - for every value the system holds, is it literally in the source?
               Catches invention. A value that fails is not corrected, it is rejected.
  unclaimed  - for every money-shaped token in the source, did something claim it?
               Catches silent loss, which sits above invention in the failure hierarchy
               precisely because nothing downstream complains about it.

The first is what makes the LLM's output usable at all: the model proposes, this file
checks the proposal against the page, and only the checked span becomes provenance.
"""

from __future__ import annotations

import re

MONEY_TOKEN = re.compile(r"\d[\d,]*\.\d{2}")


def normalise_for_match(text: str) -> str:
    """Whitespace and dashes only. Digits are never touched."""
    return re.sub(r"\s+", " ", text.replace("–", "-").replace("—", "-"))


def verbatim_span(needle: str, haystack: str) -> str | None:
    """The line of `haystack` containing `needle`, or None if it is not there at all.

    Returning the line rather than True gives the reviewer something to read next to the
    PDF. A bare boolean would prove the check ran and nothing else.
    """
    flat_needle = normalise_for_match(needle).strip()
    if not flat_needle:
        return None
    for line in haystack.splitlines():
        if flat_needle in normalise_for_match(line):
            return line.strip()
    return None


def digits_in_order(amount: str, cell: str) -> bool:
    """The repair path, for values the text layer interleaved rather than printed.

    The pack's rate column collides with the service name above it, so 395.00 reaches the
    text layer as 'lodg' + 'e3s95.00'. The amount is not a literal substring of the page
    and `verbatim_span` will correctly say so. What is still provable is weaker but not
    nothing: every digit of the claimed amount appears in the cell, in order, and no other
    digit does. Anything less than that exact condition is a guess and must not pass.
    """
    wanted = [c for c in amount if c.isdigit() or c in ".,"]
    found = [c for c in cell if c.isdigit() or c in ".,"]
    return wanted == found


def amount_span(amount: str, haystack: str) -> str | None:
    """Like `verbatim_span`, but an amount may not match inside a longer number.

    Plain substring search says 95.00 is present on a page whose only occurrence is the
    tail of 295.00, and 210.00 is present in 1,210.00. That check passes while proving
    nothing, which is worse than a check that fails: it manufactures confidence. The
    boundary is digit, comma and period on either side.
    """
    flat = normalise_for_match(amount).strip()
    pattern = re.compile(r"(?<![\d.,])" + re.escape(flat) + r"(?![\d])")
    for line in haystack.splitlines():
        if pattern.search(normalise_for_match(line)):
            return line.strip()
    return None


def check_amount(
    amount: str, page_text: str, cell: str | None = None, near: str | None = None
) -> tuple[bool, str | None]:
    """(verified, evidence line). `cell` is the raw grid cell, for glued text.

    `near` narrows the search to lines that also mention the product. Without it, 210.00
    verifies against the first line on the page carrying that figure - which for the Cape
    Town transfer is the Camissa Classic Room row. The number would be right, the citation
    would point at an unrelated product, and the reviewer would be checking the wrong line
    while believing the system had shown its work.
    """
    lines = [ln.strip() for ln in page_text.splitlines() if ln.strip()]
    if near:
        key = normalise_for_match(near).lower()[:24]
        narrowed = [ln for ln in lines if key in normalise_for_match(ln).lower()]
        lines = narrowed or lines

    for line in lines:
        if amount_span(amount, line) is not None:
            return True, line

    if cell is not None and digits_in_order(amount, cell):
        # The text layer interleaved the columns. Quote the whole line rather than the
        # mangled cell: a reviewer needs to find this row in the PDF, and "ou9n5t.a0i0n"
        # is not something anyone can look up.
        for line in lines:
            if cell in line:
                return True, line
        return True, cell

    return False, None


def unclaimed_numbers(
    pages: dict[int, str], claimed: set[str]
) -> list[tuple[int, str, str]]:
    """(page, token, line) for every money-shaped token nothing in the system holds.

    An unclaimed token is not automatically an error - a pack can mention a figure the
    quotation never books. It is a question: the run reports it rather than deciding, and
    a reviewer says whether it mattered. The alternative is a total that is quietly short
    by one clause nobody parsed.
    """
    out: list[tuple[int, str, str]] = []
    for page, text in pages.items():
        for line in text.splitlines():
            for token in MONEY_TOKEN.findall(line):
                if token.replace(",", "") not in claimed:
                    out.append((page, token, line.strip()))
    return out


def spans_present(values: list[str], source: str) -> list[str]:
    """The values that are NOT literal spans of `source`.

    Applied to model output. When the model is asked to split "1 x Beach Villa & 1 x Beach
    Villa Grande" it must return substrings of that line; anything it returns which is not
    in the line was written by the model, and a room type the model wrote is a room type
    that will not match a rate.
    """
    flat = normalise_for_match(source).lower()
    return [v for v in values if normalise_for_match(v).lower() not in flat]
