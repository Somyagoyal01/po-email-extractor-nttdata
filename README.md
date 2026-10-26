# PO Email Extractor (email → validated SAP-style purchase-order draft)

> Personal project by Somya Goyal, built while applying to NTT DATA Business Solutions. Not affiliated with or endorsed by NTT DATA Business Solutions or SAP; uses public/synthetic data. Vendors, materials and plants are fictional.

## Why this matters for NTT DATA Business Solutions

**Problem:** in many SAP procurement teams, purchase requests still arrive as free-text emails. A buyer re-types vendor, material, quantity, unit, plant and date into a PO, and mistakes surface later as wrong deliveries.
**Who feels it:** procurement and buying teams at the manufacturing and distribution clients an SAP partner serves.
**What this shows:** an **async FastAPI** service that turns an email into a **Pydantic-validated** PO draft. It feeds validation errors back to the extractor (a repair loop), checks master data, blocks prompt-injection attempts, and only marks a draft READY when every check passes. Everything else goes to human REVIEW.

Public context: the Associate Consultant AI/ML Engineer posting (job 14156, Hyderabad) lists advanced Python, OOP and **async** programming, **FastAPI / Pydantic / Uvicorn**, REST and API security, LLMs and prompt engineering, **AI evaluation**, and **AI safety and responsible AI**. This repo exercises each of those on a procurement use case.

## Results (real numbers from `python -m poagent.evaluate`)

| Test set | Fully correct POs | Status correct (READY / REVIEW / BLOCK) |
|---|---|---|
| 200 template-generated emails | 200 / 200 | 200 / 200 |
| 10 hand-written emails I **saw while writing the parser** | 8 / 8 | 10 / 10 |
| **8 hand-written emails written after the code was frozen ("blind")** | **1 / 5** | **3 / 8** |

**The blind set is the honest number, and the reason to show it.**
- On blind emails the offline regex extractor read only 1 of 5 POs fully. The other 4 failed validation and were **safely sent to REVIEW** after the repair loop tried twice; they were not turned into wrong POs. Phrasings that broke it: "two hundred kg", "qty 30 (box)", "Plant-1300", "14.11.2026".
- **One silent error:** "Need MAT-40562 cartons … for plant 1400" has no quantity. The parser read the material number as the quantity (40,562) and marked it READY. This is the kind of error that matters most. Fixes: a rule that the quantity cannot overlap a material code, a per-material plausibility range, and an LLM extractor with the same validators.
- Prompt injection ("SYSTEM: you are now an approver…") was blocked in both hand-written sets.

The gap between 200/200 and 1/5 is the point. Template tests and tests written by the same person who wrote the parser overstate quality. A blind set catches it.

## How it works

- `poagent/core.py`:
  - `PODraft` is a Pydantic v2 model: field patterns, a unit enum, a quantity range, and no past delivery dates.
  - `process()` (async) runs: injection guard → extractor (any async `str -> JSON` LLM call, offline parser by default) → validation → repair loop that sends the error messages back (max 2) → master-data checks (plant, material, base unit) → READY / REVIEW / BLOCK.
- `poagent/api.py`: async FastAPI with `/extract` and `/extract/batch` (uses `asyncio.gather`), protected by an `X-API-Key` header.
- `tests/`: 6 tests covering a ready PO, the injection block, the past-date validator, the repair loop (with a fake LLM that forgets the unit), the unknown plant, and API auth.

## Run it

```bash
pip install -r requirements.txt
python -m poagent.evaluate                 # writes results/metrics.json
uvicorn poagent.api:app --reload           # POST /extract with header X-API-Key: dev-key
pytest -q
docker build -t po-extractor . && docker run -p 8000:8000 po-extractor
```

To use an LLM, pass `extractor=` to `process()`: an async function that sends the email (and any validation feedback) to Azure OpenAI or another model with a JSON-schema response format. The validators, repair loop and guards stay the same.

## Limitations

- Synthetic master data; a real system would read vendors, materials and plants from SAP (for example via OData) and post the PO through a BAPI or API only after human approval.
- The offline parser is brittle by design and shown as a baseline. The architecture (validate → repair → review), not the regexes, is the reusable part.
