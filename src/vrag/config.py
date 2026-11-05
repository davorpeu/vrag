from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    es_url: str = "http://localhost:9200"
    es_index: str = "vrag-chunks"

    ollama_base_url: str = "http://localhost:11434"
    llm_model: str = "qwen2.5:7b"
    embed_model: str = "nomic-embed-text"
    embed_dims: int = 768

    langfuse_host: str = "http://localhost:3000"
    langfuse_public_key: str = ""
    langfuse_secret_key: str = ""

    api_url: str = "http://localhost:8000"

    # Document store for the "db" source (docs-db in docker-compose.yml)
    docs_db_url: str = "postgresql://vrag:vrag@127.0.0.1:5433/docs"

    # Retrieval knobs
    top_k: int = 6
    candidates: int = 30
    rrf_k: int = 60


@lru_cache
def get_settings() -> Settings:
    return Settings()
