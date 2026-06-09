import os
from contextlib import asynccontextmanager
from typing import Annotated
from uuid import uuid4

import httpx
from fastapi import FastAPI, Header, HTTPException, Request, status
from pydantic import BaseModel, Field


class EmbeddingRequest(BaseModel):
    input: str = Field(min_length=1, max_length=2_000)


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4_000)
    model: str = Field(default="toy-model", pattern="^[a-zA-Z0-9._-]+$")


API_KEYS = {key.strip() for key in os.getenv("API_KEYS", "dev-key").split(",") if key.strip()}
EMBEDDINGS_URL = os.getenv("EMBEDDINGS_URL", "http://localhost:8001/internal/embed")
INFERENCE_URL = os.getenv("INFERENCE_URL", "http://localhost:8002/internal/chat")
REQUEST_TIMEOUT_SECONDS = float(os.getenv("REQUEST_TIMEOUT_SECONDS", "5"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    timeout = httpx.Timeout(REQUEST_TIMEOUT_SECONDS)
    app.state.http_client = httpx.AsyncClient(timeout=timeout)
    yield
    await app.state.http_client.aclose()


app = FastAPI(title="Barebones AI API Gateway", version="0.2.0", lifespan=lifespan)


def authenticate(api_key: str | None) -> str:
    if not api_key or api_key not in API_KEYS:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid API key",
        )
    return api_key


def request_id_from(request: Request) -> str:
    return request.headers.get("X-Request-ID", str(uuid4()))


async def post_json(request: Request, url: str, payload: dict, request_id: str) -> dict:
    headers = {"X-Request-ID": request_id}

    try:
        response = await request.app.state.http_client.post(url, json=payload, headers=headers)
        response.raise_for_status()
        return response.json()
    except httpx.TimeoutException as exc:
        raise HTTPException(status_code=504, detail="Downstream service timed out") from exc
    except httpx.HTTPStatusError as exc:
        raise HTTPException(
            status_code=502,
            detail={
                "message": "Downstream service returned an error",
                "downstream_status": exc.response.status_code,
            },
        ) from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Downstream service unavailable") from exc


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "api-gateway"}


@app.post("/v1/embeddings")
async def create_embedding(
    body: EmbeddingRequest,
    request: Request,
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> dict:
    authenticate(x_api_key)
    request_id = request_id_from(request)

    downstream = await post_json(
        request,
        EMBEDDINGS_URL,
        {"input": body.input},
        request_id=request_id,
    )

    return {
        "request_id": request_id,
        "object": "embedding",
        "data": downstream,
    }


@app.post("/v1/chat/completions")
async def create_chat_completion(
    body: ChatRequest,
    request: Request,
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> dict:
    authenticate(x_api_key)
    request_id = request_id_from(request)

    downstream = await post_json(
        request,
        INFERENCE_URL,
        {"message": body.message, "model": body.model},
        request_id=request_id,
    )

    return {
        "request_id": request_id,
        "object": "chat.completion",
        "data": downstream,
    }
