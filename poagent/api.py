"""Async FastAPI service.  uvicorn poagent.api:app --reload

Simple API-key auth (header X-API-Key, value from env PO_API_KEY, default 'dev-key' for local runs).
"""
from __future__ import annotations

import os

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from .core import POResult, process, process_batch

app = FastAPI(title="PO email extractor (demo, synthetic master data)")


def require_key(x_api_key: str = Header(default="")) -> None:
    if x_api_key != os.environ.get("PO_API_KEY", "dev-key"):
        raise HTTPException(status_code=401, detail="invalid API key")


class EmailIn(BaseModel):
    email: str = Field(min_length=10, max_length=5000)


class BatchIn(BaseModel):
    emails: list[str] = Field(min_length=1, max_length=50)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}


@app.post("/extract", response_model=POResult, dependencies=[Depends(require_key)])
async def extract(body: EmailIn) -> POResult:
    return await process(body.email)


@app.post("/extract/batch", response_model=list[POResult], dependencies=[Depends(require_key)])
async def extract_batch(body: BatchIn) -> list[POResult]:
    return await process_batch(body.emails)
