"""Central configuration.

Every infrastructure value comes from the environment (or a .env file).
Nothing in the code base hard-codes a URL, a model name or a secret.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal, Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- general -----------------------------------------------------------------
    app_name: str = "University Academic-Service Assistant"
    log_level: str = "INFO"
    prompt_version: str = "v1.0"
    # Used to hash student identifiers in logs and audit records (HMAC key).
    audit_salt: str = "change-me-in-production"
    # Optional: when set, GET /audit and admin endpoints accept this token for full access.
    admin_token: Optional[str] = None

    # --- storage -------------------------------------------------------------------
    sqlite_path: str = str(PROJECT_ROOT / "data" / "university.db")
    documents_dir: str = str(PROJECT_ROOT / "data" / "documents")
    chroma_path: str = str(PROJECT_ROOT / "data" / "chroma")
    chroma_host: Optional[str] = None  # when set, use a Chroma server instead of the embedded client
    chroma_port: int = 8000
    chroma_collection_prefix: str = "university_chunks"

    # --- LLM -------------------------------------------------------------------------
    # "ollama" (default, local), "mock" (deterministic, no model needed), "cloud" (optional fallback)
    llm_backend: Literal["ollama", "mock", "cloud"] = "ollama"
    mock_llm: bool = False  # convenience switch; forces llm_backend="mock"
    llm_model: str = "llama3.1:8b"
    ollama_base_url: str = "http://localhost:11434"
    llm_timeout_seconds: float = 60.0
    llm_temperature: float = 0.0
    llm_max_retries: int = 1
    # Ollama "think" flag for reasoning models (e.g. qwen3). None leaves the model default; false skips the reasoning trace.
    llm_think: Optional[bool] = None
    # Optional cloud fallback (OpenAI-compatible). Disabled unless explicitly configured.
    cloud_llm_base_url: Optional[str] = None
    cloud_llm_api_key: Optional[str] = None
    cloud_llm_model: Optional[str] = None
    # Let the LLM polish wording of deterministic answers (validated; falls back to template).
    llm_polish: bool = True
    # Let the LLM break ties when the deterministic classifier/planner is unsure.
    llm_assist_planning: bool = True

    # --- embeddings & retrieval ----------------------------------------------------------
    embedding_backend: Literal["auto", "sentence-transformers", "hashing"] = "auto"
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    embedding_cache_enabled: bool = True
    top_k: int = 8
    rerank_top_k: int = 5
    candidate_pool: int = 40
    rrf_k: int = 60
    dense_weight: float = 1.0
    bm25_weight: float = 1.0
    reranker_backend: Literal["lexical", "cross-encoder", "none"] = "lexical"
    cross_encoder_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"
    chunking_strategy: Literal["structure", "fixed"] = "structure"
    chunk_max_chars: int = 900
    fixed_chunk_chars: int = 500
    expand_cross_references: bool = True
    retrieval_dense: bool = True  # switches used by the evaluation to compare retrieval configurations
    retrieval_bm25: bool = True

    # --- evidence sufficiency gate (calibrated by scripts/evaluate.py) ---------------------
    min_term_coverage: float = 0.55
    partial_coverage_below: float = 0.85
    min_fused_score: float = 0.0

    # --- ingestion -------------------------------------------------------------------------
    max_upload_mb: int = 25
    ocr_enabled: bool = True
    ocr_language: str = "eng"
    ocr_min_chars_per_page: int = 40  # below this, a PDF page is treated as scanned
    auto_extract_rules: bool = True

    # --- request handling -------------------------------------------------------------------
    max_question_chars: int = 2000
    as_of_date_override: Optional[str] = None  # YYYY-MM-DD, for reproducible demos only

    @property
    def effective_llm_backend(self) -> str:
        return "mock" if self.mock_llm else self.llm_backend


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    get_settings.cache_clear()
