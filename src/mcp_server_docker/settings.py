from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import Field


class ServerSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="mcp_server_",
        case_sensitive=False
    )

    allowed_containers: list[str] = Field(default_factory=lambda: ["*"])
