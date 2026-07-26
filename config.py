from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    nvidia_nim_api_key: str = ""
    nvidia_nim_base_url: str = "https://integrate.api.nvidia.com/v1"
    nvidia_nim_default_model: str = "meta/llama-3.1-8b-instruct"

    local_base_url: str = "http://localhost:11434/v1"
    local_api_key: str = ""
    local_default_model: str = "llama3.1:8b"

    prefer_local: bool = True

    luna_session_dir: str = "~/.luna/sessions"
    luna_max_history: int = 100

    emma_api_url: str = "http://localhost:8000"
    emma_api_key: str = ""
