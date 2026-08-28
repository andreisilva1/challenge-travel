"""Sonda: qwen2.5:7b consegue fazer a tarefa A (bloco da quotation -> BookedLine)?
Usa exatamente o mecanismo do desenho: format = JSON schema, temperature 0, seed fixo.
"""
import json, time, urllib.request

URL = "http://127.0.0.1:11434/api/chat"

SCHEMA = {
    "type": "object",
    "properties": {
        "lines": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "date_start": {"type": "string"},
                    "date_end": {"type": ["string", "null"]},
                    "service_type": {"type": "string", "enum": [
                        "accommodation", "transfer", "activity", "day_tour",
                        "meet_greet", "flight"]},
                    "supplier_or_property": {"type": ["string", "null"]},
                    "description_raw": {"type": "string"},
                    "room_type": {"type": ["string", "null"]},
                    "basis": {"type": ["string", "null"]},
                    "quantity": {"type": "integer"},
                    "unit_hint": {"type": ["string", "null"]},
                    "pickup": {"type": ["string", "null"]},
                    "dropoff": {"type": ["string", "null"]},
                    "modifiers": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "label": {"type": "string"},
                                "qty": {"type": "integer"},
                                "denominator": {"type": "string"},
                            },
                            "required": ["label", "qty", "denominator"],
                        },
                    },
                },
                "required": ["date_start", "service_type", "description_raw",
                             "quantity", "modifiers"],
            },
        }
    },
    "required": ["lines"],
}

SYSTEM = (
    "You extract booked travel services from an operational quotation. "
    "Copy values VERBATIM from the text. Never invent, never compute, never infer "
    "a number that is not written. Output one entry per distinct billable unit: "
    "a line reading '1 x A & 1 x B' is TWO entries. 'Included:' lines are modifiers "
    "of the entry above them, never entries of their own."
)

# Blocos reais e difíceis do PDF (transcritos da camada de texto)
BLOCKS = {
    "kudu (triple + levy 3 pax + 2 suites)": """Sunday 11 Jul 27
In: Sunday 11 Jul 27 Out: Wednesday 14 Jul 27
Accommodation: Kudu Ridge Private Game Reserve
2 x Luxury Suite on a Fully Inclusive (Selected Beverages) basis
Included: 3 x Conservation Levy compulsory pax
Included: 1 x Triple per group""",
    "ilha azul (duas villas numa linha)": """Wednesday 14 Jul 27
In: Wednesday 14 Jul 27 Out: Saturday 17 Jul 27
Accommodation: Ilha Azul Beach Lodge
1 x Beach Villa & 1 x Beach Villa Grande on a Fully Inclusive basis""",
    "transfer (rotulo colado + trailer)": """Monday05 Jul 27
Transfer:
Transfer CIA to Zone 1 (06H00-21H00) - with Guide
Per Vehicle One Way
Pick up: CPT
Drop off: The Camissa Boutique Hotel
Included: 1 x Trailer 6+ Pax per group""",
}


def ask(block, model="qwen2.5:7b"):
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM},
            {"role": "user", "content": block},
        ],
        "format": SCHEMA,
        "stream": False,
        "options": {"temperature": 0, "seed": 7, "num_ctx": 8192},
    }
    req = urllib.request.Request(
        URL, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}
    )
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=600) as r:
        out = json.load(r)
    return json.loads(out["message"]["content"]), time.time() - t0


for name, block in BLOCKS.items():
    print("=" * 70)
    print(name)
    print("=" * 70)
    try:
        data, dt = ask(block)
        print(json.dumps(data, indent=2, ensure_ascii=False))
        print(f"  -> {dt:.1f}s, {len(data.get('lines', []))} entradas")
    except Exception as e:
        print("FALHOU:", type(e).__name__, e)
    print()
