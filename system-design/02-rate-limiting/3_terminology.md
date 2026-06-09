# Rate-Limiting Terminology

## Policy dimensions

| Term | Meaning | AI example |
| --- | --- | --- |
| **Limit key** | Identity or resource whose usage is counted. | Tenant, API key, IP, model, or provider account. |
| **Limit unit** | Work consumed by one operation. | Requests, tokens, concurrent generations, or dollars. |
| **Capacity** | Maximum burst that a bucket can hold. | 100 token units. |
| **Refill rate** | Speed at which permission becomes available again. | 20 token units per second. |
| **Cost / weight** | Amount consumed by one request. | Prompt estimate plus `max_tokens`. |
| **Quota** | Longer-period allowance, often tied to a commercial plan. | One million tokens per month. |

## Algorithms

| Term | Meaning | Main trade-off |
| --- | --- | --- |
| **Fixed window** | Counts work in discrete periods such as each minute. | Simple, but allows bursts across window boundaries. |
| **Sliding log** | Stores timestamps for recent requests. | Precise, but uses more memory and work. |
| **Sliding window counter** | Approximates a sliding window using adjacent counters. | Better smoothing with moderate complexity. |
| **Token bucket** | Refills permission continuously up to a capacity; requests spend tokens. | Supports controlled bursts and weighted requests. |
| **Leaky bucket** | Drains queued work at a steady rate. | Smooths output but can introduce waiting. |

## Response and client behavior

| Term | Meaning |
| --- | --- |
| **`429 Too Many Requests`** | HTTP response indicating the caller exceeded a rate limit. |
| **`Retry-After`** | Response header suggesting how long the client should wait. |
| **Backoff** | Increasing delay between retries. |
| **Jitter** | Random variation added to retry delay to prevent synchronized retries. |
| **Remaining capacity** | Approximate work the caller can still submit immediately. |

## Distributed-system concerns

| Term | Meaning |
| --- | --- |
| **Atomicity** | The full limiter decision happens as one indivisible operation. |
| **Race condition** | Concurrent checks observe stale capacity and collectively allow too much work. |
| **Shared state** | Limiter state accessible by every gateway replica. |
| **Clock source** | Time used to calculate refill or windows. A shared server-side clock reduces disagreement. |
| **Overshoot** | Work accepted beyond the intended limit because of concurrency, replication, or delayed reconciliation. |
| **Fail open / fail closed** | Allow or reject traffic when the limiter itself is unavailable. |

## AI-specific distinctions

| Term | Meaning |
| --- | --- |
| **RPM** | Requests per minute. Useful for request overhead and provider limits. |
| **TPM** | Tokens per minute. Better reflects variable LLM request cost. |
| **Reservation** | Capacity charged before work begins based on estimated maximum cost. |
| **Reconciliation** | Adjusting reserved usage using the actual usage returned after inference. |
| **Denial of wallet** | Abuse or accidental traffic that creates excessive usage cost. |
