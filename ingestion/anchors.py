"""The anchor table: supplier prose -> rule_key, by literal phrase.

Why this is not a model call. On the six clauses of this pack, asked to pick one key from
a closed list of fourteen, qwen2.5:7b scored 2/6, mistral 3/6 and llama3.2 2/6
(ARCHITECTURE.md 1.2). The instructive failure was not a refusal - it was
`complimentary_transfer` returned for the Kudu conservation levy, with an amount of 35.00
attached. That is a confident wrong answer that would have applied a zeroing rule to a
compulsory charge, and no downstream check on amounts would have caught it, because the
amount was right.

`rule_key` is a foreign key between a sentence a supplier wrote and a function a human
reviewed. A foreign key resolved probabilistically is not a foreign key.

Every entry below carries the section it was read from and the phrase as the pack writes
it, so the table itself can be audited against the source. If two entries match the same
sentence the result is `unclassified` with both candidates recorded - never a coin flip.
"""

from __future__ import annotations

import re
from decimal import Decimal

import pdfplumber

from domain import CURRENCY, Condition, Money, RuleKey, SourceRef
from ingestion.extract import normalise, read_tables

# --------------------------------------------------------------------------- the table


class Anchor:
    """One literal phrase that identifies one rule, with where it was read from."""

    def __init__(self, rule_key: RuleKey, phrase: str, pack_section: str, quote: str):
        self.rule_key = rule_key
        self.phrase = phrase
        self.pack_section = pack_section
        self.quote = quote


ANCHORS: list[Anchor] = [
    Anchor(RuleKey.TRAILER_SUPPLEMENT, "trailer supplement", "2a",
           "2a. Trailer supplement, required for groups of 6 pax and above..."),
    Anchor(RuleKey.PER_GROUP_EXTRA, "per group extra", "2b",
           "...subject to a Per Group Extra of 140.00 per group..."),
    Anchor(RuleKey.CONSERVATION_LEVY, "conservation levy", "3, 4",
           "Marula Reserve Conservation Levy: 28.00 per person per night, compulsory..."),
    Anchor(RuleKey.COMPLIMENTARY_TRANSFER, "complimentary scheduled road transfer", "3",
           "Complimentary scheduled road transfer from Hoedspruit Airport (HDS) is included..."),
    Anchor(RuleKey.TRIPLE_OCCUPANCY, "triple occupancy", "4a",
           "4a. Triple occupancy is available in Luxury Suites on request."),
    Anchor(RuleKey.SINGLE_OCCUPANCY, "single occupancy", "1, 4",
           "Single occupancy: no reduction."),
    Anchor(RuleKey.SINGLE_OCCUPANCY, "single supplement", "4",
           "Single supplement applied."),
    Anchor(RuleKey.GATE_FEES, "reserve gate charges", "5",
           "Reserve gate charges, where levied, are additional: Vehicle Entrance Fee 40.00..."),
    Anchor(RuleKey.CAPACITY_CONSTRAINT, "max ", "2, 4",
           "Max 2 adults. See note 4a for triple. / Max 7 pax per vehicle."),
    Anchor(RuleKey.CAPACITY_CONSTRAINT, "min ", "4",
           "Min 2 nights."),
    Anchor(RuleKey.CAPACITY_CONSTRAINT, "minimum ", "2",
           "Half day. Minimum 2 pax."),
    Anchor(RuleKey.TAX_INCLUDED, "included in the rates shown", "1, 6",
           "City tourism levy of 1% is included in the rates shown."),
    Anchor(RuleKey.CARRIED_FORWARD_TARIFF, "carried forward", "header, 7",
           "...the most recent published tariff is carried forward..."),
    Anchor(RuleKey.CARRIED_FORWARD_TARIFF, "had not been released", "7",
           "2027 tariffs for Mozambique air transfers had not been released..."),
    Anchor(RuleKey.AIR_NOT_CARRIED, "not carried in this pack", "8",
           "Airfares are not carried in this pack."),
    Anchor(RuleKey.CORRESPONDENCE_PRECEDENCE, "takes precedence over this pack", "9",
           "Where a rate has been superseded by written supplier correspondence, the "
           "correspondence takes precedence over this pack."),
    # NO_RATE_REQUOTE is anchored but has no function in rules.RULES. It is not a rule that
    # fires on a clause: it is the policy the costing engine cites when it finds no rate at
    # all, and only the engine can observe that. Anchoring it means the review item can
    # quote the supplier's own sentence instead of asserting the policy on its own account.
    Anchor(RuleKey.NO_RATE_REQUOTE, "must be re quoted", "9",
           "Where a service is shown in an operational quotation but no corresponding rate "
           "appears in this pack, the service must be re-quoted with the supplier..."),
]


# --------------------------------------------------------------------------- matching

_MONEY = re.compile(r"(\d[\d,]*\.\d{2})")

# The unit clause that follows an amount. Bounded by the unit vocabulary rather than by
# punctuation: "140.00 per group covering permits, parking and ..." would otherwise yield
# "per group covering permits" as a unit, and units.py would have to guess at prose.
_UNIT_WORDS = (
    "per person pax group vehicle room chalet suite villa night entry transfer "
    "way movement sharing one day"
).split()
_UNIT_AFTER = re.compile(
    r"\d[\d,]*\.\d{2}\s+(per\s(?:(?:" + "|".join(_UNIT_WORDS) + r")\s*)+)", re.IGNORECASE
)
# Fees named immediately before their own amount, as section 5 writes them.
_NAMED_FEE = re.compile(r"([A-Z][A-Za-z ]*?Fee)\s+(\d[\d,]*\.\d{2})")


def match_text(text: str) -> tuple[RuleKey, list[RuleKey]]:
    """(rule_key, candidates). Two matches means unclassified, not a preference."""
    haystack = " " + re.sub(r"\s+", " ", normalise(text).replace("-", " ")).lower() + " "
    hits: list[RuleKey] = []
    for anchor in ANCHORS:
        if anchor.phrase in haystack and anchor.rule_key not in hits:
            hits.append(anchor.rule_key)
    if not hits:
        return RuleKey.UNCLASSIFIED, []
    if len(hits) > 1:
        return RuleKey.UNCLASSIFIED, hits
    return hits[0], []


# --------------------------------------------------------------------------- sentences

# Only the full stop ends a sentence here. Section 5 states two gate fees separated by a
# semicolon inside one sentence; breaking on ";" split the amounts away from the clause
# that anchors them, and the half carrying 40.00 and 25.00 matched no anchor and vanished.
# Losing money silently is the worst failure in the hierarchy, so the weaker separators
# are not separators.
_SENTENCE_BREAK = re.compile(r"(?<![0-9])(?<=[.])\s+(?=[A-Z])")


def split_sentences(text: str) -> list[str]:
    """Sentence per clause, without breaking '65.00 per group' at the decimal point.

    A leading marker like '2b.' splits off as a two-character fragment; it is glued back
    onto the sentence it introduces rather than dropped, because the section number is
    part of how a reviewer finds the clause in the PDF.
    """
    parts = [p.strip() for p in _SENTENCE_BREAK.split(text) if p.strip()]
    merged: list[str] = []
    for part in parts:
        if merged and len(merged[-1]) <= 5:
            merged[-1] = f"{merged[-1]} {part}"
        else:
            merged.append(part)
    return merged


def prose_paragraphs(path: str) -> list[tuple[int, str | None, str]]:
    """(page, section, text) for every line of the pack that is not inside a table.

    Prose wraps mid-sentence in the text layer, so contiguous non-table lines within a
    section are joined before sentences are cut. A clause split across a line break would
    otherwise lose either its amount or its condition.
    """
    section_pattern = re.compile(r"^(\d)\.\s+(.+)$")
    out: list[tuple[int, str | None, str]] = []

    with pdfplumber.open(path) as pdf:
        for page, plumber_page in enumerate(pdf.pages, 1):
            boxes = [(t.bbox[1], t.bbox[3]) for t in plumber_page.find_tables()]
            section: str | None = None
            buffer: list[str] = []

            for line in plumber_page.extract_text_lines():
                text, top = line["text"].strip(), line["top"]
                if not text:
                    continue
                inside_table = any(a <= top <= b for a, b in boxes)
                heading = section_pattern.match(text)
                if heading or inside_table:
                    if buffer:
                        out.append((page, section, " ".join(buffer)))
                        buffer = []
                    if heading:
                        section = heading.group(1)
                    continue
                buffer.append(text)

            if buffer:
                out.append((page, section, " ".join(buffer)))

    return out


# --------------------------------------------------------------------------- conditions


def extract_conditions(path: str) -> list[Condition]:
    """Every clause of the pack that can change, zero or block money.

    Sentences that match no anchor are not turned into conditions. That is safe only
    because verify.unclaimed_numbers() separately proves that no *amount* was left behind
    by this pass - the sweep is what makes silence here honest.
    """
    document = path.split("/")[-1].split("\\")[-1]
    conditions: list[Condition] = []

    def emit(page: int, section: str | None, sentence: str, label: str | None,
             amount_text: str | None, unit_raw: str | None) -> None:
        rule_key, candidates = match_text(sentence)
        if rule_key is RuleKey.UNCLASSIFIED and not candidates:
            return
        conditions.append(
            Condition(
                condition_id=f"C{len(conditions) + 1:02d}",
                rule_key=rule_key,
                candidates=candidates,
                quote=sentence,
                label=label,
                amount=Money(amount=Decimal(amount_text.replace(",", "")), currency=CURRENCY)
                if amount_text
                else None,
                unit_raw=unit_raw,
                source=SourceRef(document=document, page=page, section=section, quote=sentence),
            )
        )

    for page, section, paragraph in prose_paragraphs(path):
        for sentence in split_sentences(paragraph):
            named = _NAMED_FEE.findall(sentence)
            if len(named) > 1:
                # Section 5 states two different gate fees in one sentence. One condition
                # each, so a rule can answer for the fee the quotation actually booked
                # rather than picking whichever amount came first.
                for fee, amount in named:
                    tail = sentence.split(amount, 1)[1]
                    unit = _UNIT_AFTER.search(f"{amount} {tail}")
                    emit(page, section, sentence, fee.strip(), amount,
                         unit.group(1).strip() if unit else None)
                continue
            amounts = _MONEY.findall(sentence)
            unit = _UNIT_AFTER.search(sentence)
            emit(
                page,
                section,
                sentence,
                None,
                amounts[0] if len(amounts) == 1 else None,
                unit.group(1).strip() if unit else None,
            )

    # Table Notes cells carry the capacity limits, which are conditions in a column rather
    # than in a paragraph: "Max 2 adults" is what makes 5 guests in 2 Luxury Suites a
    # question rather than an arithmetic detail.
    for page, page_tables in read_tables(path).items():
        for _, table in page_tables:
            header = tuple((c or "").split("\n")[0].strip() for c in table[0])
            if "Notes" not in header:
                continue
            notes_at = header.index("Notes")
            product_at = 0
            for row in table[1:]:
                note = (row[notes_at] or "").strip()
                product = (row[product_at] or "").strip()
                for sentence in split_sentences(note):
                    emit(page, None, sentence, product, None, None)

    return conditions
