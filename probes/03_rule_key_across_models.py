"""Sonda 3: a classificacao rule_key em todos os modelos instalados."""
import json, time, urllib.request

URL = "http://127.0.0.1:11434/api/chat"
RULE_KEYS = [
    "trailer_supplement", "per_group_extra", "conservation_levy",
    "complimentary_transfer", "triple_occupancy", "single_occupancy",
    "gate_fees", "capacity_constraint", "tax_included",
    "carried_forward_tariff", "air_not_carried", "no_rate_requote",
    "correspondence_precedence", "unclassified",
]
SCHEMA = {
    "type": "object",
    "properties": {
        "rule_key": {"type": "string", "enum": RULE_KEYS},
        "amount": {"type": ["number", "null"]},
    },
    "required": ["rule_key", "amount"],
}
SYS = (
    "Classify one clause from a supplier rate pack into exactly one rule_key from the "
    "allowed list. Copy the amount verbatim if the clause states a price, else null. "
    "Use 'unclassified' only if no key fits. Never invent an amount."
)
CASES = [
    ("trailer", "2a. Trailer supplement, required for groups of 6 pax and above or where luggage exceeds vehicle capacity: 65.00 per group, charged per transfer.", "trailer_supplement", 65.0),
    ("levy", "Kudu Sands Conservation Levy: 35.00 per person per night, compulsory. Not commissionable.", "conservation_levy", 35.0),
    ("cortesia", "Complimentary scheduled road transfer from Hoedspruit Airport (HDS) is included for guests staying two nights or more. No charge.", "complimentary_transfer", None),
    ("carregada", "2027 tariffs for Mozambique air transfers had not been released at the date this pack was compiled. Operator has indicated a likely increase but has not confirmed. Reconfirm before quoting.", "carried_forward_tariff", None),
    ("precedencia", "Where a rate has been superseded by written supplier correspondence, the correspondence takes precedence over this pack.", "correspondence_precedence", None),
    ("triple", "4a. Triple occupancy is available in Luxury Suites on request. No supplement and no reduction applies - the third guest is charged at the standard per person sharing rate.", "triple_occupancy", None),
]


def ask(model, clause):
    body = {
        "model": model,
        "messages": [{"role": "system", "content": SYS}, {"role": "user", "content": clause}],
        "format": SCHEMA, "stream": False,
        "options": {"temperature": 0, "seed": 7, "num_ctx": 8192},
    }
    req = urllib.request.Request(URL, data=json.dumps(body).encode(),
                                headers={"Content-Type": "application/json"})
    t0 = time.time()
    with urllib.request.urlopen(req, timeout=600) as r:
        out = json.load(r)
    return json.loads(out["message"]["content"]), time.time() - t0


for model in ["qwen2.5:7b", "mistral:latest", "llama3.2:latest"]:
    print("=" * 72)
    print(model)
    print("=" * 72)
    ok = 0
    tot_t = 0.0
    for name, clause, exp_key, exp_amt in CASES:
        try:
            d, dt = ask(model, clause)
            tot_t += dt
            got_k, got_a = d.get("rule_key"), d.get("amount")
            hit = (got_k == exp_key) and (got_a == exp_amt)
            ok += hit
            mark = "OK  " if hit else ("SAFE" if got_k == "unclassified" else "BAD ")
            print(f"  {mark} {name:12s} -> {got_k:28s} amount={got_a}  (esperado {exp_key}/{exp_amt})")
        except Exception as e:
            print(f"  ERR  {name:12s} -> {type(e).__name__}: {e}")
    print(f"  ==> {ok}/{len(CASES)} corretos, {tot_t:.0f}s total\n")
