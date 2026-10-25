from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", case_sensitive=False)

    # Database
    database_url: str = "postgresql+psycopg://medication_doc_verifier:medication_doc_verifier@localhost:5432/medication_doc_verifier"

    # Auth
    jwt_secret: str = "dev-only-change-me"
    jwt_ttl_minutes: int = 720
    cookie_name: str = "dv_session"
    cookie_secure: bool = False

    # Uploads
    max_upload_mb: int = 15
    duplicate_window_seconds: int = 300

    # Azure OpenAI v1 API (chat and embeddings share one resource).
    # The endpoint is the resource host, for example https://<resource>.services.ai.azure.com
    azure_openai_endpoint: str | None = None
    azure_openai_api_key: str | None = None
    azure_openai_chat_deployment: str = "gpt-5.6-terra"
    azure_openai_embedding_deployment: str = "text-embedding-3-small"

    # LLM
    llm_factory: str | None = None  # "package.module:callable" returning an LLM, for tests and custom providers
    llm_reasoning_effort: str = ""  # optional: minimal | low | medium | high, sent only if set
    llm_max_tokens: int = 16000
    llm_timeout_seconds: float = 180.0
    llm_max_retries: int = 3

    # Embeddings
    embedding_provider: str = "azure"  # azure | hash (offline, tests only)
    embedding_dim: int = 1536  # text-embedding-3-small native size; changing it needs a migration

    # Retrieval and verification
    retrieval_scoped_k: int = 8
    retrieval_global_k: int = 3
    retrieval_mention_k: int = 4
    min_relevance: float = 0.35
    fuzzy_name_threshold: float = 95.0
    quote_match_threshold: float = 88.0
    verify_concurrency: int = 4

    # Chunking
    chunk_max_words: int = 220
    chunk_overlap_words: int = 30

    # Pipeline and worker
    job_lease_seconds: int = 120
    job_heartbeat_seconds: int = 30
    job_max_attempts: int = 3
    worker_poll_seconds: float = 2.0
    long_doc_chars: int = 90000

    # Seed
    seed_demo_user: bool = True
    demo_email: str = "demo@example.com"
    demo_password: str = "demo-password-1"
    reference_dir: str = "/data/references"

    log_level: str = "INFO"
    log_json: bool = True


@lru_cache
def get_settings() -> Settings:
    return Settings()