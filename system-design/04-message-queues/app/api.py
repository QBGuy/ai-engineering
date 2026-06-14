import os
import time
import uuid

import redis.asyncio as aioredis
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
STREAM = "jobs:pending"
JOB_TTL = int(os.getenv("JOB_TTL", "3600"))

app = FastAPI(title="Message Queue Demo — API")
_redis: aioredis.Redis | None = None


async def get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(REDIS_URL, decode_responses=True)
    return _redis


class JobRequest(BaseModel):
    document: str


@app.get("/health")
async def health():
    r = await get_redis()
    await r.ping()
    return {"status": "ok"}


@app.post("/jobs", status_code=202)
async def submit_job(body: JobRequest):
    job_id = str(uuid.uuid4())
    r = await get_redis()

    # Write initial status so polls before the worker starts return "pending"
    await r.hset(f"jobs:{job_id}", mapping={"status": "pending", "submitted_at": str(time.time())})
    await r.expire(f"jobs:{job_id}", JOB_TTL)

    # Enqueue — XADD appends to the stream; Redis generates the entry ID
    await r.xadd(STREAM, {"job_id": job_id, "document": body.document})

    return {"job_id": job_id, "status": "pending"}


@app.get("/jobs/{job_id}")
async def get_job(job_id: str):
    r = await get_redis()
    data = await r.hgetall(f"jobs:{job_id}")
    if not data:
        raise HTTPException(status_code=404, detail="job not found")

    if data["status"] == "done":
        return {"job_id": job_id, "status": "done", "result": data["result"],
                "processing_ms": data.get("processing_ms")}
    if data["status"] == "failed":
        return {"job_id": job_id, "status": "failed", "error": data.get("error")}

    # Still pending or processing
    return JSONResponse(status_code=202, content={"job_id": job_id, "status": data["status"]})


@app.get("/stats")
async def stats():
    r = await get_redis()
    stream_len = await r.xlen(STREAM)

    # Count pending (unacknowledged) messages across the consumer group
    try:
        groups = await r.xinfo_groups(STREAM)
        pending = groups[0]["pending"] if groups else 0
    except Exception:
        pending = 0

    return {
        "queue_depth": stream_len,
        "pending_unacked": pending,
    }
