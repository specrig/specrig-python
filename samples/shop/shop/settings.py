from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str
    billing_url: str
    billing_timeout: float = 5.0


settings = Settings()
