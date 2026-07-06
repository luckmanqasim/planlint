"""Environment-driven settings. Every deployment knob lives here."""

from __future__ import annotations

from pathlib import Path

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

_REPO_ROOT = Path(__file__).resolve().parents[2]

# Load the repo-root .env into the process environment so provider API keys
# (GOOGLE_API_KEY etc.) reach Pydantic AI in local dev, matching what
# docker-compose's env_file does. Existing env vars are never overridden.
load_dotenv(_REPO_ROOT / ".env")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "planlint123"

    # Any Pydantic AI model string works, e.g. "openai:gpt-5.2", "anthropic:claude-sonnet-5"
    planlint_vision_model: str = "google:gemini-3-flash-preview"
    planlint_text_model: str = "google:gemini-3-flash-preview"

    # auto = use Docling when installed, else the built-in simple text parser
    planlint_semantic_parser: str = "auto"  # auto | docling | simple

    planlint_data_dir: Path = _REPO_ROOT / "data"
    planlint_samples_dir: Path = _REPO_ROOT / "samples"

    embed_model: str = "BAAI/bge-small-en-v1.5"  # 384-dim; must match the vector index
    embed_dimensions: int = 384

    # Test seam: replaces all LLM agents with deterministic fakes (see tests/ and README dev notes)
    planlint_fake_llm: bool = False

    cors_origins: str = "http://localhost:3000"

    retrieval_top_k: int = 8
    extraction_confidence_floor: float = 0.5


settings = Settings()
