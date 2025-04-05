import dotenv
from pydantic import FilePath, SecretStr, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ServerSettings(BaseSettings, cli_parse_args=True):
    model_config = SettingsConfigDict(
        env_prefix="mcp_server_", env_nested_delimiter="__"
    )

    docker_secrets_env_files: list[FilePath] = []

    @computed_field
    @property
    def docker_secrets(self) -> dict[str, SecretStr]:
        return {
            k: SecretStr(v)
            for file in self.docker_secrets_env_files
            for k, v in dotenv.dotenv_values(file).items()
            if v is not None
        }
