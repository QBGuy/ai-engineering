local request_capacity = tonumber(ARGV[1])
local request_refill = tonumber(ARGV[2])
local request_cost = tonumber(ARGV[3])
local token_capacity = tonumber(ARGV[4])
local token_refill = tonumber(ARGV[5])
local token_cost = tonumber(ARGV[6])

local now_parts = redis.call("TIME")
local now = tonumber(now_parts[1]) + tonumber(now_parts[2]) / 1000000

local function load_bucket(key, capacity, refill)
    local values = redis.call("HMGET", key, "tokens", "updated_at")
    local tokens = tonumber(values[1]) or capacity
    local updated_at = tonumber(values[2]) or now
    return math.min(capacity, tokens + math.max(0, now - updated_at) * refill)
end

local request_tokens = load_bucket(KEYS[1], request_capacity, request_refill)
local token_tokens = load_bucket(KEYS[2], token_capacity, token_refill)

local request_wait = math.max(0, request_cost - request_tokens) / request_refill
local token_wait = math.max(0, token_cost - token_tokens) / token_refill
local allowed = request_wait == 0 and token_wait == 0

if allowed then
    request_tokens = request_tokens - request_cost
    token_tokens = token_tokens - token_cost

    local request_ttl = math.ceil((request_capacity / request_refill) * 2)
    local token_ttl = math.ceil((token_capacity / token_refill) * 2)

    redis.call("HSET", KEYS[1], "tokens", request_tokens, "updated_at", now)
    redis.call("EXPIRE", KEYS[1], request_ttl)
    redis.call("HSET", KEYS[2], "tokens", token_tokens, "updated_at", now)
    redis.call("EXPIRE", KEYS[2], token_ttl)
end

local retry_after = math.ceil(math.max(request_wait, token_wait))
local limit_type = "none"
if request_wait > 0 and request_wait >= token_wait then
    limit_type = "requests"
elseif token_wait > 0 then
    limit_type = "tokens"
end

return {
    allowed and 1 or 0,
    math.floor(request_tokens),
    math.floor(token_tokens),
    retry_after,
    limit_type
}
