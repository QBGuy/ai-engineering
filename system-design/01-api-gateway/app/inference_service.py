from typing import Annotated

from fastapi import FastAPI, Header
from pydantic import BaseModel, Field


class InternalChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4_000)
    model: str = Field(default="toy-model")


app = FastAPI(title="Mock Inference Service", version="0.1.0")


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "inference"}


@app.post("/internal/chat")
async def chat(
    body: InternalChatRequest,
    x_request_id: Annotated[str | None, Header(alias="X-Request-ID")] = None,
) -> dict:
    return {
        "request_id": x_request_id,
        "model": body.model,
        "message": "The gateway authenticated, validated, and routed your request before this service handled it.",
        "echo": body.message,
    }
