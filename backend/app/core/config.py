from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    supabase_url: str
    supabase_service_role_key: str
    fernet_key: str
    ms_client_id: str
    ms_client_secret: str
    ms_authority: str = "https://login.microsoftonline.com/common"
    cors_allow_origins: str = "http://localhost:3000"
    sync_secret: str

    # LLM provider. Kept behind config rather than constants because free-tier
    # quotas are not a contract - switching provider or model should be an
    # environment change, not a code change. Any OpenAI-compatible endpoint
    # works; Groq is the default because its response_format shape matches the
    # schema this app already sends.
    llm_api_key: str
    llm_base_url: str = "https://api.groq.com/openai/v1"
    llm_model: str = "openai/gpt-oss-20b"

    # Message bodies are truncated to this many characters before being sent.
    # On a free tier the binding limit is tokens per day, not requests per day,
    # so one unbounded newsletter can eat a disproportionate share of the daily
    # budget. Roughly 1500 chars is ~400 tokens.
    llm_max_body_chars: int = 1500

    # Items processed per extraction run (not messages per LLM call - see
    # llm_messages_per_call when batching lands).
    extraction_batch_limit: int = 50


settings = Settings()
