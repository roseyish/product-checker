from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration. Every value can be overridden via environment or .env."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    llm_provider: Literal["openai", "mock"] = "mock"
    llm_base_url: str = "https://api.openai.com/v1"
    llm_api_key: str = ""
    llm_model: str = "gpt-4o-mini"
    embed_model: str = "text-embedding-3-small"
    embed_dim: int = 1536

    vector_store: Literal["pgvector", "memory"] = "memory"
    database_url: str = "postgresql://threadcheck:threadcheck@localhost:5432/threadcheck"

    top_k: int = 8
    rerank_top_n: int = 4
    chunk_size: int = 600
    chunk_overlap: int = 80


@lru_cache
def get_settings() -> Settings:
    return Settings()
