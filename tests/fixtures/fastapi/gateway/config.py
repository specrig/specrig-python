import os

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GATEWAY_")

    crm_url: str
    crm_timeout: float = 3.0
    token: str = Field(alias="CRM_TOKEN")


settings = Settings()
DATABASE_URL = os.environ["DATABASE_URL"]
REDIS = os.getenv("REDIS_URL", "redis://localhost")
