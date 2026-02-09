from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BACKEND_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    database_url: str = "postgresql+asyncpg://analyst:analyst@localhost:5432/analyst"
    redis_url: str = "redis://localhost:6379/0"

    edgar_user_agent: str = "AlphaAnalyst contact@example.com"

    anthropic_api_key: str = ""
    openai_api_key: str = ""
    gemini_api_key: str = ""
    sec_api_key: str = ""
    polygon_api_key: str = ""
    fmp_api_key: str = ""
    finnhub_api_key: str = ""
    marketaux_api_key: str = ""
    fred_api_key: str = ""
    voyage_api_key: str = ""

    max_cost_per_analysis: float = 5.00
    cors_origins: str = "http://localhost:3000"

    models_config_path: Path = Field(default=BACKEND_ROOT / "config" / "models.yaml")


settings = Settings()
