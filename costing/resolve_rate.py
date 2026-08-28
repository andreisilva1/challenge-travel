"""Choosing a rate, and saying so out loud.

Picking which price applies is a commercial decision. It does not get to live inside an
`if` and disappear: every call returns a RateResolution naming the winner, the losers and
the rule that decided, and that record is output, not logging.

Three separate questions, in this order:
  1. Has correspondence superseded the pack?   (pack section 9 - by document class)
  2. Which product does this line name?        (exact for rooms, scored for services)
  3. Is that rate valid for this night?        (validity window, season band)

Thresholds here were set by measuring this pack, not chosen by feel; the numbers are in
the comments beside them so a reviewer can disagree with the evidence rather than with a
preference.
"""

from __future__ import annotations

import difflib
import re
from datetime import date

from domain import (
    BookedLine,
    Candidate,
    Confidence,
    RateOverride,
    RateRecord,
    RateResolution,
)

# Below this, the best match is not a match. "Zambezi Air" scores 0.364 against its nearest
# product in the whole pack, which is a villa: air is genuinely not in this pack.
FLOOR = 0.60

# The winner must beat the runner-up by this much. Absolute score is a poor signal -
# "One Way Assistance" scores only 0.720 against the right product - but the gap to the
# runner-up (0.467) is decisive. Where the gap is small the two really are confusable.
MARGIN = 0.10

# Awarded when the pack's product contains every word of the booked description. "Helicopter
# Transfer" scores 0.469 against "Helicopter transfer VNX (Vilanculos) - Benguerra island
# lodges" purely because the product string is three times longer; containment is the real
# evidence there. Deliberately below the scores of genuine near-identical matches, so it
# rescues a short description without ever outranking a literal one.
CONTAINMENT = 0.75


def _key(text: str | None) -> str:
    return re.sub(r"[^a-z0-9 ]", " ", re.sub(r"\s+", " ", (text or "").replace("–", "-").lower())).strip()


def _tokens(text: str) -> set[str]:
    return {t for t in _key(text).split() if len(t) > 2}


def score(booked: str, product: str) -> float:
    """Similarity, with containment as a rescue and never as an override.

    The rescue only fires below FLOOR. Applying it everywhere would flatten the difference
    between "Transfer CIA to Zone 1, 06H00-21H00" and "...21H00-06H00", which contain each
    other's words exactly and differ only in the order that decides a 75.00 surcharge.
    """
    ratio = difflib.SequenceMatcher(None, _key(booked), _key(product)).ratio()
    if ratio < FLOOR and _tokens(booked) and _tokens(booked) <= _tokens(product):
        return CONTAINMENT
    return ratio


def _same_supplier(line: BookedLine, supplier: str) -> bool:
    return _key(line.supplier_or_property) == _key(supplier)


def _candidates(line: BookedLine, rates: list[RateRecord]) -> list[RateRecord]:
    """Scoped to the property the quotation names, when it names one.

    Without scoping "Family Suite" competes with "Luxury Suite" across four properties. The
    quotation states the property on every accommodation line, so this is free precision.
    """
    if line.supplier_or_property:
        scoped = [r for r in rates if _same_supplier(line, r.supplier)]
        if scoped:
            return scoped
    return rates


def _as_candidate(rate: RateRecord, value: float) -> Candidate:
    return Candidate(rate_id=rate.rate_id, product=rate.product, score=round(value, 3), source=rate.source)


# --------------------------------------------------------------------------- product match


def match_accommodation(line: BookedLine, rates: list[RateRecord]) -> RateResolution:
    """A room type matches by name or it does not match at all.

    This is the strictest rule in the system and it is strict on purpose. The quotation
    books a "Beach Villa Grande"; Ilha Azul sells a "Beach Villa", an "Infinity Beach
    Villa" and a "Presidential Villa". Fuzzy matching scores Beach Villa at 0.759 and would
    charge three nights at 780.00 for a villa this supplier has never quoted. Refusing to
    match is the only answer that leaves the question open, and the question - what is a
    Beach Villa Grande and what does it cost - is the whole point of the exercise.
    """
    pool = _candidates(line, rates)
    wanted = _key(line.room_type)
    exact = [r for r in pool if _key(r.product) == wanted]

    if exact:
        winner = exact[0]
        return RateResolution(
            winner=winner.rate_id,
            winner_source=winner.source,
            rule="room type matched the supplier's product name exactly",
            rejected=[],
            reason=f"{line.room_type!r} is listed by {winner.supplier}.",
        )

    near = sorted(((score(line.room_type or "", r.product), r) for r in pool), reverse=True, key=lambda t: t[0])
    return RateResolution(
        winner=None,
        winner_source=None,
        rule="no product of this supplier carries this room type",
        rejected=[_as_candidate(r, s) for s, r in near[:3]],
        reason=(
            f"{line.supplier_or_property} does not list {line.room_type!r}. "
            "Similar names were found and rejected: a room type that is nearly right is a "
            "different room at a different price."
        ),
    )


def match_service(line: BookedLine, rates: list[RateRecord]) -> RateResolution:
    """Everything that is not a room: transfers, tours, activities, assistance, air."""
    pool = _candidates(line, rates)
    ranked = sorted(((score(line.description, r.product), r) for r in pool), reverse=True, key=lambda t: t[0])
    if not ranked:
        return RateResolution(
            winner=None, winner_source=None, rule="no rates for this supplier",
            rejected=[], reason=f"The pack lists nothing under {line.supplier_or_property!r}.",
        )

    best_score, best = ranked[0]
    runner_up = ranked[1][0] if len(ranked) > 1 else 0.0
    rejected = [_as_candidate(r, s) for s, r in ranked[1:4]]

    if best_score < FLOOR:
        return RateResolution(
            winner=None, winner_source=None,
            rule=f"best match {best_score:.2f} is below the floor of {FLOOR:.2f}",
            rejected=[_as_candidate(best, best_score), *rejected],
            reason=f"No product in the pack describes {line.description!r}.",
        )

    if best_score - runner_up < MARGIN:
        return RateResolution(
            winner=None, winner_source=None,
            rule=f"two products are within {MARGIN:.2f} of each other",
            rejected=[_as_candidate(best, best_score), *rejected],
            reason=(
                f"{line.description!r} is as close to {ranked[1][1].product!r} as it is to "
                f"{best.product!r}. Guessing between them would be a coin flip on money."
            ),
        )

    return RateResolution(
        winner=best.rate_id,
        winner_source=best.source,
        rule=f"best match {best_score:.2f}, clear of the runner-up by {best_score - runner_up:.2f}",
        rejected=rejected,
        reason=f"{line.description!r} matched {best.product!r}.",
    )


# --------------------------------------------------------------------------- precedence


def find_override(
    line: BookedLine, overrides: list[RateOverride]
) -> RateOverride | None:
    """Correspondence beats the pack, by document class and not by date.

    Pack section 9 says written supplier correspondence takes precedence. The Camissa email
    was sent on 12 August 2026 and the pack was compiled on 14 August 2026, so the email is
    the *older* document and still wins. Any rule that sorted by recency would silently
    reinstate the superseded rates and be off by 220.00 with no sign that anything happened.
    """
    wanted = _key(line.room_type or line.description)
    for override in overrides:
        if line.supplier_or_property and not _same_supplier(line, override.supplier):
            continue
        if _key(override.product) == wanted:
            return override
    return None


# --------------------------------------------------------------------------- validity


def rate_for_night(
    rates: list[RateRecord], rate_id: str, night: date
) -> tuple[list[RateRecord], Confidence, str | None]:
    """Every rate under the same product name that could apply on `night`.

    Returns a list, not a rate. Marula's Green Season runs "01 Apr 27 - 09 Jul 27" and Peak
    Season runs "09 Jul 27 - 31 Oct 27": the pack prints 09 July in both, and the Halloran
    party checks in on 09 July. Two rates cover that night at 620.00 and 890.00, and there
    is no reading of the document that decides between them. Returning one of them would be
    the invisible assumption; returning both is the truth.
    """
    anchor = next(r for r in rates if r.rate_id == rate_id)
    family = [r for r in rates if r.supplier == anchor.supplier and r.product == anchor.product]

    seasonal = [r for r in family if r.season is not None]
    if seasonal:
        covering = [r for r in seasonal if r.season.covers(night)]
        if len(covering) == 1:
            return covering, Confidence.CONTRACTED, None
        if len(covering) > 1:
            bands = " and ".join(f"{r.season.name} ({r.season.raw})" for r in covering)
            return covering, Confidence.ASSUMED, f"{night:%d %b %y} falls inside {bands}."
        return [], Confidence.UNRESOLVED, f"No season band covers {night:%d %b %y}."

    valid = [r for r in family if r.validity is None or r.validity.covers(night)]
    if valid:
        return valid[:1], Confidence.CONTRACTED, None

    # Nothing covers the night. The pack itself says what it did in that case: it carried
    # the last published tariff forward and marked it indicative. That is a usable number
    # with a named weakness, which is not the same thing as a contracted rate.
    carried = [r for r in family if r.validity and r.validity.carried_forward]
    if carried:
        return carried[:1], Confidence.EXPIRED, carried[0].confidence_reason
    return [], Confidence.UNRESOLVED, f"No rate is valid on {night:%d %b %y}."
