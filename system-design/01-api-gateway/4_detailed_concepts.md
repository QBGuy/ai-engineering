# Detailed Gateway Concepts

This document expands the implementation details behind the architecture. An **upstream pool**, a **connection pool**, a **health check**, and **failover** solve different problems.

## 1. Upstream Pools

An **upstream pool** is a named group of backend service instances that NGINX can send requests to.

```nginx
upstream fastapi_gateway {
    least_conn;
    server gateway-1:8080;
    server gateway-2:8080;
    server gateway-3:8080;
}
```

Here, `fastapi_gateway` is the pool, each `server` is a FastAPI replica, and `least_conn` selects the replica with the fewest active NGINX-to-FastAPI connections.

```nginx
location / {
    proxy_pass http://fastapi_gateway;
}
```

An upstream pool answers: **which equivalent backend instances may handle this request?**

## 2. Upstream Pools versus Connection Pools

An **upstream pool** lists possible destinations. A **connection pool** contains already-open connections that can be reused.

```text
Upstream pool:              Reusable connection pool:
  gateway-1                   connection to gateway-1
  gateway-2                   connection to gateway-2
  gateway-3                   connection to gateway-2
```

NGINX can keep reusable connections to its upstream FastAPI replicas:

```nginx
upstream fastapi_gateway {
    least_conn;
    server gateway-1:8080;
    server gateway-2:8080;
    server gateway-3:8080;
    keepalive 32;
}
```

FastAPI separately keeps reusable connections to application services through its shared HTTP client:

```python
app.state.http_client = httpx.AsyncClient(timeout=timeout)
```

```text
Client
  -- client connection --> NGINX
  -- pooled connection --> selected FastAPI replica
  -- pooled connection --> embeddings or inference service
```

Connection pooling reduces repeated DNS, TCP, and TLS setup work. The trade-off is capacity:

- Pools that are too small make requests wait.
- Pools that are too large consume resources and can overwhelm downstream services.
- Every replica owns its own pools, so total possible connections multiply as replicas are added.

## 3. Load-Balancing Selection

NGINX chooses one server from the upstream pool for each new request.

```text
gateway-1: 10 active connections
gateway-2:  3 active connections
gateway-3:  7 active connections

Next request -> gateway-2
```

`least_conn` is useful when requests have different durations, such as streamed LLM responses. It only knows about NGINX's active connections. It does not directly know container CPU, memory, queue depth, or downstream model workload.

## 4. Passive Health Checks

Open-source NGINX performs **passive health checks**. It learns that a server is unhealthy when normal client requests fail while using that server.

```nginx
upstream fastapi_gateway {
    least_conn;
    server gateway-1:8080 max_fails=3 fail_timeout=30s;
    server gateway-2:8080 max_fails=3 fail_timeout=30s;
    server gateway-3:8080 max_fails=3 fail_timeout=30s;
}
```

Conceptually:

```text
1. NGINX sends a real request to gateway-1.
2. Connecting to gateway-1 fails.
3. gateway-1 accumulates a failed attempt.
4. After enough failures, NGINX temporarily avoids gateway-1.
5. After fail_timeout, live requests probe whether gateway-1 recovered.
```

| Parameter | Meaning |
| --- | --- |
| `max_fails=3` | Unsuccessful attempts allowed during `fail_timeout`. |
| `fail_timeout=30s` | Failure-counting window and approximate time the server is avoided. |

Passive health checks react to failures discovered by user traffic. They do not periodically call FastAPI's `/health` endpoint.

## 5. Active Health Checks and NGINX Plus

An **active health check** periodically probes each server even when there is no user request:

```text
NGINX -> GET gateway-1:8080/health
NGINX -> GET gateway-2:8080/health
NGINX -> GET gateway-3:8080/health
```

**NGINX Plus is the paid, closed-source commercial version of NGINX.** It supports native active upstream health checks. Open-source NGINX supports passive health checks but does not include the same native active health-check feature.

Common active-check alternatives when using open-source NGINX include:

- A cloud load balancer or managed API gateway.
- Kubernetes readiness probes removing unhealthy pods from service discovery.
- An orchestrator restarting unhealthy containers.
- Third-party NGINX modules.

The health check in this repository's `docker-compose.yml` only controls startup ordering:

```yaml
nginx:
  depends_on:
    gateway:
      condition: service_healthy
```

Docker Compose waits for FastAPI to become healthy before starting NGINX. It does not continuously update NGINX's upstream selection after startup.

## 6. Failover and Request Retry

**Failover** means trying another server when the selected upstream server cannot successfully handle the request.

```nginx
location / {
    proxy_next_upstream error timeout http_502 http_503 http_504;
    proxy_next_upstream_tries 3;
    proxy_next_upstream_timeout 10s;

    proxy_pass http://fastapi_gateway;
}
```

```text
Request -> gateway-1
           connection fails
        -> gateway-2
           succeeds
        -> client receives response
```

Failover can only happen while NGINX can still replace the response. If FastAPI already started streaming response bytes to the client, NGINX cannot switch replicas and restart invisibly.

## 7. Retry Safety and Idempotency

Retrying a request is not always safe. A `POST` may create work, spend money, or modify data:

```text
POST model request -> gateway-1 starts expensive model call
                   -> connection fails before response
                   -> NGINX retries on gateway-2
                   -> expensive model call may run twice
```

NGINX therefore does not normally retry a non-idempotent request after it has already been sent upstream unless explicitly configured with `non_idempotent`.

An **idempotency key** can make selected retries safer:

```http
Idempotency-Key: request-abc-123
```

The application stores the key in shared state and ensures repeated requests with that key do not execute twice.

Practical guidance:

- Retry idempotent operations with strict attempt and time limits.
- Avoid retrying expensive or state-changing `POST` requests without idempotency protection.
- Never assume failover can recover a response after streaming starts.

## 8. How the Concepts Work Together

```text
1. Upstream pool defines the available FastAPI replicas.
2. Load-balancing method selects one replica.
3. Connection pool reuses an existing connection when possible.
4. Passive or active health checks identify unhealthy replicas.
5. Failover may select another replica when a request fails.
6. Retry rules determine whether repeating that request is safe.
```

The local project has one FastAPI replica, so NGINX has an upstream pool and reusable connections but nowhere else to fail over. Failover becomes meaningful after multiple gateway replicas are configured.

## 9. Forwarding Headers

FastAPI sees its direct caller as NGINX, so NGINX adds headers that preserve details from the original client request:

| Header | Preserves |
| --- | --- |
| `X-Forwarded-For` | Original client IP address and proxy chain. |
| `X-Forwarded-Host` | Public hostname requested by the client. |
| `X-Forwarded-Proto` | Original protocol, usually `http` or `https`. |

These support accurate logging, rate limiting, redirects, generated URLs, and secure-cookie behavior. Applications must only trust forwarding headers from known proxies because clients can send fake values.

## Sources

- [NGINX HTTP load balancing](https://nginx.org/en/docs/http/load_balancing.html)
- [NGINX proxy retry and failover directives](https://nginx.org/en/docs/http/ngx_http_proxy_module.html#proxy_next_upstream)
- [NGINX HTTP health checks](https://docs.nginx.com/nginx/admin-guide/load-balancer/http-health-check/)
