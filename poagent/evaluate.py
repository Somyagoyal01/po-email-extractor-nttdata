"""Field-level accuracy on generated emails vs 10 hand-written held-out emails.

    python -m poagent.evaluate
"""
from __future__ import annotations

import asyncio
import json
import random
from datetime import date, timedelta
from pathlib import Path

from .core import MATERIALS, VENDORS, process

ROOT = Path(__file__).resolve().parents[1]
FIELDS = ["vendor_id", "material", "quantity", "unit", "plant", "delivery_date"]
PLANT_OF = {"MAT-10234": "1100", "MAT-20311": "1200", "MAT-30007": "1300", "MAT-40562": "1400"}
UNIT_WORD = {"KG": "kg", "BOX": "boxes", "EA": "pcs"}


def generated(n=200, seed=5):
    rng = random.Random(seed)
    rows = []
    for _ in range(n):
        vid = rng.choice(list(VENDORS))
        mat = rng.choice(list(MATERIALS))
        q = rng.randint(1, 900)
        d = date(2026, 11, 1) + timedelta(days=rng.randint(0, 50))
        tpl = rng.choice(["Please raise a PO to {v} for {q} {u} of {m}, plant {p}, delivery {d}.",
                          "Order {m} x {q} {u} from {v} for plant {p} by {d}",
                          "Vendor {v}; material {m}; quantity {q} {u}; plant {p}; date {d}"])
        email = tpl.format(v=VENDORS[vid], q=q, u=UNIT_WORD[MATERIALS[mat]], m=mat, p=PLANT_OF[mat], d=d.isoformat())
        rows.append({"email": email, "expect": {"vendor_id": vid, "material": mat, "quantity": q,
                                                 "unit": MATERIALS[mat], "plant": PLANT_OF[mat], "delivery_date": d.isoformat()}})
    return rows


async def score(rows):
    res = await asyncio.gather(*(process(r["email"]) for r in rows))
    field_ok = {f: 0 for f in FIELDS}
    n_fields, all_ok, status_ok, details = 0, 0, 0, []
    for r, out in zip(rows, res):
        exp = r["expect"]
        if isinstance(exp, str):                              # BLOCK / REVIEW expectations
            status_ok += out.status == exp
            details.append({"email": r["email"][:60], "expected": exp, "got": out.status})
            continue
        status_ok += out.status == "READY"
        got = out.draft.model_dump(mode="json") if out.draft else {}
        ok_all = True
        for f in FIELDS:
            ok = str(got.get(f)) == str(exp[f]) or (f == "quantity" and got.get(f) == float(exp[f]))
            field_ok[f] += ok
            ok_all &= ok
        all_ok += ok_all
        if not ok_all or out.status != "READY":
            details.append({"email": r["email"][:60], "status": out.status, "issues": out.issues,
                            "wrong": [f for f in FIELDS if str(got.get(f)) != str(exp[f]) and not (f == "quantity" and got.get(f) == float(exp[f]))]})
    n_po = sum(1 for r in rows if not isinstance(r["expect"], str))
    return {"n": len(rows), "field_accuracy": {f: round(v / n_po, 3) for f, v in field_ok.items()},
            "all_fields_correct": f"{all_ok}/{n_po}", "status_correct": f"{status_ok}/{len(rows)}",
            "repair_rounds_used": sum(o.repair_rounds for o in res), "problems": details[:10]}


def main():
    held = [json.loads(l) for l in (ROOT / "data" / "heldout_emails.jsonl").read_text().splitlines() if l.strip()]
    blind = [json.loads(l) for l in (ROOT / "data" / "blind_emails.jsonl").read_text().splitlines() if l.strip()]
    out = {"generated": asyncio.run(score(generated())),
           "heldout_handwritten_seen_while_coding": asyncio.run(score(held)),
           "blind_handwritten_written_after_code": asyncio.run(score(blind))}
    (ROOT / "results").mkdir(exist_ok=True)
    (ROOT / "results" / "metrics.json").write_text(json.dumps(out, indent=2))
    return out


if __name__ == "__main__":
    print(json.dumps(main(), indent=1))
