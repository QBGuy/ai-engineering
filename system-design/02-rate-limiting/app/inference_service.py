from fastapi import FastAPI
from pydantic import BaseModel, Field


class InternalChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4_000)
    max_tokens: int = Field(ge=1, le=100)
    model: str


app = FastAPI(title="Mock Rate-Limited Inference Service", version="0.1.0")


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "inference"}


@app.post("/internal/chat")
async def chat(body: InternalChatRequest) -> dict:
    return {
        "model": body.model,
        "message": "The request reached inference because both tenant buckets had capacity.",
        "echo": body.message,
        "max_tokens": body.max_tokens,
    }
