"""The run: three documents in, two JSON files and one table out.

The pipeline is two programs with opposite postures, and the boundary between them is a
file rather than a function call so that the difference is enforced rather than intended.

  ingest  - untrusted input, I/O, one model call, tolerant and suspicious. Writes
            intermediate.json: everything the documents say, with a citation for each fact.
  cost    - a pure function of that file. No I/O, no model, no clock, no randomness. Given
            the same intermediate.json it returns the same bytes.

Anyone who doubts a number can read intermediate.json to see what the documents said, then
re-run costing on it alone. That is the difference between a system that can be audited and
one that has to be trusted.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from rich.console import Console
from rich.table import Table

from costing.cost import cost_quotation
from domain import BookedLine, ExtractedFacts
from ingestion.anchors import extract_conditions
from ingestion.extract import (
    parse_correspondence,
    parse_quotation,
    parse_rate_pack,
    read_pages,
)
from ingestion.llm import split_room_line
from ingestion.verify import MONEY_TOKEN, unclaimed_numbers
import invariants

HERE = Path(__file__).parent
QUOTATION = HERE / "MRA-2027-0641-Halloran-operational-quotation.pdf"
RATE_PACK = HERE / "Supplier-Rate-Pack-SA-2027-v3.pdf"
CORRESPONDENCE = HERE / "Supplier-email-Camissa-2027-rates.txt"


# --------------------------------------------------------------------------- ingest


def _split_lines(
    lines: list[BookedLine], to_split: list[tuple[str, str]], use_model: bool
) -> tuple[list[BookedLine], list[str]]:
    """Expand the one room line the quotation's grammar does not separate.

    On failure the line is left exactly as the document wrote it. That is not a fallback -
    it is the input, unmodified - and it reaches costing as a room type no supplier lists,
    which is precisely the question a human should be asked.
    """
    warnings: list[str] = []
    if not to_split:
        return lines, warnings

    out: list[BookedLine] = []
    pending = dict(to_split)
    for line in lines:
        raw = pending.get(line.line_id)
        if raw is None:
            out.append(line)
            continue
        if not use_model:
            warnings.append(f"{line.line_id}: --no-llm, so {raw!r} was left unsplit.")
            out.append(line)
            continue

        units, rejection = split_room_line(raw)
        if rejection:
            warnings.append(f"{line.line_id}: {rejection} Left as written: {raw!r}")
            out.append(line)
            continue

        stem = line.line_id.split("-")[0]
        for index, (quantity, room_type) in enumerate(units, 1):
            out.append(
                line.model_copy(
                    update={
                        "line_id": f"{stem}-{index}",
                        "quantity": quantity,
                        "room_type": room_type,
                        # The description follows the room type. Left composite, every
                        # question and every row of the output about the villa nobody can
                        # price would still be headed with the name of the villa that was.
                        "description": f"{quantity} x {room_type}",
                    }
                )
            )
        warnings.append(
            f"{line.line_id}: split by model into "
            + ", ".join(f"{q} x {r}" for q, r in units)
            + f" — each name verified as a literal span of {raw!r}."
        )
    return out, warnings


def ingest(use_model: bool) -> ExtractedFacts:
    metadata, booked, to_split = parse_quotation(str(QUOTATION))
    rates = parse_rate_pack(str(RATE_PACK))
    overrides = parse_correspondence(str(CORRESPONDENCE))
    conditions = extract_conditions(str(RATE_PACK))
    booked, warnings = _split_lines(booked, to_split, use_model)

    # The sweep in the other direction: every money-shaped token in the pack, against
    # everything the system claims to hold. An unclaimed amount is not proof of an error,
    # but an error of the worst kind - a clause nobody parsed - always looks like this.
    claimed = {str(rate.price.amount) for rate in rates}
    claimed |= {str(override.price.amount) for override in overrides}
    claimed |= {str(c.amount.amount) for c in conditions if c.amount is not None}
    claimed |= {token.replace(",", "") for c in conditions for token in MONEY_TOKEN.findall(c.quote)}
    for page, token, line in unclaimed_numbers(read_pages(str(RATE_PACK)), claimed):
        warnings.append(f"rate pack page {page}: {token} is not claimed by any rate or clause — {line!r}")

    return ExtractedFacts(
        quotation_ref=metadata["quotation_ref"],
        travellers=metadata["travellers"],
        travel_start=metadata["travel_start"],
        travel_end=metadata["travel_end"],
        booked_lines=booked,
        rates=rates,
        overrides=overrides,
        conditions=conditions,
        system_warnings=warnings,
    )


# --------------------------------------------------------------------------- render


def render(result, console: Console) -> None:
    table = Table(title=f"{result.quotation_ref} — {result.travellers} travellers", expand=True)
    table.add_column("Line", no_wrap=True)
    table.add_column("Service")
    table.add_column("Calculation")
    table.add_column("Total", justify="right", no_wrap=True)
    table.add_column("Confidence", no_wrap=True)
    table.add_column("Review", no_wrap=True)

    shade = {
        "contracted": "green",
        "superseded_by_correspondence": "cyan",
        "assumed": "yellow",
        "expired": "magenta",
        "unresolved": "red",
    }
    for line in result.costed_lines:
        colour = shade[line.confidence.value]
        table.add_row(
            line.line_id,
            line.description[:44],
            line.calculation or "—",
            str(line.total) if line.total is not None else "[red]not priced[/red]",
            f"[{colour}]{line.confidence.value}[/{colour}]",
            ", ".join(line.review_refs) or "",
        )
    console.print(table)

    totals = result.totals
    console.print()
    console.print(f"  [green]firm[/green]      {totals.firm}  ({totals.firm_line_count} lines)")
    console.print(
        f"  [yellow]exposure[/yellow]  {totals.exposure}  "
        f"({totals.exposure_line_count} lines priced on an indicative rate, plus the review items below)"
    )
    console.print(f"  [red]unpriced[/red]  {totals.unpriced_line_count} lines — no defensible number:")
    for reason in totals.unpriced_reasons:
        console.print(f"      • {reason}")

    console.print()
    console.print(f"[bold]needs_review — {len(result.needs_review)} items[/bold]")
    for item in result.needs_review:
        mark = "[red]blocking[/red]" if item.severity == "blocking" else "[yellow]info[/yellow]"
        money = f"  [yellow](exposure {item.exposure})[/yellow]" if item.exposure else ""
        console.print(f"  {item.review_id} {mark} {item.line_id or '—'} · {item.field}{money}")
        console.print(f"      {item.question}")
        for reference in item.evidence[:2]:
            console.print(f"      [dim]{reference.document} p{reference.page}: {reference.quote[:110]}[/dim]")

    if result.system_warnings:
        console.print()
        console.print("[bold]system warnings[/bold]")
        for warning in result.system_warnings:
            console.print(f"  [dim]• {warning}[/dim]")


# --------------------------------------------------------------------------- entry point


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--no-llm",
        action="store_true",
        help="skip the one model call; the composite room line is left as the document wrote it",
    )
    parser.add_argument(
        "--from-intermediate",
        action="store_true",
        help="cost an existing intermediate.json without touching the PDFs",
    )
    parser.add_argument("--out", default=str(HERE), help="where to write the two JSON files")
    args = parser.parse_args()

    console = Console()
    out = Path(args.out)
    intermediate = out / "intermediate.json"

    if args.from_intermediate:
        facts = ExtractedFacts.model_validate_json(intermediate.read_text(encoding="utf-8"))
    else:
        facts = ingest(use_model=not args.no_llm)
        intermediate.write_text(facts.model_dump_json(indent=2), encoding="utf-8")

    result = cost_quotation(facts)
    invariants.check(facts, result)
    (out / "output.json").write_text(result.model_dump_json(indent=2), encoding="utf-8")

    render(result, console)
    console.print()
    console.print(f"[dim]wrote {intermediate} and {out / 'output.json'}[/dim]")

    # A quotation with blocking questions has not been costed, it has been examined. The
    # exit code says so, because the next thing to read this may be a script.
    return 1 if any(item.severity == "blocking" for item in result.needs_review) else 0


if __name__ == "__main__":
    sys.exit(main())
