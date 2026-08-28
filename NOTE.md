# Note

## What I built

Two programs, opposite postures, separated by `intermediate.json`. **Ingestion** reads the PDFs
and the email — tolerant, suspicious, one model call. **Costing** is a pure function of that file:
no I/O, no LLM, no clock, no randomness. Same intermediate, same bytes, a year from now.

The failure hierarchy and that split were fixed before the first line of code; `probes/` set the
LLM boundary.

Every figure carries a `SourceRef` (document, page, verbatim quote), accepted only if the amount
appears **literally** on that page. A failing figure is rejected, never corrected. Confidence is
ordinal; a line is only as strong as its weakest fact.

The output has **no field called `total`**: firm **USD 24,167.00** (14 lines), exposure **USD
5,023.00**, and **6 unpriced lines**. Collapsing them is the confident wrong number the brief
calls worse than useless. `0.00` is never a fallback — the one zero is Marula's complimentary
transfer, contracted at exactly two nights, quoted and cited.

`rule_key` binds supplier prose to code: a closed enum assigned by literal anchors, each naming
one reviewed function.

## Where the LLM stays out

Everywhere a number is decided. On whole-block extraction qwen2.5:7b invented five services where
one exists; on clause classification it scored 2/6. Its most instructive failure returned
`complimentary_transfer` for the Kudu conservation levy **with the correct amount, 35.00**.
Plausible and wrong is the failure this product cannot have.

So the model gets one job: splitting `1 x Beach Villa & 1 x Beach Villa Grande` into two units —
no arithmetic, checked against the source, discarded if invented. Splitting on `&` breaks "Bed &
Breakfast" and "Rise & Climb"; this pack sells both.

## What the two hours did not buy

86 tests and 10 invariants run on every output: no line may vanish, a zero must cite its clause.
But verbatim proves a number is *on* the page, not that it is the *right row*. Fourteen line
totals are hand-derived in the tests; eleven of the twenty-six rates are never independently
checked — the first thing I would do next.

Second: 09 Jul falls in two bands; I price the lower and carry the difference as exposure — a
commercial choice, not a fact. Third: the thresholds were measured on this pack; no harness exists
for a second.

Stack: Python, pydantic, pdfplumber, rich, pytest, Ollama local. No RAG or embeddings — the corpus
is three documents; precedence and units were the problem, not retrieval.

---

# Supplement — from needs_review to actions

## 1. Mozambique Air & Sea Transfers — L15 / L17, field `carried_forward_tariff`

**Ask.** Reservations, at the address in the pack's section 7 header. One closed question: is the
2025 published helicopter transfer VNX–Benguerra, **USD 395.00 per person per way**, still the
rate for 14 and 17 Jul 27 — and if not, what is the 2027 rate and its validity? The mail quotes
the pack's own carry-forward clause, so the supplier corrects a number they can see.

**Reply.** The agent parses it into a *proposed* `RateOverride` and it enters the system the way
the Camissa email already does: as a document with provenance, not as a value. The amount must
verify verbatim against the message body; unverified, the item stays open rather than degrading
to ASSUMED.

**Writes.** `R25.price`, `unit`, a new `Validity`; confidence EXPIRED →
SUPERSEDED_BY_CORRESPONDENCE.

**Downstream.** L15 and L17 recost, **USD 3,950.00 moves from exposure to firm**, two review
items close, totals and proposal re-render. It stops there. An already-issued booking or invoice
is never silently rewritten — it raises its own task.

## 2. Ilha Azul Beach Lodge — L16-2, field `rate`

**Ask.** Whether a "Beach Villa Grande" exists under that name, and its 2027 fully-inclusive
nightly rate for 14–17 Jul 27.

**Reply.** Two answers, two different writes. A rate creates a new `RateRecord` for a product the
pack has no entry for, correspondence-sourced. A correction — "no Grande; you mean Infinity Beach
Villa" — is a change to the *booking*, not the rate, and goes back to the consultant. The agent
must never resolve it by matching; that is the 0.759 fuzzy match this system exists to refuse.

**Downstream.** L16-2 becomes priced, unpriced drops 6 → 5, firm rises, proposal re-renders.

## Risks

- A reply that revises other rates too: new correspondence, re-run precedence — never patch fields.
- Sender identity: an override applies only inside its own supplier scope.
- Silence is not a smaller exposure, it is the same exposure with a deadline.
- An answer in a scanned attachment cannot be verified verbatim; it stays a human's job.
- Propagation stops at the proposal. Booking and invoice are where a quiet update becomes fraud.
