# src/sentinel/config/settings.py
"""Typed configuration, read once at startup.

§10.5: all configuration comes from the environment, into a single typed object,
and nothing else reads environment variables. That rule earned its place here —
the LLM adapters originally called `os.getenv("GROQ_API_KEY")` directly and
reported "not configured" on a machine where the key was plainly set, because it
lived in `.env` and only pydantic-settings was loading that file.

Behaviour that gets tuned — model per profile, retry budget, timeouts — belongs
in this file rather than in code.
"""

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
DEFAULT_DB_PATH = BASE_DIR / "sentinel_audit.db"


class Settings(BaseSettings):
    # --- Language models -----------------------------------------------------
    LLM_PROVIDER: str = "groq"
    # A rate-limited free tier is the normal operating condition, so somewhere to
    # fall back to is the default rather than an opt-in.
    LLM_FALLBACK_PROVIDER: str = "ollama"
    MODEL_NAME: str = "openai/gpt-oss-120b"
    LLM_MODEL: str = "openai/gpt-oss-120b"
    LLM_MODEL_FAST: str = "openai/gpt-oss-20b"
    LLM_MODEL_EXTRACTION: str = ""
    LLM_TEMPERATURE: float = 0.1
    LLM_MAX_ATTEMPTS: int = 3
    LLM_TIMEOUT_SECONDS: float = 60.0
    LLM_CACHE_TTL_SECONDS: int = 6 * 60 * 60

    GROQ_API_KEY: str = ""
    # Hugging Face serverless inference — the second provider (M-01). Declared
    # here because a variable present in .env but absent from this object is
    # read by nothing and fails silently.
    HF_TOKEN: str = ""
    HF_MODEL: str = "Qwen/Qwen2.5-72B-Instruct"
    HF_MODEL_FAST: str = "Qwen/Qwen2.5-72B-Instruct"

    OLLAMA_HOST: str = "http://127.0.0.1:11434"
    OLLAMA_MODEL: str = "llama3"

    # --- Data services -------------------------------------------------------
    DATABASE_URL: str = f"sqlite:///{DEFAULT_DB_PATH}"
    REDIS_URL: str = ""
    DB_POOL_SIZE: int = 10

    # --- External data -------------------------------------------------------
    TAVILY_API_KEY: str = ""
    OPENSANCTIONS_API_KEY: str = ""
    SEC_USER_AGENT: str = "Sentinel Compliance Research (contact: compliance@sentinel.local)"

    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def extraction_model(self) -> str:
        """Falls back to the reasoning model when no extraction model is named."""
        return self.LLM_MODEL_EXTRACTION or self.LLM_MODEL


settings = Settings()
