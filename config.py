from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    mesh_api_key: str
    mesh_base_url: str = "https://api.meshapi.ai/v1"

    planner_model: str = "openai/gpt-4o-mini"
    drafter_model: str = "openai/gpt-4o-mini"
    verifier_model: str = "openai/gpt-4o-mini"
    cheap_model: str = "openai/gpt-4o-mini"
    synthesizer_model: str = "openai/gpt-4o"

    max_loops: int = 2
    min_verified_claims: int = 2
    min_distinct_domains: int = 2
    db_path: str = "data/deepdive.db"
    max_sources_per_subq: int = 6
    max_chunks_per_source: int = 4
    chunk_chars: int = 2000          # about 500 tokens
    chunk_overlap: int = 200
    fetch_timeout: float = 15.0

    drafter_max_chunks: int = 12
    max_claims_per_subq: int = 6
    min_quote_coverage: float = 0.7
    min_sentence_coverage: float = 0.85
    max_concurrent_runs: int = 2
    max_runs_per_hour: int = 10
settings = Settings()