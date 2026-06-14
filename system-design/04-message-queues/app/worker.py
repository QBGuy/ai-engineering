"""
Redis Stream consumer.

Reads jobs from the stream, runs a mock LLM summariser, and writes
results to a Redis Hash. Acknowledges only after the result is written
so a crash before the write causes safe re-delivery.
"""

import os
import time
import logging

import redis

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
STREAM = "jobs:pending"
GROUP = "workers"
CONSUMER = "worker-1"
LLM_LATENCY = float(os.getenv("LLM_LATENCY_MS", "2000")) / 1000
JOB_TTL = int(os.getenv("JOB_TTL", "3600"))
BLOCK_MS = 2000
RECLAIM_MIN_IDLE_MS = 30_000


def connect() -> redis.Redis:
    return redis.from_url(REDIS_URL, decode_responses=True)


def ensure_group(r: redis.Redis) -> None:
    try:
        r.xgroup_create(STREAM, GROUP, id="0", mkstream=True)
        log.info("Created consumer group %s on stream %s", GROUP, STREAM)
    except redis.ResponseError as e:
        if "BUSYGROUP" not in str(e):
            raise


def mock_llm(document: str) -> str:
    """Simulates slow LLM inference."""
    time.sleep(LLM_LATENCY)
    words = document.split()
    summary = " ".join(words[:min(10, len(words))]) + ("..." if len(words) > 10 else "")
    return f"Summary: {summary}"


def process(r: redis.Redis, entry_id: str, fields: dict) -> None:
    job_id = fields.get("job_id")
    document = fields.get("document", "")

    if not job_id:
        log.warning("Message %s missing job_id — skipping", entry_id)
        r.xack(STREAM, GROUP, entry_id)
        return

    # Idempotency check — skip if already completed by a previous attempt
    existing = r.hget(f"jobs:{job_id}", "status")
    if existing == "done":
        log.info("Job %s already done — acking without reprocessing", job_id)
        r.xack(STREAM, GROUP, entry_id)
        return

    log.info("Processing job %s (%.0f ms simulated latency)", job_id, LLM_LATENCY * 1000)
    r.hset(f"jobs:{job_id}", "status", "processing")

    start = time.time()
    try:
        result = mock_llm(document)
        elapsed_ms = int((time.time() - start) * 1000)

        # Write result BEFORE acknowledging — ensures safe re-delivery on crash
        r.hset(f"jobs:{job_id}", mapping={
            "status": "done",
            "result": result,
            "processing_ms": str(elapsed_ms),
            "completed_at": str(time.time()),
        })
        r.expire(f"jobs:{job_id}", JOB_TTL)
        r.xack(STREAM, GROUP, entry_id)
        log.info("Job %s done in %d ms", job_id, elapsed_ms)

    except Exception as exc:
        log.error("Job %s failed: %s", job_id, exc)
        r.hset(f"jobs:{job_id}", mapping={"status": "failed", "error": str(exc)})
        r.xack(STREAM, GROUP, entry_id)


def reclaim_stale(r: redis.Redis) -> None:
    """Reclaim messages idle longer than RECLAIM_MIN_IDLE_MS (e.g. from crashed workers)."""
    try:
        entries = r.xautoclaim(STREAM, GROUP, CONSUMER, RECLAIM_MIN_IDLE_MS, count=10)
        messages = entries[1] if isinstance(entries, (list, tuple)) and len(entries) > 1 else []
        for entry_id, fields in messages:
            log.info("Reclaimed stale message %s", entry_id)
            process(r, entry_id, fields)
    except Exception as exc:
        log.debug("Reclaim skipped: %s", exc)


def run() -> None:
    r = connect()
    ensure_group(r)
    log.info("Worker started — consuming from stream %s, group %s", STREAM, GROUP)

    iteration = 0
    while True:
        # Periodically reclaim messages from crashed consumers
        if iteration % 10 == 0:
            reclaim_stale(r)
        iteration += 1

        messages = r.xreadgroup(GROUP, CONSUMER, {STREAM: ">"}, count=1, block=BLOCK_MS)
        if not messages:
            continue

        for _stream, entries in messages:
            for entry_id, fields in entries:
                process(r, entry_id, fields)


if __name__ == "__main__":
    run()
