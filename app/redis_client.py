import redis.asyncio as redis
from redis.asyncio.connection import ConnectionPool
from typing import Optional
from app.config import settings


class RedisManager:
    _pool: Optional[ConnectionPool] = None
    _client: Optional[redis.Redis] = None

    @classmethod
    async def get_client(cls) -> redis.Redis:
        if cls._client is None:
            cls._pool = ConnectionPool.from_url(
                settings.REDIS_URL,
                max_connections=200,
                decode_responses=True
            )
            cls._client = redis.Redis.from_pool(cls._pool)
        return cls._client

    @classmethod
    async def close(cls):
        if cls._client:
            await cls._client.aclose()
        if cls._pool:
            await cls._pool.disconnect()


async def get_redis() -> redis.Redis:
    return await RedisManager.get_client()


async def set_with_expiry(key: str, value: str, seconds: int = 3600):
    r = await get_redis()
    await r.setex(key, seconds, value)


async def get_value(key: str) -> Optional[str]:
    r = await get_redis()
    return await r.get(key)


async def delete_key(key: str):
    r = await get_redis()
    await r.delete(key)


async def lpush_list(key: str, *values: str):
    r = await get_redis()
    await r.lpush(key, *values)


async def lrange_list(key: str, start: int = 0, end: int = -1):
    r = await get_redis()
    return await r.lrange(key, start, end)


async def publish_message(channel: str, message: str):
    r = await get_redis()
    await r.publish(channel, message)


async def hset_dict(key: str, mapping: dict):
    r = await get_redis()
    await r.hset(key, mapping=mapping)


async def hget_all(key: str) -> dict:
    r = await get_redis()
    return await r.hgetall(key)


async def increment_counter(key: str, amount: int = 1) -> int:
    r = await get_redis()
    return await r.incrby(key, amount)
