"""Sonda 2: campos obrigatorios + tarefas estreitas.
Hipotese: o modelo falha em ESTRUTURA (aninhamento, segmentacao) mas acerta
SEMANTICA estreita. Se confirmar, a tarefa A vira deterministica e o LLM fica
so em B2 e C.
"""
import json, time, urllib.request

URL = "http://127.0.0.1:11434/api/chat"


def ask(system, user, schema, model="qwen2.5:7b"):
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "format": schema,
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


def run(name, system, user, schema, expected):
    print("=" * 72)
    print(name)
    print("-" * 72)
    try:
        data, dt = ask(system, user, schema)
        print(json.dumps(data, indent=2, ensure_ascii=False))
        print(f"  [{dt:.1f}s]  esperado: {expected}")
    except Exception as e:
        print("FALHOU:", type(e).__name__, e)
    print()


# ---------------------------------------------------------------- A' estreita
run(
    "A'  separar unidades numa linha de quarto (campos OBRIGATORIOS)",
    "Split a booked accommodation line into one entry per billable unit. "
    "Copy room_type verbatim from the text. Every field is required; use null "
    "only when the text truly does not contain it.",
    "1 x Beach Villa & 1 x Beach Villa Grande on a Fully Inclusive basis",
    {
        "type": "object",
        "properties": {
            "units": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "quantity": {"type": "integer"},
                        "room_type": {"type": "string"},
                        "basis": {"type": ["string", "null"]},
                    },
                    "required": ["quantity", "room_type", "basis"],
                },
            }
        },
        "required": ["units"],
    },
    "2 unidades: 'Beach Villa' e 'Beach Villa Grande', basis 'Fully Inclusive'",
)

# ---------------------------------------------------------------- B2 rule_key
RULE_KEYS = [
    "trailer_supplement", "per_group_extra", "conservation_levy",
    "complimentary_transfer", "triple_occupancy", "single_occupancy",
    "gate_fees", "capacity_constraint", "tax_included",
    "carried_forward_tariff", "air_not_carried", "no_rate_requote",
    "correspondence_precedence", "unclassified",
]
COND_SCHEMA = {
    "type": "object",
    "properties": {
        "rule_key": {"type": "string", "enum": RULE_KEYS},
        "amount": {"type": ["number", "null"]},
        "unit": {"type": ["string", "null"]},
        "applies_when_text": {"type": ["string", "null"]},
    },
    "required": ["rule_key", "amount", "unit", "applies_when_text"],
}
COND_SYS = (
    "Classify one clause from a supplier rate pack into exactly one rule_key. "
    "Copy amount verbatim if the clause states a price; null otherwise. "
    "Use 'unclassified' if unsure. Never invent an amount."
)

for label, clause, exp in [
    ("B2.1 trailer",
     "2a. Trailer supplement, required for groups of 6 pax and above or where luggage "
     "exceeds vehicle capacity: 65.00 per group, charged per transfer. Advise at time of booking.",
     "trailer_supplement / 65.0"),
    ("B2.2 levy",
     "Kudu Sands Conservation Levy: 35.00 per person per night, compulsory. Not commissionable.",
     "conservation_levy / 35.0"),
    ("B2.3 cortesia (sem valor)",
     "Complimentary scheduled road transfer from Hoedspruit Airport (HDS) is included for "
     "guests staying two nights or more. No charge.",
     "complimentary_transfer / null"),
    ("B2.4 carregada (armadilha: tem numero no contexto?)",
     "2027 tariffs for Mozambique air transfers had not been released at the date this pack "
     "was compiled. Operator has indicated a likely increase but has not confirmed. "
     "Reconfirm before quoting.",
     "carried_forward_tariff / null"),
    ("B2.5 precedencia",
     "Where a rate has been superseded by written supplier correspondence, the correspondence "
     "takes precedence over this pack.",
     "correspondence_precedence / null"),
    ("B2.6 triple",
     "4a. Triple occupancy is available in Luxury Suites on request. No supplement and no "
     "reduction applies - the third guest is charged at the standard per person sharing rate.",
     "triple_occupancy / null"),
]:
    run(label, COND_SYS, clause, COND_SCHEMA, exp)

# ---------------------------------------------------------------- C e-mail
EMAIL = open(
    r"c:\Users\USER\Downloads\Compressed\AI Lead challenge\Supplier-email-Camissa-2027-rates.txt",
    encoding="utf-8",
).read()

run(
    "C  e-mail -> RateOverride",
    "Extract revised supplier rates from correspondence. Copy every number and every "
    "product name VERBATIM. Never compute. If the email claims it supersedes previous "
    "rates, quote that sentence verbatim in supersession_claim.",
    EMAIL,
    {
        "type": "object",
        "properties": {
            "supplier": {"type": "string"},
            "sent_at": {"type": "string"},
            "effective_scope": {"type": "string"},
            "supersession_claim": {"type": ["string", "null"]},
            "items": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "product": {"type": "string"},
                        "basis": {"type": ["string", "null"]},
                        "amount": {"type": "number"},
                        "currency": {"type": "string"},
                        "unit": {"type": "string"},
                    },
                    "required": ["product", "basis", "amount", "currency", "unit"],
                },
            },
        },
        "required": ["supplier", "sent_at", "effective_scope", "supersession_claim", "items"],
    },
    "3 itens: 375 / 230 / 320, per room per night, USD",
)
