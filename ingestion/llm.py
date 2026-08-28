"""The only place a model is called, and the only place its output is distrusted by default.

Three properties, in the order they matter:

  checked   - nothing the model returns becomes a fact until verify.py finds it literally in
              the source. The model proposes a split; the page decides whether it happened.
  repeatable- temperature 0, a fixed seed, a pinned model tag, and a disk cache keyed by the
              hash of (model, prompt, schema). A second run of the same document does not
              call the model at all, so the run is reproducible even for a reviewer who has
              no GPU.
  contained - one function, one prompt, one JSON schema. If the model is unreachable the
              run does not fail: the line goes to needs_review with the raw text, which is
              the honest outcome and, on this quotation, is what the reviewer needs anyway.

urllib is deliberate. An HTTP client is not a dependency worth taking for one POST to
localhost, and the request is more readable written out than configured.
"""

from __future__ import annotations

import hashlib
import json
import os
import urllib.error
import urllib.request

from ingestion.prompts import (
    SPLIT_ROOM_LINE_SCHEMA,
    SPLIT_ROOM_LINE_SYSTEM,
    SPLIT_ROOM_LINE_USER,
)
from ingestion.verify import spans_present

MODEL = "qwen2.5:7b"
ENDPOINT = "http://localhost:11434/api/chat"
CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".cache")
TIMEOUT_SECONDS = 120

# seed and temperature together are what make this reproducible; num_ctx is set explicitly
# because the default is smaller than some of the blocks this could be pointed at later, and
# a silently truncated prompt is a wrong answer that looks like a right one.
OPTIONS = {"temperature": 0, "seed": 7, "num_ctx": 8192}


class ModelUnavailable(RuntimeError):
    """Raised when the model cannot be reached. Callers degrade; they do not retry."""


def _cache_path(payload: dict) -> str:
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:32]
    return os.path.join(CACHE_DIR, f"{digest}.json")


def _chat(system: str, user: str, schema: dict) -> dict:
    """One request, cached on disk by the exact bytes that produced it."""
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "format": schema,
        "options": OPTIONS,
        "stream": False,
    }

    cached = _cache_path(payload)
    if os.path.exists(cached):
        with open(cached, encoding="utf-8") as handle:
            return json.load(handle)

    request = urllib.request.Request(
        ENDPOINT,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            body = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError) as failure:
        raise ModelUnavailable(f"{ENDPOINT} did not answer: {failure}") from failure

    answer = json.loads(body["message"]["content"])
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(cached, "w", encoding="utf-8") as handle:
        json.dump(answer, handle, ensure_ascii=False, indent=2)
    return answer


def split_room_line(raw: str) -> tuple[list[tuple[int, str]], str | None]:
    """([(quantity, room_type)], rejection). Exactly one of the two is empty.

    The rejection is a sentence, not a code, because it goes straight into the review item a
    human reads. Four things can go wrong and all four end the same way - the line is not
    split, and the reviewer is told what was asked and what came back:

      the model is not running          -> the line is reviewed by hand
      the model returned no units       -> the line is reviewed by hand
      a room type is not in the line    -> the model wrote it, so it is not evidence
      the quantities do not add up      -> not checked here; the room types are the risk

    Nothing here falls back to a guess. A room type invented by a model matches no rate, and
    a room type silently dropped is a night nobody pays for.
    """
    try:
        answer = _chat(
            SPLIT_ROOM_LINE_SYSTEM, SPLIT_ROOM_LINE_USER.format(line=raw), SPLIT_ROOM_LINE_SCHEMA
        )
    except ModelUnavailable as failure:
        return [], f"The room line could not be split automatically ({failure})."

    units = answer.get("units") or []
    if not units:
        return [], "The model returned no room units for this line."

    names = [str(unit.get("room_type", "")).strip() for unit in units]
    invented = spans_present(names, raw)
    if invented:
        return [], (
            "The model returned room type(s) that do not appear in the line: "
            + ", ".join(repr(name) for name in invented)
            + ". A room type that is not in the document cannot be priced against the pack."
        )

    return [(int(unit.get("quantity", 1)), name) for unit, name in zip(units, names)], None
