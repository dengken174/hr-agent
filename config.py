import os
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class LLMConfig:
    api_key: str = os.getenv("DEEPSEEK_API_KEY", "")
    base_url: str = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    chat_model: str = "deepseek-chat"
    reasoner_model: str = "deepseek-reasoner"
    temperature: float = 0.1
    max_tokens: int = 2048


@dataclass
class RedisConfig:
    url: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    session_ttl: int = 1800           # 30min
    max_history_rounds: int = 10


@dataclass
class MySQLConfig:
    host: str = os.getenv("MYSQL_HOST", "127.0.0.1")
    port: int = int(os.getenv("MYSQL_PORT", "3306"))
    user: str = os.getenv("MYSQL_USER", "root")
    password: str = os.getenv("MYSQL_PASSWORD", "123456")
    database: str = os.getenv("MYSQL_DATABASE", "hragent")
    pool_min: int = 1
    pool_max: int = 5


@dataclass
class ElasticsearchConfig:
    url: str = os.getenv("ES_URL", "http://localhost:9200")
    index_name: str = "hr_knowledge"


@dataclass
class MilvusConfig:
    uri: str = os.getenv("MILVUS_URI", "http://localhost:19530")
    collection_name: str = "hr_documents"
    embedding_dim: int = 1024


@dataclass
class JinaConfig:
    api_key: str = os.getenv("JINA_API_KEY", "")
    reranker_model: str = "jina-reranker-v2-base-multilingual"


@dataclass
class RagConfig:
    chunk_size: int = 512
    chunk_overlap: int = 50
    retrieval_k: int = 20
    rerank_top_n: int = 5
    rrf_k: int = 60


@dataclass
class LangSmithConfig:
    """LangSmith 可观测性（tracing）。

    设 LANGCHAIN_TRACING_V2=true + LANGCHAIN_API_KEY 即自动全链路追踪。
    生产环境敏感数据建议切 Langfuse 自托管。
    """
    tracing_enabled: bool = os.getenv("LANGCHAIN_TRACING_V2", "false").lower() in ("true", "1")
    api_key: str = os.getenv("LANGCHAIN_API_KEY", "")
    project: str = os.getenv("LANGCHAIN_PROJECT", "hr-agent")
    endpoint: str = os.getenv("LANGCHAIN_ENDPOINT", "https://api.smith.langchain.com")


@dataclass
class FeishuConfig:
    app_id: str = os.getenv("FEISHU_APP_ID", "")
    app_secret: str = os.getenv("FEISHU_APP_SECRET", "")
    verification_token: str = os.getenv("FEISHU_VERIFICATION_TOKEN", "")
    encrypt_key: str = os.getenv("FEISHU_ENCRYPT_KEY", "")
    bot_name: str = os.getenv("FEISHU_BOT_NAME", "HR智能助手")
    webhook_path: str = "/feishu/webhook"
    # API
    base_url: str = "https://open.feishu.cn/open-apis"
    token_url: str = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
    token_cache_ttl: int = 7000        # token 有效期 7200s，提前刷新
    http_timeout: int = 30
    # 默认资源 ID (可通过 Tool 参数覆盖)
    primary_calendar_id: str = os.getenv("FEISHU_CALENDAR_ID", "primary")
    holiday_calendar_id: str = os.getenv("FEISHU_HOLIDAY_CALENDAR_ID", "")
    default_spreadsheet_token: str = os.getenv("FEISHU_SHEET_TOKEN", "")
    approval_code: str = os.getenv("FEISHU_APPROVAL_CODE", "")
    mail_sender_id: str = os.getenv("FEISHU_MAIL_SENDER_ID", "")
    # Channel SDK
    channel_event_types: tuple = (
        "im.message.receive_v1",
        "im.message.reaction.created",
        "approval_instance.approved",
    )


@dataclass
class Config:
    llm: LLMConfig = field(default_factory=LLMConfig)
    redis: RedisConfig = field(default_factory=RedisConfig)
    mysql: MySQLConfig = field(default_factory=MySQLConfig)
    es: ElasticsearchConfig = field(default_factory=ElasticsearchConfig)
    milvus: MilvusConfig = field(default_factory=MilvusConfig)
    jina: JinaConfig = field(default_factory=JinaConfig)
    rag: RagConfig = field(default_factory=RagConfig)
    feishu: FeishuConfig = field(default_factory=FeishuConfig)
    langsmith: LangSmithConfig = field(default_factory=LangSmithConfig)
    agent_max_iterations: int = 6
    memory_max_token_limit: int = 4000
    approval_timeout_hours: int = 48
    log_level: str = os.getenv("LOG_LEVEL", "INFO")


config = Config()
