import math
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

import httpx
import redis.asyncio as redis
from fastapi import FastAPI, Header, HTTPException, Request, Response, status
from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4_000)
    max_tokens: int = Field(default=20, ge=1, le=100)
    model: str = Field(default="toy-model", pattern="^[a-zA-Z0-9._-]+$")


API_KEY_TENANTS = dict(
    entry.split(":", maxsplit=1)
    for entry in os.getenv(
        "API_KEY_TENANTS",
        "dev-key:tenant-dev,team-key:tenant-team",
    ).split(",")
    if ":" in entry
)
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
INFERENCE_URL = os.getenv("INFERENCE_URL", "http://localhost:8002/internal/chat")
REQUEST_CAPACITY = int(os.getenv("REQUEST_CAPACITY", "5"))
REQUEST_REFILL_PER_SECOND = float(os.getenv("REQUEST_REFILL_PER_SECOND", "1"))
TOKEN_CAPACITY = int(os.getenv("TOKEN_CAPACITY", "100"))
TOKEN_REFILL_PER_SECOND = float(os.getenv("TOKEN_REFILL_PER_SECOND", "20"))
TOKEN_BUCKET_SCRIPT = Path(__file__).with_name("token_bucket.lua").read_text()


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.redis = redis.from_url(REDIS_URL, decode_responses=True)
    app.state.http_client = httpx.AsyncClient(timeout=httpx.Timeout(10))
    yield
    await app.state.http_client.aclose()
    await app.state.redis.aclose()


app = FastAPI(title="AI-Aware Rate Limiter", version="0.1.0", lifespan=lifespan)


def authenticate(api_key: str | None) -> str:
    if not api_key or api_key not in API_KEY_TENANTS:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid API key",
        )
    return API_KEY_TENANTS[api_key]


def estimate_token_cost(body: ChatRequest) -> int:
    prompt_tokens = max(1, math.ceil(len(body.message) / 4))
    return prompt_tokens + body.max_tokens


async def check_limits(request: Request, tenant_id: str, token_cost: int) -> list:
    keys = [
        f"rate_limit:tenant:{tenant_id}:requests",
        f"rate_limit:tenant:{tenant_id}:tokens",
    ]
    args = [
        REQUEST_CAPACITY,
        REQUEST_REFILL_PER_SECOND,
        1,
        TOKEN_CAPACITY,
        TOKEN_REFILL_PER_SECOND,
        token_cost,
    ]

    try:
        return await request.app.state.redis.eval(TOKEN_BUCKET_SCRIPT, len(keys), *keys, *args)
    except redis.RedisError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Rate limiter unavailable",
        ) from exc


def apply_rate_limit_headers(response: Response, result: list) -> None:
    response.headers["X-RateLimit-Request-Remaining"] = str(result[1])
    response.headers["X-RateLimit-Token-Remaining"] = str(result[2])


@app.get("/health")
async def health(request: Request) -> dict:
    try:
        await request.app.state.redis.ping()
    except redis.RedisError as exc:
        raise HTTPException(status_code=503, detail="Redis unavailable") from exc
    return {"status": "ok", "service": "rate-limiting-gateway"}


@app.post("/v1/chat/completions")
async def create_chat_completion(
    body: ChatRequest,
    request: Request,
    response: Response,
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> dict:
    tenant_id = authenticate(x_api_key)
    token_cost = estimate_token_cost(body)
    if token_cost > TOKEN_CAPACITY:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "message": "Estimated request cost exceeds the per-request token capacity",
                "estimated_token_cost": token_cost,
                "maximum_token_cost": TOKEN_CAPACITY,
            },
        )

    result = await check_limits(request, tenant_id, token_cost)
    allowed, request_remaining, token_remaining, retry_after, limit_type = result

    if not allowed:
        headers = {
            "Retry-After": str(retry_after),
            "X-RateLimit-Request-Remaining": str(request_remaining),
            "X-RateLimit-Token-Remaining": str(token_remaining),
            "X-RateLimit-Limit-Type": str(limit_type),
        }
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail={
                "message": "Rate limit exceeded",
                "limit_type": limit_type,
                "estimated_token_cost": token_cost,
            },
            headers=headers,
        )

    apply_rate_limit_headers(response, result)

    try:
        downstream = await request.app.state.http_client.post(
            INFERENCE_URL,
            json=body.model_dump(),
        )
        downstream.raise_for_status()
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Inference service unavailable") from exc

    return {
        "object": "chat.completion",
        "tenant": tenant_id,
        "estimated_token_cost": token_cost,
        "data": downstream.json(),
    }
