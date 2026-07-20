"""Typed settings: non-secrets from config.toml, secrets from env/.env.

ponytail: stdlib tomllib (3.11+) reads the TOML; pydantic just validates.
No custom TOML source class for pydantic-settings — a dict merge is one line.
"""
from __future__ import annotations

import base64
import os
import tomllib
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = Path(os.environ.get("REVIEWER_CONFIG", ROOT / "config.toml"))


class GerritCfg(BaseModel):
    url: str = "http://localhost:8080"
    ca_bundle: str = ""
    timeout_seconds: int = 30


class ServerCfg(BaseModel):
    host: str = "127.0.0.1"
    port: int = 7474


class ReviewCfg(BaseModel):
    max_workers: int = 4
    worker_timeout_seconds: int = 1800
    diff_cap_chars: int = 8000
    strategy: str = "hermes"
    incremental: bool = True


class PostCfg(BaseModel):
    auto_vote: bool = False
    vote_label: str = "Code-Review"


class CostCfg(BaseModel):
    input_per_1m_usd: float = 0.30
    output_per_1m_usd: float = 0.30
    chars_per_token: int = 4


class ModelCfg(BaseModel):
    base_url: str = "https://api.xiaomimimo.com/v1"
    name: str = "mimo-v2.5-pro"


class Secrets(BaseSettings):
    """Secrets from environment / .env only — never from TOML, never committed."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    gerrit_auth: str = ""
    gerrit_user: str = ""
    gerrit_http_password: str = ""
    api_token: str = "change-me"
    model_api_key: str = ""       # Strategy B direct-API worker
    webhook_secret: str = ""      # gates POST /hooks/gerrit

    def auth_header(self) -> str:
        """Full Authorization header value, built from env. Ported from FIXES.md #1.

        Prefers GERRIT_AUTH; else builds Basic from user + HTTP password.
        Returns "" when nothing is configured (caller decides how to fail).
        """
        if self.gerrit_auth:
            return self.gerrit_auth
        if self.gerrit_user and self.gerrit_http_password:
            return basic_auth_header(self.gerrit_user, self.gerrit_http_password)
        return ""


def basic_auth_header(username: str, http_password: str) -> str:
    """Build a Gerrit Basic header without persisting either credential."""
    token = base64.b64encode(f"{username}:{http_password}".encode()).decode("ascii")
    return f"Basic {token}"


class Settings(BaseModel):
    gerrit: GerritCfg = GerritCfg()
    server: ServerCfg = ServerCfg()
    review: ReviewCfg = ReviewCfg()
    post: PostCfg = PostCfg()
    cost: CostCfg = CostCfg()
    model: ModelCfg = ModelCfg()
    secrets: Secrets = Secrets()


def _load_toml() -> dict:
    if CONFIG_PATH.is_file():
        with open(CONFIG_PATH, "rb") as f:
            return tomllib.load(f)
    return {}


@lru_cache
def get_settings() -> Settings:
    data = _load_toml()
    return Settings(
        gerrit=GerritCfg(**data.get("gerrit", {})),
        server=ServerCfg(**data.get("server", {})),
        review=ReviewCfg(**data.get("review", {})),
        post=PostCfg(**data.get("post", {})),
        cost=CostCfg(**data.get("cost", {})),
        model=ModelCfg(**data.get("model", {})),
        secrets=Secrets(),
    )
