import hashlib
import json
import os
import time
import uuid
from contextlib import asynccontextmanager

import httpx
import redis.asyncio as redis
from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel, Field
from qdrant_client import AsyncQdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
QDRANT_URL = os.getenv("QDRANT_URL", "http://localhost:6333")
EMBEDDING_URL = os.getenv("EMBEDDING_URL", "http://localhost:8002/internal/embed")
CACHE_TTL = int(os.getenv("CACHE_TTL", "3600"))
SEMANTIC_THRESHOLD = float(os.getenv("SEMANTIC_THRESHOLD", "0.97"))
COLLECTION = "embedding_cache"
EMBEDDING_DIM = 8


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.redis = redis.from_url(REDIS_URL, decode_responses=True)
    app.state.qdrant = AsyncQdrantClient(url=QDRANT_URL)
    app.state.http = httpx.AsyncClient(timeout=httpx.Timeout(15))

    collections = await app.state.qdrant.get_collections()
    existing = {c.name for c in collections.collections}
    if COLLECTION not in existing:
        await app.state.qdrant.create_collection(
            collection_name=COLLECTION,
            vectors_config=VectorParams(size=EMBEDDING_DIM, distance=Distance.COSINE),
        )

    yield
    await app.state.http.aclose()
    await app.state.redis.aclose()
    await app.state.qdrant.close()


app = FastAPI(title="Embedding Cache Gateway", version="0.1.0", lifespan=lifespan)


class EmbedRequest(BaseModel):
    query: str = Field(min_length=1, max_length=2_000)


def _exact_key(query: str) -> str:
    digest = hashlib.sha256(query.strip().lower().encode()).hexdigest()
    return f"cache:embedding:exact:{digest}"


def _point_id(query: str) -> str:
    digest = hashlib.sha256(query.strip().lower().encode()).hexdigest()[:32]
    return str(uuid.UUID(hex=digest))


async def _check_exact(r: redis.Redis, query: str) -> list[float] | None:
    raw = await r.get(_exact_key(query))
    return json.loads(raw) if raw else None


async def _check_semantic(
    qdrant: AsyncQdrantClient, embedding: list[float]
) -> tuple[list[float], float, str] | None:
    results = await qdrant.search(
        collection_name=COLLECTION,
        query_vector=embedding,
        limit=1,
        score_threshold=SEMANTIC_THRESHOLD,
    )
    if not results:
        return None
    hit = results[0]
    return hit.payload["embedding"], hit.score, hit.payload["query"]


async def _store(
    r: redis.Redis, qdrant: AsyncQdrantClient, query: str, embedding: list[float]
) -> None:
    await r.setex(_exact_key(query), CACHE_TTL, json.dumps(embedding))
    await qdrant.upsert(
        collection_name=COLLECTION,
        points=[PointStruct(
            id=_point_id(query),
            vector=embedding,
            payload={"query": query, "embedding": embedding},
        )],
    )


async def _call_embedding_api(http: httpx.AsyncClient, query: str) -> list[float]:
    try:
        resp = await http.post(EMBEDDING_URL, json={"query": query})
        resp.raise_for_status()
        return resp.json()["embedding"]
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Embedding service unavailable") from exc


@app.get("/health")
async def health(request: Request) -> dict:
    try:
        await request.app.state.redis.ping()
    except redis.RedisError as exc:
        raise HTTPException(status_code=503, detail="Redis unavailable") from exc
    return {"status": "ok", "service": "embedding-cache-gateway"}


@app.post("/v1/embeddings")
async def get_embedding(body: EmbedRequest, request: Request) -> dict:
    r: redis.Redis = request.app.state.redis
    qdrant: AsyncQdrantClient = request.app.state.qdrant
    http: httpx.AsyncClient = request.app.state.http
    t0 = time.perf_counter()

    # 1. Exact cache — O(1) Redis lookup
    exact_hit = await _check_exact(r, body.query)
    if exact_hit is not None:
        await r.incr("stats:hits:exact")
        return {
            "cache": "hit",
            "cache_type": "exact",
            "embedding": exact_hit,
            "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
        }

    # 2. Get embedding vector
    embedding = await _call_embedding_api(http, body.query)

    # 3. Semantic cache — ANN search in Qdrant
    sem_result = await _check_semantic(qdrant, embedding)
    if sem_result is not None:
        cached_embedding, similarity, matched_query = sem_result
        await r.incr("stats:hits:semantic")
        # Promote to exact cache so next identical query is O(1)
        await r.setex(_exact_key(body.query), CACHE_TTL, json.dumps(cached_embedding))
        return {
            "cache": "hit",
            "cache_type": "semantic",
            "similarity": round(similarity, 4),
            "matched_query": matched_query,
            "embedding": cached_embedding,
            "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
        }

    # 4. Miss — store in both Redis and Qdrant
    await r.incr("stats:misses")
    await _store(r, qdrant, body.query, embedding)
    return {
        "cache": "miss",
        "cache_type": None,
        "embedding": embedding,
        "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
    }


@app.get("/cache/stats")
async def cache_stats(request: Request) -> dict:
    r: redis.Redis = request.app.state.redis
    qdrant: AsyncQdrantClient = request.app.state.qdrant
    exact_hits = int(await r.get("stats:hits:exact") or 0)
    semantic_hits = int(await r.get("stats:hits:semantic") or 0)
    misses = int(await r.get("stats:misses") or 0)
    info = await qdrant.get_collection(COLLECTION)
    semantic_entries = info.points_count
    total = exact_hits + semantic_hits + misses
    return {
        "exact_hits": exact_hits,
        "semantic_hits": semantic_hits,
        "misses": misses,
        "total_requests": total,
        "hit_rate": round((exact_hits + semantic_hits) / total, 3) if total else 0.0,
        "semantic_cache_entries": semantic_entries,
    }


@app.delete("/cache")
async def clear_cache(request: Request) -> dict:
    r: redis.Redis = request.app.state.redis
    qdrant: AsyncQdrantClient = request.app.state.qdrant
    await r.flushdb()
    await qdrant.delete_collection(COLLECTION)
    await qdrant.create_collection(
        collection_name=COLLECTION,
        vectors_config=VectorParams(size=EMBEDDING_DIM, distance=Distance.COSINE),
    )
    return {"status": "cleared"}
