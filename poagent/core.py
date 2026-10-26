"""Purchase-order email -> validated SAP-style PO draft.

Pipeline (async): injection guard -> extract (LLM or offline parser) -> Pydantic validation ->
repair loop (feed validation errors back to the extractor, max 2 rounds) -> master-data checks
-> status: READY / REVIEW / BLOCK. Everything runs offline; an LLM extractor can be passed in.
"""
from __future__ import annotations

import asyncio
import json
import re
from datetime import date, datetime
from difflib import SequenceMatcher
from enum import Enum
from typing import Awaitable, Callable, Optional

from pydantic import BaseModel, Field, ValidationError, field_validator

# Synthetic master data (fictional vendors / materials / plants)
VENDORS = {"V100": "Sunrise Polymers Pvt Ltd", "V101": "Apex Safety Supplies", "V102": "Bharat Steel Traders",
           "V103": "Greenleaf Packaging"}
MATERIALS = {"MAT-10234": "KG", "MAT-20311": "BOX", "MAT-30007": "EA", "MAT-40562": "EA"}
PLANTS = {"1100", "1200", "1300", "1400"}
TODAY = date(2026, 10, 26)  # fixed so results are reproducible


class Unit(str, Enum):
    KG = "KG"
    TO = "TO"   # tonne
    BOX = "BOX"
    EA = "EA"   # each / pieces


class PODraft(BaseModel):
    vendor_id: str = Field(pattern=r"^V\d{3}$")
    material: str = Field(pattern=r"^MAT-\d{5}$")
    quantity: float = Field(gt=0, le=100000)
    unit: Unit
    plant: str = Field(pattern=r"^\d{4}$")
    delivery_date: date

    @field_validator("delivery_date")
    @classmethod
    def not_in_past(cls, v: date) -> date:
        if v < TODAY:
            raise ValueError("delivery date is in the past")
        return v


class POResult(BaseModel):
    status: str                      # READY / REVIEW / BLOCK
    draft: Optional[PODraft] = None
    issues: list[str] = []
    repair_rounds: int = 0


# --------------------------------------------------------------------------- guard
INJECTION = re.compile(r"ignore (all |any )?(previous|prior) instructions|disregard .*rules|you are now|"
                       r"system prompt|approve (this|a) po", re.I)


def injection_suspected(text: str) -> bool:
    return bool(INJECTION.search(text))


# --------------------------------------------------------------------------- offline extractor
UNIT_WORDS = {"kg": "KG", "kgs": "KG", "kilogram": "KG", "kilograms": "KG", "ton": "TO", "tons": "TO", "tonne": "TO",
              "tonnes": "TO", "box": "BOX", "boxes": "BOX", "pcs": "EA", "pc": "EA", "pieces": "EA", "piece": "EA",
              "ea": "EA", "each": "EA", "units": "EA", "nos": "EA", "cartons": "EA"}
MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def _parse_date(text: str) -> Optional[str]:
    t = text.lower()
    m = re.search(r"\b(20\d\d)-(\d{1,2})-(\d{1,2})\b", t)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    m = re.search(r"\b(\d{1,2})[/-](\d{1,2})[/-](20\d\d)\b", t)          # DD/MM/YYYY (Indian convention)
    if m:
        return f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"
    m = re.search(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(?:of\s+)?([a-z]{3})[a-z]*\.?,?\s+(20\d\d)\b", t)
    if m and m.group(2) in MONTHS:
        return f"{m.group(3)}-{MONTHS[m.group(2)]:02d}-{int(m.group(1)):02d}"
    m = re.search(r"\b([a-z]{3})[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(20\d\d)\b", t)
    if m and m.group(1) in MONTHS:
        return f"{m.group(3)}-{MONTHS[m.group(1)]:02d}-{int(m.group(2)):02d}"
    return None


def match_vendor(text: str) -> tuple[Optional[str], float]:
    """Fuzzy-match the best vendor name mentioned anywhere in the email."""
    t = text.lower()
    best, score = None, 0.0
    for vid, name in VENDORS.items():
        core = re.sub(r"\b(pvt|ltd|private|limited|supplies|traders)\b", "", name.lower()).split()
        hits = sum(1 for w in core if re.search(rf"\b{re.escape(w)}", t))
        s = hits / len(core)
        if s > score:
            best, score = vid, s
    return best, score


def offline_extract(email: str, feedback: str = "") -> str:
    """Stand-in for an LLM call with a JSON schema. Returns JSON text, like an LLM would."""
    mat = re.search(r"MAT-\d{5}", email, re.I)
    plant = re.search(r"plant\s*(?:no\.?|code)?\s*(\d{4})|(\d{4})\s*plant", email, re.I)
    qty = re.search(r"(?:qty|quantity|x)?\s*([\d,]+(?:\.\d+)?)\s*(kgs?|kilograms?|tons?|tonnes?|box(?:es)?|pcs|pieces?|ea|each|units|nos|cartons)\b",
                    email, re.I) or re.search(r"\bx\s*([\d,]+)\s*(EA|KG|BOX)\b", email)
    vendor, _ = match_vendor(email)
    unit = UNIT_WORDS.get(qty.group(2).lower()) if qty else None
    out = {"vendor_id": vendor, "material": mat.group().upper() if mat else None,
           "quantity": float(qty.group(1).replace(",", "")) if qty else None,
           "unit": unit, "plant": next((g for g in plant.groups() if g), None) if plant else None,
           "delivery_date": _parse_date(email)}
    if "unit" in feedback and out["material"] in MATERIALS and out["unit"] is None:   # repair: use material's base unit
        out["unit"] = MATERIALS[out["material"]]
    return json.dumps(out)


Extractor = Callable[[str, str], Awaitable[str]]


async def _offline_async(email: str, feedback: str = "") -> str:
    await asyncio.sleep(0)            # where a real LLM HTTP call would await
    return offline_extract(email, feedback)


# --------------------------------------------------------------------------- pipeline
async def process(email: str, extractor: Extractor = _offline_async, max_repairs: int = 2) -> POResult:
    if injection_suspected(email):
        return POResult(status="BLOCK", issues=["possible prompt injection in email"])
    feedback, rounds = "", 0
    while True:
        raw = json.loads(await extractor(email, feedback))
        try:
            draft = PODraft(**raw)
            break
        except ValidationError as e:
            if rounds >= max_repairs:
                return POResult(status="REVIEW", issues=[f"{'.'.join(map(str, er['loc']))}: {er['msg']}" for er in e.errors()],
                                repair_rounds=rounds)
            feedback = "; ".join(f"{er['loc'][0]}: {er['msg']}" for er in e.errors())
            rounds += 1
    issues = []
    if draft.plant not in PLANTS:
        issues.append(f"plant {draft.plant} not in master data")
    if draft.material not in MATERIALS:
        issues.append(f"material {draft.material} not in master data")
    elif MATERIALS[draft.material] != draft.unit.value and draft.unit.value not in ("TO",):
        issues.append(f"unit {draft.unit.value} differs from base unit {MATERIALS[draft.material]}")
    return POResult(status="REVIEW" if issues else "READY", draft=draft, issues=issues, repair_rounds=rounds)


async def process_batch(emails: list[str]) -> list[POResult]:
    return await asyncio.gather(*(process(e) for e in emails))
