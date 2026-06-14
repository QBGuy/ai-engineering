import asyncio
import hashlib
import math
import os

from fastapi import FastAPI
from pydantic import BaseModel, Field

LATENCY_MS = int(os.getenv("EMBEDDING_LATENCY_MS", "500"))
EMBEDDING_DIM = 8

# Topic dimensions: keywords that raise a specific dimension
_TOPICS: list[tuple[list[str], int]] = [
    (["password", "reset", "login", "auth", "credential", "signin"], 0),
    (["billing", "payment", "invoice", "charge", "subscription", "price"], 1),
    (["error", "bug", "crash", "fail", "broken", "exception"], 2),
    (["export", "download", "upload", "file", "data", "csv"], 3),
    (["api", "endpoint", "webhook", "integration", "sdk", "request"], 4),
    (["user", "profile", "account", "settings", "preferences"], 5),
    (["how", "help", "guide", "support", "what", "steps"], 6),
    (["slow", "performance", "speed", "latency", "optimize", "fast"], 7),
]


def _make_embedding(query: str) -> list[float]:
    query_lower = query.lower()
    dims = [0.0] * EMBEDDING_DIM

    for keywords, dim in _TOPICS:
        for kw in keywords:
            if kw in query_lower:
                dims[dim] += 1.0

    # Small deterministic noise so identical queries produce identical embeddings
    # and queries with the same topic cluster stay close but not identical
    h = int(hashlib.md5(query.encode()).hexdigest(), 16)
    for i in range(EMBEDDING_DIM):
        dims[i] += ((h >> (i * 4)) & 0xF) / 200.0

    magnitude = math.sqrt(sum(d * d for d in dims)) or 1.0
    return [d / magnitude for d in dims]


class EmbedRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2_000)


app = FastAPI(title="Mock Embedding Service", version="0.1.0")


@app.post("/internal/embed")
async def embed(body: EmbedRequest) -> dict:
    await asyncio.sleep(LATENCY_MS / 1_000)
    return {
        "object": "embedding",
        "embedding": _make_embedding(body.query),
        "model": "mock-embed-v1",
        "usage": {"prompt_tokens": max(1, len(body.query.split()))},
    }


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "embedding-service"}
