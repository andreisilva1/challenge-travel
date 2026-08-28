# Costed quotation — MRA-2027-0641

Three supplier documents in, one costed quotation out: 12 nights, 5 travellers, South Africa
and Mozambique. The output is deliberately three totals and a list of open questions rather
than one number.

## Install

Python 3.13 (developed and tested on 3.13.1 — nothing here is version-exotic, but that is the
only interpreter it has run on).

```bash
python -m venv .venv
.venv\Scripts\activate        # PowerShell: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Check the install before anything else, because a missing `pdfplumber` fails at import and
tells you nothing useful:

```bash
python -c "import pdfplumber, pydantic, rich"
```

## Ollama

There is exactly one model call in the system: splitting `1 x Beach Villa & 1 x Beach Villa
Grande` into two units. Responses are cached in `.cache/`, which is gitignored, so a fresh
clone needs Ollama up for that one call:

```bash
ollama pull qwen2.5:7b
ollama serve
```

If Ollama is unreachable the run does **not** fail. The composite line stays as the document
wrote it and goes to `needs_review` saying so — same as passing `--no-llm`. What you lose is
one priced line: firm drops from USD 24,167.00 to USD 21,827.00 and the quotation has 21 lines
instead of 22. Exposure, unpriced count and the 25 questions are unchanged.

## Run

```bash
python main.py
```

Prints the quotation as a table and writes two files next to itself:

- `intermediate.json` — everything the documents say, each fact with document, page and the
  verbatim quote it came from. This is the whole input to costing.
- `output.json` — the costed lines, the three totals, and the 25 open questions.

**The exit code is 1, and that is correct.** A quotation with blocking questions has not been
costed, it has been examined. The next thing to read this may be a script, so it is told.

| flag | |
|---|---|
| `--no-llm` | skip the model call; see above for what changes |
| `--from-intermediate` | re-cost an existing `intermediate.json` without touching the PDFs |
| `--out DIR` | where to write the two JSON files (a directory, default: here) |

## The two programs

Ingestion reads the documents. Costing is a pure function of `intermediate.json` — no I/O, no
model, no clock, no randomness. The boundary is a file rather than a function call, so the
claim is testable: move the three source documents out of the folder and run

```bash
python main.py --from-intermediate
```

It produces the full quotation with no document present. Anyone who doubts a number can read
`intermediate.json` to see what the documents said, then re-run costing on that alone.

## Tests

```bash
python -m pytest -q     # 86 tests
```

`tests/test_invariants.py` is the one worth reading: each of the ten invariants is shown
catching a deliberately damaged copy of the real output — a dropped line, an invented line, an
uncontracted zero, the same money counted twice.

## Reading order

`NOTE.md` first (400 words, and the last section is what these two hours did not buy), then
`main.py`, then `rules.py`.
