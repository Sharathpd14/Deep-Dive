from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    mesh_api_key: str
    mesh_base_url: str = "https://api.meshapi.ai/v1"

    planner_model: str = "openai/gpt-4o"
    drafter_model: str = "openai/gpt-4o"
    verifier_model: str = "openai/gpt-4o"
    cheap_model: str = "openai/gpt-4o-mini"

    max_loops: int = 2
    min_verified_claims: int = 2
    min_distinct_domains: int = 2
    db_path: str = "data/deepdive.db"

settings = Settings()