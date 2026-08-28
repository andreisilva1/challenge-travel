"""Prompts, kept in one file so the model's whole surface area can be read at once.

There is one prompt. That is the finding, not an omission: on this pack the 7B model asked
to segment a quotation block returned five services where the document has one, and asked
to classify a clause against a closed list of fourteen keys it scored 2/6
(ARCHITECTURE.md 1.2). Both of those jobs moved into deterministic code, which is why
extract.py and anchors.py exist and why this file is short.

What is left is genuinely semantic and genuinely small: one room line that encodes two
units in prose the quotation's own grammar does not separate.

    "1 x Beach Villa & 1 x Beach Villa Grande on a Fully Inclusive basis"

Splitting that on "&" is wrong the moment a property sells a "Bed & Breakfast" or a
"Rise & Climb" - and this pack sells both. So the model is asked, under one constraint that
makes its answer checkable: every room type it returns must be a literal span of the line it
was given. A room type the model composed is a room type that will match no rate, and
verify.spans_present() rejects it before it can become a price.
"""

from __future__ import annotations

SPLIT_ROOM_LINE_SYSTEM = (
    "You separate a single accommodation line into the room units it contains.\n"
    "Copy room type names exactly as they appear in the line, character for character.\n"
    "Never expand an abbreviation, never fix a spelling, never add a word that is not there.\n"
    "The basis (for example 'on a Fully Inclusive basis') is not part of a room type.\n"
    "An ampersand inside a name, such as 'Bed & Breakfast', does not separate two units."
)

SPLIT_ROOM_LINE_USER = (
    "Accommodation line:\n{line}\n\n"
    "List each room unit with its quantity and its room type exactly as written."
)

# Ollama enforces this as a grammar, so the response is either this shape or an error. It
# does not make the content true - only verify.spans_present() does that - but it removes
# the whole class of failures where the answer is prose about the answer.
SPLIT_ROOM_LINE_SCHEMA = {
    "type": "object",
    "properties": {
        "units": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "quantity": {"type": "integer"},
                    "room_type": {"type": "string"},
                },
                "required": ["quantity", "room_type"],
            },
        }
    },
    "required": ["units"],
}
