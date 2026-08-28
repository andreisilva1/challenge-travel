"""Domain model.

Every type here exists because the commercial problem requires it, not because the
implementation needed another layer. Read this file first: the rest of the system is
transformations between these types.

Two rules hold everywhere:
  - Money is Decimal and carries a currency. Never float.
  - A commercial figure without a SourceRef is incomplete and must not exist.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, ConfigDict, field_validator

CURRENCY = "USD"  # Rate pack: "Currency: USD unless otherwise stated"


# --------------------------------------------------------------------------- provenance


class SourceRef(BaseModel):
    """Where a fact came from, in terms a human can check by hand.

    `quote` is the literal excerpt from the document's text layer. It is not a
    convenience field: the non-technical reviewer reads it side by side with the PDF,
    and the verbatim check (ingestion/verify.py) uses it as the evidence that a number
    was read rather than invented.
    """

    model_config = ConfigDict(frozen=True)

    document: str
    page: int
    section: str | None = None
    quote: str


class Money(BaseModel):
    model_config = ConfigDict(frozen=True)

    amount: Decimal
    currency: str = CURRENCY

    @field_validator("amount", mode="before")
    @classmethod
    def _no_floats(cls, v: object) -> object:
        # A float that reached here would already have lost precision. Refuse it at the
        # boundary rather than rounding the evidence away.
        if isinstance(v, float):
            raise TypeError("Money.amount must not be built from float; use Decimal or str")
        return v

    def __add__(self, other: Money) -> Money:
        if self.currency != other.currency:
            raise ValueError(f"refusing to add {self.currency} to {other.currency}")
        return Money(amount=self.amount + other.amount, currency=self.currency)

    def times(self, factor: int) -> Money:
        return Money(amount=self.amount * factor, currency=self.currency)

    def __str__(self) -> str:
        return f"{self.currency} {self.amount:,.2f}"


ZERO = Money(amount=Decimal("0"))


# --------------------------------------------------------------------------- confidence


class Confidence(str, Enum):
    """Ordinal. Derived from structural facts, never reported by the model.

    A CostedLine is always the weakest of the facts it was built from, so this needs an
    ordering — see `rank` and `weakest`.
    """

    CONTRACTED = "contracted"
    SUPERSEDED_BY_CORRESPONDENCE = "superseded_by_correspondence"
    ASSUMED = "assumed"
    EXPIRED = "expired"
    UNRESOLVED = "unresolved"

    @property
    def rank(self) -> int:
        return _CONFIDENCE_RANK[self]


_CONFIDENCE_RANK: dict[Confidence, int] = {
    Confidence.CONTRACTED: 5,
    Confidence.SUPERSEDED_BY_CORRESPONDENCE: 4,
    Confidence.ASSUMED: 3,
    Confidence.EXPIRED: 2,
    Confidence.UNRESOLVED: 1,
}


def weakest(*levels: Confidence) -> Confidence:
    return min(levels, key=lambda c: c.rank)


# --------------------------------------------------------------------------- unit algebra


class Denominator(str, Enum):
    """What the rate is charged per. Sits under the money."""

    PERSON = "person"
    PERSON_SHARING = "person_sharing"
    ROOM = "room"
    CHALET = "chalet"
    SUITE = "suite"
    VILLA = "villa"
    VEHICLE = "vehicle"
    GROUP = "group"


class Period(str, Enum):
    """What the rate is charged for, once per what."""

    NIGHT = "night"
    ONE_WAY = "one_way"
    MOVEMENT = "movement"
    ENTRY = "entry"
    TRANSFER = "transfer"
    FLAT = "flat"


class Unit(BaseModel):
    """A parsed unit, with the literal text it was parsed from.

    An unparsed unit is not a guess: it is `None` on the RateRecord and the line goes to
    needs_review. Quantity arithmetic without a known denominator is how a total gets
    silently multiplied by the wrong number of people.
    """

    model_config = ConfigDict(frozen=True)

    denominator: Denominator
    period: Period
    raw: str


# --------------------------------------------------------------------------- rule keys


class RuleKey(str, Enum):
    """Foreign key between a sentence in a supplier document and a reviewed function.

    Assigned by literal anchor (ingestion/anchors.py), never by the model — measured, see
    ARCHITECTURE.md 1.2. NO_RATE_REQUOTE is deliberately absent from the anchor table: it
    is a statement about the *absence* of a rate and only the costing engine can observe
    it (rules.py has no function for it).
    """

    TRAILER_SUPPLEMENT = "trailer_supplement"
    PER_GROUP_EXTRA = "per_group_extra"
    CONSERVATION_LEVY = "conservation_levy"
    COMPLIMENTARY_TRANSFER = "complimentary_transfer"
    TRIPLE_OCCUPANCY = "triple_occupancy"
    SINGLE_OCCUPANCY = "single_occupancy"
    GATE_FEES = "gate_fees"
    CAPACITY_CONSTRAINT = "capacity_constraint"
    TAX_INCLUDED = "tax_included"
    CARRIED_FORWARD_TARIFF = "carried_forward_tariff"
    AIR_NOT_CARRIED = "air_not_carried"
    CORRESPONDENCE_PRECEDENCE = "correspondence_precedence"
    NO_RATE_REQUOTE = "no_rate_requote"
    UNCLASSIFIED = "unclassified"


# --------------------------------------------------------------------------- ingestion out


class Modifier(BaseModel):
    """An `Included: N x <label> per <denominator>` line from the quotation.

    It hangs off the BookedLine above it. It is never a service of its own — that mistake
    is what the LLM made on this document (ARCHITECTURE.md 1.2).
    """

    label: str
    quantity: int
    denominator_hint: str | None
    source: SourceRef


class BookedLine(BaseModel):
    """One billable service as the operational quotation states it.

    This is what was sold. Every one of these must end in exactly one CostedLine —
    priced, or explicitly unpriced with a reason. There is no third outcome.
    """

    line_id: str
    region: str | None
    service_type: str  # accommodation | transfer | activity | day_tour | meet_greet | flight | unknown
    description: str
    supplier_or_property: str | None = None
    room_type: str | None = None
    basis: str | None = None
    quantity: int = 1
    unit_hint: str | None = None
    pickup: str | None = None
    dropoff: str | None = None
    date_start: date
    date_end: date | None = None
    modifiers: list[Modifier] = []
    source: SourceRef

    @property
    def nights(self) -> int | None:
        # Not in the document. Derived in Python, never asked of the model.
        if self.date_end is None:
            return None
        return (self.date_end - self.date_start).days


class SeasonBand(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    start: date
    end: date
    raw: str

    def covers(self, day: date) -> bool:
        return self.start <= day <= self.end


class Validity(BaseModel):
    model_config = ConfigDict(frozen=True)

    start: date
    end: date
    raw: str
    carried_forward: bool = False

    def covers(self, day: date) -> bool:
        return self.start <= day <= self.end


class RateRecord(BaseModel):
    """A price as a supplier document states it."""

    rate_id: str
    supplier: str
    product: str
    basis: str | None = None
    price: Money
    unit: Unit | None
    unit_raw: str
    validity: Validity | None = None
    season: SeasonBand | None = None
    confidence: Confidence
    confidence_reason: str | None = None
    source: SourceRef


class RateOverride(BaseModel):
    """A price from written supplier correspondence.

    Pack section 9: "Where a rate has been superseded by written supplier correspondence,
    the correspondence takes precedence over this pack." Precedence is by document class,
    not by date — the Camissa email (12 Aug 2026) is older than the pack (compiled
    14 Aug 2026) and still wins.
    """

    override_id: str
    supplier: str
    product: str
    price: Money
    unit: Unit | None
    unit_raw: str
    sent_at: date
    effective_scope: str
    supersession_claim: str | None
    source: SourceRef


class Condition(BaseModel):
    """A sentence of supplier prose that changes money, zeroes money, or flags a line."""

    condition_id: str
    rule_key: RuleKey
    quote: str
    label: str | None = None  # the thing the clause prices, when it names one
    amount: Money | None = None
    unit_raw: str | None = None
    applies_when_text: str | None = None
    candidates: list[RuleKey] = []  # populated when two anchors tie; then rule_key is UNCLASSIFIED
    source: SourceRef


class ExtractedFacts(BaseModel):
    """The ingestion/costing boundary, serialised to intermediate.json.

    Costing reads this and nothing else: no PDFs, no prompts, no model responses.
    """

    quotation_ref: str
    travellers: int
    travel_start: date
    travel_end: date
    booked_lines: list[BookedLine]
    rates: list[RateRecord]
    overrides: list[RateOverride]
    conditions: list[Condition]
    system_warnings: list[str] = []


# --------------------------------------------------------------------------- costing out


class Candidate(BaseModel):
    """A rate that was considered. Losers are output, not debug noise."""

    rate_id: str
    product: str
    score: float
    source: SourceRef


class RateResolution(BaseModel):
    """Which rate won, which lost, and the rule that decided.

    Choosing a rate is a commercial decision. It does not get to live inside an `if`.
    """

    winner: str | None
    winner_source: SourceRef | None
    rule: str
    rejected: list[Candidate] = []
    reason: str


class ReviewItem(BaseModel):
    """A question the system cannot answer honestly, addressed to a human.

    `question` must be answerable by a supplier as written. If it is not a question, the
    item is malformed.
    """

    review_id: str
    severity: str  # blocking | informational
    line_id: str | None
    field: str
    question: str
    evidence: list[SourceRef]
    exposure: Money | None = None


class CostedLine(BaseModel):
    line_id: str
    description: str
    region: str | None
    date_start: date
    date_end: date | None
    quantity: int
    unit_price: Money | None
    units_charged: int | None
    calculation: str | None  # human-readable arithmetic, e.g. "2 rooms x 4 nights x USD 340.00"
    total: Money | None
    confidence: Confidence
    confidence_reason: str | None
    resolution: RateResolution
    provenance: list[SourceRef]
    review_refs: list[str] = []


class Totals(BaseModel):
    """Three numbers, deliberately. There is no field called `total`.

    Collapsing firm and exposure into one number is exactly the "clean, confident, wrong"
    output the brief calls worse than useless.
    """

    firm: Money
    firm_line_count: int
    exposure: Money
    exposure_line_count: int
    unpriced_line_count: int
    unpriced_reasons: list[str]


class CostedQuotation(BaseModel):
    quotation_ref: str
    travellers: int
    currency: str = CURRENCY
    costed_lines: list[CostedLine]
    needs_review: list[ReviewItem]
    totals: Totals
    system_warnings: list[str] = []
