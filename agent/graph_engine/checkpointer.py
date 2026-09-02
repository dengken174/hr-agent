import logging
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver
from config import config

logger = logging.getLogger(__name__)


def get_checkpointer(backend: str | None = None) -> BaseCheckpointSaver:
    from agent.graph_engine import checkpointer_backend
    backend = backend or checkpointer_backend()
    if backend == "redis":
        try:
            from langgraph.checkpoint.redis import RedisSaver  # type: ignore
            cp = RedisSaver.from_conn_string(config.redis.url)
            logger.info("graph checkpointer: redis (%s)", config.redis.url)
            return cp
        except Exception as e:
            logger.warning("redis checkpointer unavailable (%s); fallback MemorySaver", e)
    return MemorySaver()
