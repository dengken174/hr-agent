import logging
import aiomysql
from config import config

logger = logging.getLogger(__name__)
_pool: aiomysql.Pool | None = None


async def get_pool() -> aiomysql.Pool:
    global _pool
    if _pool is None:
        _pool = await aiomysql.create_pool(
            host=config.mysql.host,
            port=config.mysql.port,
            user=config.mysql.user,
            password=config.mysql.password,
            db=config.mysql.database,
            minsize=config.mysql.pool_min,
            maxsize=config.mysql.pool_max,
            autocommit=True,
            charset='utf8mb4',
        )
        logger.info("MySQL pool created: %s:%s/%s", config.mysql.host, config.mysql.port, config.mysql.database)
    return _pool


async def close_pool():
    global _pool
    if _pool:
        _pool.close()
        await _pool.wait_closed()
        _pool = None
        logger.info("MySQL pool closed")
