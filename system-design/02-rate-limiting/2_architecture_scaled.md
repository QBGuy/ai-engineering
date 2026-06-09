# Scaled Rate-Limiting Architecture

The local example has one gateway and one Redis instance. At production scale, rate limiting becomes a consistency, latency, and product-policy problem.

```mermaid
flowchart TB
    clients[Clients] --> edge[CDN / WAF / edge limits]
    edge --> router[Global traffic routing]

    router --> regionA[Region A gateways]
    router --> regionB[Region B gateways]

    regionA --> redisA[(Regional Redis)]
    regionB --> redisB[(Regional Redis)]

    regionA --> provider[Model provider / GPU fleet]
    regionB --> provider

    control[Policy service<br/>plans / quotas / overrides] -. publishes policy .-> regionA
    control -. publishes policy .-> regionB
    usage[Usage ledger<br/>billing / audit / reconciliation] <-. async usage events .-> regionA
    usage <-. async usage events .-> regionB
```

## Separate fast enforcement from durable accounting

The synchronous limiter must answer quickly, so Redis or an equivalent low-latency store holds short-lived enforcement state. A durable usage ledger separately records actual model usage for billing, audit, and reconciliation.

Do not treat expiring Redis bucket state as the billing source of truth.

## Regional versus global limits

One global Redis deployment gives a stronger global limit but adds cross-region latency and creates a large failure domain. Regional limiters are faster and more resilient, but a tenant may temporarily consume its allowance in every region.

Common approaches include:

- Allocate part of a global allowance to each region.
- Route each tenant to a home region.
- Enforce short-window limits regionally and reconcile longer quotas globally.
- Accept bounded overshoot in exchange for availability.

## Multiple protected resources

A production AI platform may enforce several policies:

| Resource | Example policy key | Useful unit |
| --- | --- | --- |
| Edge capacity | IP or network | Requests/second |
| Tenant fairness | Tenant and endpoint | Requests/minute |
| Model spend | Tenant and model | Input/output tokens/minute |
| Provider quota | Provider account and model | Provider RPM/TPM |
| GPU capacity | Model pool | Concurrent requests or weighted work |
| Async processing | Tenant and job type | Queue submissions/minute |
| Commercial allowance | Organization and billing period | Dollars or tokens/month |

## Failure behavior

The limiter's dependency can fail too. Choose a policy per route:

- **Fail closed:** reject requests when the limiter is unavailable. Protects spend and capacity but reduces availability.
- **Fail open:** allow requests when the limiter is unavailable. Preserves availability but risks overload and cost.
- **Local emergency limit:** temporarily apply a conservative in-process limit while shared state is unavailable.

High-cost generation routes usually lean toward fail closed or a conservative emergency limit. Low-cost health and metadata routes should not depend on the limiter.
