from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=Path(__file__).resolve().parent.parent / ".env", extra="ignore"
    )

    mongodb_uri: str = "mongodb://localhost:27017"
    mongodb_database: str = "zion"
    corte_mongodb_database: str = "corte"
    corte_instagram_access_token: SecretStr = SecretStr("")
    corte_instagram_user_id: str = ""
    corte_instagram_required: bool = False
    client_slug: str = "zion"
    site_url: str = "https://zionsoftwarehouse.com.br"
    wordpress_api_url: str = "https://cms.zionsoftwarehouse.com.br/wp-json/wp/v2"
    instagram_username: str = "zionsoftwarehouse"
    openai_api_key: SecretStr = SecretStr("")
    openai_model: str = "gpt-4.1-mini"
    openai_embedding_model: str = "text-embedding-3-small"
    api_key: SecretStr = SecretStr("")
    public_api_url: str | None = Field(default=None, pattern=r"^https?://[^/?#]+/?$")
    cors_allow_origins: list[str] = Field(
        default_factory=lambda: [
            "https://zionsoftwarehouse.com.br",
            "https://www.zionsoftwarehouse.com.br",
            "https://cortevisagismo.com.br",
            "https://www.cortevisagismo.com.br",
        ]
    )
    cors_max_age: int = Field(default=600, ge=0)
    worker_interval_seconds: int = Field(default=3600, ge=30)
    worker_enabled: bool = True
    worker_retry_seconds: int = Field(default=30, ge=1)
    crawl_max_pages: int = Field(default=100, ge=2, le=1000)
    crawl_max_depth: int = Field(default=3, ge=0, le=10)
    crawl_delay_seconds: float = Field(default=1, ge=0, le=60)
    max_blog_articles: int = Field(default=30, ge=1, le=200)
    default_post_image_url: str | None = Field(
        default="https://zionsoftwarehouse.com.br/og-image.webp", pattern=r"^https?://"
    )
    similarity_threshold: float = Field(default=0.86, ge=0, le=1)
    instagram_access_token: SecretStr = SecretStr("")
    instagram_user_id: str = ""
    instagram_api_version: str = Field(default="v25.0", pattern=r"^v\d+\.\d+$")
    instagram_required: bool = False
    instagram_max_pages: int = Field(default=20, ge=1, le=1000)
    request_timeout_seconds: float = Field(default=30, gt=0)


@lru_cache
def get_settings() -> Settings:
    return Settings()


def client_settings(settings: Settings, client: str) -> Settings:
    if client == "zion":
        return settings
    if client != "corte":
        raise ValueError("Cliente desconhecido")
    if settings.corte_mongodb_database == settings.mongodb_database:
        raise ValueError("Os bancos Zion e Côrte devem ser diferentes")
    return settings.model_copy(update={
        "client_slug": "corte",
        "mongodb_database": settings.corte_mongodb_database,
        "site_url": "https://www.cortevisagismo.com.br",
        "wordpress_api_url": "https://blog.cortevisagismo.com.br/wp-json/wp/v2",
        "instagram_username": "corte.visagismo",
        "instagram_required": settings.corte_instagram_required,
        "instagram_user_id": settings.corte_instagram_user_id,
        "instagram_access_token": settings.corte_instagram_access_token,
        "default_post_image_url": None,
    })
