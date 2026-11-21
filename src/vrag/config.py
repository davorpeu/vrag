from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    es_url: str = "http://localhost:9200"
    es_index: str = "vrag-chunks"
    activity_index: str = "vrag-activity"

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

    # Object store for the "s3" source (docs-minio in docker-compose.yml): MinIO or any S3 API
    docs_s3_url: str = "http://127.0.0.1:9100"
    docs_s3_access_key: str = "vrag"
    docs_s3_secret_key: str = "vrag-minio-secret"
    docs_s3_bucket: str = "vrag-docs"

    # Retrieval knobs
    top_k: int = 6
    candidates: int = 30
    rrf_k: int = 60
    # Weighted RRF: a hit scores weight / (rrf_k + rank) per ranking. Equal weights = plain RRF.
    rrf_bm25_weight: float = 1.0
    rrf_knn_weight: float = 20.0

    # Reranking (rerank.py): a cross-encoder reorders the top fused hits. Measured with
    # scripts/eval_retrieval.py. Model files are cached in models_dir (downloaded on first use).
    rerank: bool = False
    rerank_model: str = "Xenova/ms-marco-MiniLM-L-6-v2"
    rerank_candidates: int = 20
    models_dir: str = "data/models"


@lru_cache
def get_settings() -> Settings:
    return Settings()
