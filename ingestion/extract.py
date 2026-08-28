"""Text layer and deterministic skeletons.

Nothing in this file calls a model. The quotation skeleton and the rate pack tables are
literal anchors and pdfplumber grids, because that is what the measurement supported
(ARCHITECTURE.md 1.2): the 7B model invented five services where the document has one.

The one genuinely semantic residue - a room line encoding two units, "1 x A & 1 x B" -
is handed to the LLM by the caller, not here.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal

import pdfplumber

from ingestion.verify import check_amount

from domain import (
    CURRENCY,
    BookedLine,
    Confidence,
    Modifier,
    Money,
    RateOverride,
    RateRecord,
    SeasonBand,
    SourceRef,
    Validity,
)

EN_DASH = "–"
EM_DASH = "—"


def normalise(text: str) -> str:
    """For matching only. Quotes keep the document's own characters."""
    return text.replace(EN_DASH, "-").replace(EM_DASH, "-")


def read_pages(path: str) -> dict[int, str]:
    with pdfplumber.open(path) as pdf:
        return {i: (p.extract_text() or "") for i, p in enumerate(pdf.pages, 1)}


def read_tables(path: str) -> dict[int, list[tuple[float, list[list[str | None]]]]]:
    """Tables with their vertical position, so each can be tied to the heading above it."""
    with pdfplumber.open(path) as pdf:
        return {
            i: [(t.bbox[1], t.extract()) for t in p.find_tables()]
            for i, p in enumerate(pdf.pages, 1)
        }


def read_lines(path: str) -> dict[int, list[tuple[float, str]]]:
    with pdfplumber.open(path) as pdf:
        return {
            i: [(ln["top"], ln["text"].strip()) for ln in p.extract_text_lines()]
            for i, p in enumerate(pdf.pages, 1)
        }


# --------------------------------------------------------------------------- dates

_MONTHS = "Jan Feb Mar Apr May Jun Jul Aug Sep Oct Nov Dec".split()
_DATE = re.compile(r"(\d{1,2})\s+([A-Z][a-z]{2})\s+(\d{2})")

# Weekday glued to the day number is normal in this export: both "Monday05 Jul 27" and
# "Monday 05 Jul 27" appear, on pages 1 and 2 of the same document.
_DATE_HEADER = re.compile(
    r"^(?:Mon|Tues|Wednes|Thurs|Fri|Satur|Sun)day\s*(\d{1,2}\s+[A-Z][a-z]{2}\s+\d{2})\s*$"
)


def parse_date(text: str) -> date:
    m = _DATE.search(text)
    if not m:
        raise ValueError(f"no date in {text!r}")
    day, mon, yy = m.groups()
    return date(2000 + int(yy), _MONTHS.index(mon) + 1, int(day))


def parse_range(text: str) -> tuple[date, date]:
    """'01 Apr 27 - 09 Jul 27' -> two dates. Both dashes appear in the pack."""
    parts = _DATE.findall(normalise(text))
    if len(parts) < 2:
        raise ValueError(f"no date range in {text!r}")
    (d1, m1, y1), (d2, m2, y2) = parts[0], parts[1]
    return (
        date(2000 + int(y1), _MONTHS.index(m1) + 1, int(d1)),
        date(2000 + int(y2), _MONTHS.index(m2) + 1, int(d2)),
    )


# --------------------------------------------------------------------------- quotation

_SERVICE_LABELS = ("Meet & Greet", "Transfer", "Accommodation", "Activity", "Day Tour", "Flight")

_SERVICE_TYPE = {
    "Meet & Greet": "meet_greet",
    "Transfer": "transfer",
    "Accommodation": "accommodation",
    "Activity": "activity",
    "Day Tour": "day_tour",
    "Flight": "flight",
}

# The label is glued to its value in this export: "Activity:SLOW Walks & Hikes",
# "Transfer:Transfers & Tours". Splitting on the colon handles both forms.
_LABEL_LINE = re.compile(rf"^({'|'.join(re.escape(s) for s in _SERVICE_LABELS)}):\s*(.*)$")

_IN_OUT = re.compile(r"^In:\s*(.+?)\s+Out:\s*(.+?)\s*$")
_PICKUP = re.compile(r"^Pick up:\s*(.*)$")
_DROPOFF = re.compile(r"^Drop off:\s*(.*)$")
_DEPARTURE = re.compile(r"^Departure:\s*(.*)$")
_ARRIVAL = re.compile(r"^Arrival:\s*(.*)$")
_INCLUDED = re.compile(r"^Included:\s*(\d+)\s*x\s*(.+?)\s*$")
_ROOM_LINE = re.compile(r"^(?:(\d+)\s*x\s+)?(.+?)\s+on\s+an?\s+(.+?)\s+basis\s*$")

# Trailing words of an `Included:` line that name what the modifier is counted per.
_MODIFIER_DENOMINATORS = ("per group", "per pax", "compulsory pax", "per person")

# A bare line that describes how the service is charged rather than what it is.
_UNIT_HINT = re.compile(r"^(Per\s|Half Day\s*\(|Private Tour$|Flights$)", re.IGNORECASE)

# Anchors that mark a block as having real service content. Used to tell a region header
# apart from a trailing unit line - see _split_blocks.
_CONTENT_ANCHORS = (_IN_OUT, _PICKUP, _DROPOFF, _DEPARTURE, _ARRIVAL, _INCLUDED, _UNIT_HINT)


def _is_region_header(line: str, block_lines: list[str]) -> bool:
    """A geography heading sits between two date blocks and belongs to the next one.

    The trap: "Private Tour" is also a bare trailing line before a date header, but it is
    the unit of the Peninsula tour, not a region. What separates them is that the region
    always follows a block that already carries service content (Pick up:, In:/Out:,
    Included:, a Per... unit), and "Private Tour" follows a block that carries none.
    """
    if ":" in line or any(ch.isdigit() for ch in line):
        return False
    return any(pat.match(prev) for prev in block_lines for pat in _CONTENT_ANCHORS)


def _split_blocks(lines: list[tuple[int, str]]) -> list[tuple[str | None, date, list[tuple[int, str]]]]:
    """(region, date, lines) per service block, in document order."""
    blocks: list[tuple[str | None, date, list[tuple[int, str]]]] = []
    region: str | None = None
    pending_region: str | None = None
    current: list[tuple[int, str]] = []
    current_date: date | None = None

    for page, line in lines:
        header = _DATE_HEADER.match(line)
        if header:
            if current_date is not None:
                # The last line of the block just closed may be the next block's region.
                if current and _is_region_header(current[-1][1], [t for _, t in current[:-1]]):
                    pending_region = current.pop()[1]
                blocks.append((region, current_date, current))
            if pending_region:
                region, pending_region = pending_region, None
            current, current_date = [], parse_date(header.group(1))
            continue
        if current_date is None:
            # Between "TRAVEL ARRANGEMENTS" and the first date header sits the first region.
            if line and not line.isupper():
                region = line
            continue
        current.append((page, line))

    if current_date is not None:
        blocks.append((region, current_date, current))
    return blocks


def parse_quotation(path: str) -> tuple[dict, list[BookedLine], list[tuple[str, str]]]:
    """Returns (metadata, booked lines, [(line_id, room text)] needing an LLM split).

    A room line containing " & " encodes more than one billable unit. It is returned with
    its line_id so the caller can replace exactly that line with the split result and
    leave the rest of the skeleton untouched. Everything else here is anchored.
    """
    document = path.split("/")[-1].split("\\")[-1]
    pages = read_pages(path)
    flat = "\n".join(pages.values())

    meta = {
        "quotation_ref": re.search(r"REF:\s*(\S+)", flat).group(1),
        "travellers": int(re.search(r"(\d+)\s+TRAVELLERS", flat).group(1)),
    }
    travel = re.search(r"Travel Date:\s*(.+?)\s+to\s+(.+?)\s*$", flat, re.M)
    meta["travel_start"] = parse_date(travel.group(1))
    meta["travel_end"] = parse_date(travel.group(2))

    # Number the lines by page so every SourceRef can name one. The document body runs
    # from TRAVEL ARRANGEMENTS to INCLUDES; the rest is letterhead and terms.
    numbered: list[tuple[int, str]] = []
    started = False
    for page, text in pages.items():
        for raw in text.splitlines():
            line = raw.strip()
            if line == "TRAVEL ARRANGEMENTS":
                started = True
                continue
            if line == "INCLUDES":
                started = False
            if started and line:
                numbered.append((page, line))

    booked: list[BookedLine] = []
    to_split: list[tuple[str, str]] = []

    for index, (region, day, block) in enumerate(_split_blocks(numbered), 1):
        page = block[0][0] if block else 1
        text_lines = [t for _, t in block]
        joined = " | ".join(text_lines)

        service_type, label_value, label_text = "unknown", None, None
        for _, line in block:
            m = _LABEL_LINE.match(line)
            if m:
                service_type = _SERVICE_TYPE[m.group(1)]
                label_value = m.group(2).strip() or None
                label_text = line
                break

        date_end = None
        for _, line in block:
            m = _IN_OUT.match(line)
            if m:
                day = parse_date(m.group(1))
                date_end = parse_date(m.group(2))

        pickup = dropoff = None
        for _, line in block:
            if (m := _PICKUP.match(line)) or (m := _DEPARTURE.match(line)):
                pickup = m.group(1).strip() or None
            elif (m := _DROPOFF.match(line)) or (m := _ARRIVAL.match(line)):
                dropoff = m.group(1).strip() or None

        unit_hint = next((t for t in text_lines if _UNIT_HINT.match(t)), None)

        modifiers: list[Modifier] = []
        for mod_page, line in block:
            m = _INCLUDED.match(line)
            if not m:
                continue
            quantity, rest = int(m.group(1)), m.group(2)
            denominator = next((d for d in _MODIFIER_DENOMINATORS if rest.endswith(d)), None)
            label = rest[: -len(denominator)].strip() if denominator else rest
            modifiers.append(
                Modifier(
                    label=label,
                    quantity=quantity,
                    denominator_hint=denominator,
                    source=SourceRef(document=document, page=mod_page, quote=line),
                )
            )

        room_lines = [(p, t) for p, t in block if _ROOM_LINE.match(t)]

        # What the block says the service *is*, stripped of its label, its unit and its
        # attributes. For "Transfer:Transfers & Tours" this is the routing line; the
        # supplier name stays on `supplier_or_property` where a rate lookup can use it.
        body = [
            t
            for t in text_lines
            if not _UNIT_HINT.match(t)
            and not _LABEL_LINE.match(t)
            and not any(p.match(t) for p in (_PICKUP, _DROPOFF, _DEPARTURE, _ARRIVAL, _INCLUDED))
        ]

        def make(desc: str, mods: list[Modifier], **kw) -> BookedLine:
            return BookedLine(
                line_id=f"L{index:02d}" + kw.pop("suffix", ""),
                region=region,
                service_type=service_type,
                description=desc,
                pickup=pickup,
                dropoff=dropoff,
                date_start=day,
                date_end=date_end,
                unit_hint=unit_hint,
                modifiers=mods,
                source=SourceRef(document=document, page=page, quote=joined),
                **kw,
            )

        if service_type == "accommodation" and room_lines:
            for room_index, (_, room_text) in enumerate(room_lines, 1):
                m = _ROOM_LINE.match(room_text)
                quantity, room, basis = m.group(1), m.group(2).strip(), m.group(3).strip()
                if " & " in room:
                    # "1 x Beach Villa & 1 x Beach Villa Grande on a Fully Inclusive basis"
                    # is two billable units on one line. The only LLM call in task A.
                    to_split.append((f"L{index:02d}-{room_index}", room_text))
                booked.append(
                    make(
                        room_text,
                        # `Included:` lines sit at the level of the stay, not of a room:
                        # Marula has one "2 x Consv Levy" line above two chalets. Copying it
                        # onto both would charge the levy twice - invented money. It goes on
                        # the first line and the rule reads the party size from the header.
                        modifiers if room_index == 1 else [],
                        suffix=f"-{room_index}",
                        supplier_or_property=label_value,
                        room_type=room,
                        basis=basis,
                        quantity=int(quantity) if quantity else 1,
                    )
                )
            continue

        description = body[0] if body else (label_value or joined)
        booked.append(make(description, modifiers, supplier_or_property=label_value))

    return meta, booked, to_split


# --------------------------------------------------------------------------- rate pack

_SECTION = re.compile(r"^(\d)\.\s+(.+)$")

# The supplier is not in the table; it is in the heading, and the heading does not name it
# the same way twice ("LOWVELD - ROAD TRANSFERS (TRANSFERS & TOURS)" vs "MARULA RESERVE -
# MARULA RIVER LODGE"). Declaring the pairing beats a rule that guesses it, and the
# expected heading is stored alongside so a reworded pack fails loudly instead of
# attributing rates to the wrong supplier. Section 1 names its property per row.
_SECTION_SUPPLIER: dict[str, tuple[str, str | None]] = {
    "1": ("CAPE TOWN - ACCOMMODATION", None),
    "2": ("CAPE TOWN - TOURING & TRANSFERS", "Cape Town Touring & Transfers"),
    "3": ("MARULA RESERVE - MARULA RIVER LODGE", "Marula River Lodge"),
    "4": ("KUDU SANDS - KUDU RIDGE PRIVATE GAME RESERVE", "Kudu Ridge Private Game Reserve"),
    "5": ("LOWVELD - ROAD TRANSFERS (TRANSFERS & TOURS)", "Transfers & Tours"),
    "6": ("MOZAMBIQUE - BENGUERRA / BAZARUTO ARCHIPELAGO", "Ilha Azul Beach Lodge"),
    "7": ("MOZAMBIQUE - AIR & SEA TRANSFERS", "Mozambique Air & Sea Transfers"),
}


def split_glued_cell(cell: str) -> tuple[str, str]:
    """Undo the column collision in the pack's text layer.

    The rate column swallows the tail of the service name, sometimes appended and
    sometimes interleaved character by character:

        'de210.00'      -> ('de', '210.00')            name tail "...with gui|de"
        'e3s95.00'      -> ('es', '395.00')            name tail "...island lodg|es"
        'ou9n5t.a0i0n'  -> ('ountain', '95.00')        name tail "...Table M|ountain"

    Letters and digits each keep their own order, so separating the two character classes
    reconstructs both fields exactly. Returns (name tail, amount text).
    """
    letters = "".join(c for c in cell if c.isalpha() or c == " ")
    digits = "".join(c for c in cell if c.isdigit() or c in ".,")
    return letters.strip(), digits


def _money(text: str) -> Money:
    return Money(amount=Decimal(text.replace(",", "")), currency=CURRENCY)


def _validity(raw: str | None) -> Validity | None:
    if not raw:
        return None
    start, end = parse_range(raw)
    return Validity(
        start=start,
        end=end,
        raw=raw.replace("\n", " "),
        carried_forward="carried forward" in normalise(raw).lower(),
    )


def parse_rate_pack(path: str) -> list[RateRecord]:
    """One explicit branch per table shape.

    The branches are keyed on the header row the supplier wrote, not on position, and
    each one is a different commercial reading: seasonal bands, per-person-sharing,
    per-villa, carried-forward air. Collapsing them into one loop would hide exactly the
    differences that decide the money.
    """
    document = path.split("/")[-1].split("\\")[-1]
    lines, tables, pages = read_lines(path), read_tables(path), read_pages(path)
    rates: list[RateRecord] = []

    for page, page_tables in tables.items():
        # Sections 8 and 9 are prose with no table at all, so tables cannot be matched to
        # headings by counting. Each table takes the last heading that starts above it.
        headings = [
            (top, m.group(1), m.group(2))
            for top, line in lines[page]
            if (m := _SECTION.match(line))
        ]

        for table_top, table in page_tables:
            above = [h for h in headings if h[0] < table_top]
            if not above:
                raise RuntimeError(f"page {page}: a table sits above every section heading")
            _, number, heading = above[-1]

            expected, supplier = _SECTION_SUPPLIER[number]
            if normalise(heading).strip() != expected:
                raise RuntimeError(
                    f"section {number} reads {heading!r}, expected {expected!r}; "
                    "the pack was reworded and the supplier mapping must be revisited"
                )

            header = tuple((c or "").split("\n")[0].strip() for c in table[0])
            rows = [[(c or "").strip() for c in row] for row in table[1:]]

            def add(product: str, price: Money, unit_raw: str, cell: str, **kw) -> None:
                who = kw.pop("supplier", supplier)
                if who is None:
                    raise RuntimeError(f"section {number}: no supplier for {product!r}")
                # No RateRecord exists without a line of the document that literally
                # contains its amount. If the amount cannot be located, the parser has
                # misread the grid, and a price with invented provenance is worse than no
                # run at all - so this aborts rather than degrading.
                verified, quote = check_amount(
                    f"{price.amount:,.2f}", pages[page], cell=cell, near=product
                )
                if not verified:
                    raise RuntimeError(
                        f"section {number}: {price} for {product!r} is not in the text "
                        f"layer of page {page}; the table was misread"
                    )
                rates.append(
                    RateRecord(
                        rate_id=f"R{len(rates) + 1:02d}",
                        supplier=who,
                        product=product,
                        price=price,
                        unit=None,
                        unit_raw=unit_raw,
                        source=SourceRef(document=document, page=page, section=number, quote=quote),
                        **kw,
                    )
                )

            if header == ("Property", "Room type", "Basis", "Rate", "Unit", "Validity"):
                for prop, room, basis, rate, unit, validity in rows:
                    v = _validity(validity)
                    add(room, _money(rate), unit, rate, basis=basis, supplier=prop,
                        validity=v, confidence=Confidence.CONTRACTED)

            elif header[:3] == ("Service", "Rate", "Unit"):
                for row in rows:
                    name, rate, unit = row[0], row[1], row[2]
                    tail, amount = split_glued_cell(rate)
                    if tail:
                        name = name + tail
                    validity = _validity(row[3]) if header[3] == "Validity" else None
                    expired = validity is not None and validity.carried_forward
                    add(
                        name,
                        _money(amount),
                        unit,
                        rate,
                        validity=validity,
                        confidence=Confidence.EXPIRED if expired else Confidence.CONTRACTED,
                        confidence_reason=(
                            f"Carried-forward tariff; the pack states validity {validity.raw}. "
                            "Pack section 7: \"Reconfirm before quoting.\""
                            if expired
                            else None
                        ),
                    )

            elif header[0] == "Room type" and "Green Season" in header[1]:
                # Two price columns, two rate records, two season bands. The bands share
                # their boundary date - 09 Jul appears as the end of Green and the start
                # of Peak - which is trap 2 and is resolved in costing, not here.
                bands = []
                for cell in (table[0][1], table[0][2]):
                    start, end = parse_range(cell)
                    bands.append(
                        SeasonBand(
                            name=cell.split("\n")[0].strip(),
                            start=start,
                            end=end,
                            raw=cell.replace("\n", " "),
                        )
                    )
                for room, green, peak, unit in rows:
                    for band, amount in zip(bands, (green, peak)):
                        add(room, _money(amount), unit, amount, season=band,
                            confidence=Confidence.CONTRACTED)

            elif header == ("Room type", "Rate", "Unit", "Notes"):
                for room, rate, unit, notes in rows:
                    add(room, _money(rate), unit, rate, confidence=Confidence.CONTRACTED)

            elif header == ("Routing", "Rate", "Unit", "Validity"):
                for routing, rate, unit, validity in rows:
                    add(routing, _money(rate), unit, rate, validity=_validity(validity),
                        confidence=Confidence.CONTRACTED)

            elif header == ("Villa type", "Rate", "Unit", "Validity"):
                for villa, rate, unit, validity in rows:
                    add(villa, _money(rate), unit, rate, validity=_validity(validity),
                        confidence=Confidence.CONTRACTED)

            else:
                raise RuntimeError(f"page {page} section {number}: unrecognised table header {header}")

    return rates


# --------------------------------------------------------------------------- correspondence


# "  Family Suite (B&B)        USD 375.00 per room per night"
_EMAIL_RATE = re.compile(
    r"^\s+(?P<product>[A-Z][A-Za-z &-]+?)\s*\((?P<basis>[^)]+)\)\s+USD\s+"
    r"(?P<amount>\d[\d,]*\.\d{2})\s+(?P<unit>per .+?)\s*$"
)
_EMAIL_DATE = re.compile(r"^Date:\s*\w{3},\s*(\d{1,2} \w{3} \d{4})")
_EMAIL_SUBJECT = re.compile(r"^Subject:.*?-\s*(?P<supplier>.+?)\s*$")


def parse_correspondence(path: str) -> list[RateOverride]:
    """Supplier correspondence read as rates, and as a claim about the pack.

    The email is plain text, so there is no extraction risk worth a model here; what is
    worth care is what the email *asserts*. Two sentences do the work - "effective for all
    2027 arrivals" and "These supersede anything you have on file for 2027" - and both are
    carried onto every override as text rather than collapsed into a boolean. A reviewer
    asked to accept a 220.00 increase on someone else's say-so needs the say-so.

    The product name is split from its basis: the email writes "Family Suite (B&B)" and the
    pack writes "Family Suite" with B&B in its own column. Left glued, the names would not
    match, the override would never fire, and the run would quietly bill the pack's
    superseded rates - the silent loss that sits at the top of the failure hierarchy.
    """
    document = path.split("/")[-1].split("\\")[-1]
    text = open(path, encoding="utf-8").read()
    lines = text.splitlines()

    sent_at: date | None = None
    supplier: str | None = None
    for line in lines[:12]:
        if (stamp := _EMAIL_DATE.match(line)) and sent_at is None:
            sent_at = datetime.strptime(stamp.group(1), "%d %b %Y").date()
        if (subject := _EMAIL_SUBJECT.match(line)) and supplier is None:
            supplier = subject.group("supplier")
    if sent_at is None or supplier is None:
        raise RuntimeError(f"{document}: no Date: or no supplier in the Subject: line")

    scope = next((ln.strip() for ln in lines if "effective for all" in ln), "")
    claim = next((ln.strip() for ln in lines if "supersede" in ln), "")

    overrides: list[RateOverride] = []
    for line in lines:
        found = _EMAIL_RATE.match(line)
        if not found:
            continue
        amount = found.group("amount")
        verified, evidence = check_amount(amount, text, near=found.group("product"))
        if not verified:
            raise RuntimeError(f"{document}: {amount} is not a literal span of the email")
        overrides.append(
            RateOverride(
                override_id=f"O{len(overrides) + 1:02d}",
                supplier=supplier,
                product=found.group("product").strip(),
                price=Money(amount=Decimal(amount.replace(",", "")), currency=CURRENCY),
                unit=None,  # parsed by costing/units.py, which owns the vocabulary
                unit_raw=found.group("unit").strip(),
                sent_at=sent_at,
                effective_scope=scope,
                supersession_claim=claim,
                source=SourceRef(document=document, page=1, section=None, quote=evidence),
            )
        )

    if not overrides:
        raise RuntimeError(f"{document}: correspondence parsed but states no rates")
    return overrides
