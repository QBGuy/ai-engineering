import hashlib
from typing import Annotated

from fastapi import FastAPI, Header
from pydantic import BaseModel, Field


class InternalEmbeddingRequest(BaseModel):
    input: str = Field(min_length=1, max_length=2_000)


app = FastAPI(title="Mock Embeddings Service", version="0.1.0")


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "embeddings"}


@app.post("/internal/embed")
async def embed(
    body: InternalEmbeddingRequest,
    x_request_id: Annotated[str | None, Header(alias="X-Request-ID")] = None,
) -> dict:
    digest = hashlib.sha256(body.input.encode("utf-8")).digest()
    vector = [round(byte / 255, 4) for byte in digest[:8]]

    return {
        "request_id": x_request_id,
        "model": "toy-hash-embedding",
        "embedding": vector,
        "dimensions": len(vector),
    }
