from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.storage import database_url, hosted


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    app_env: str = "production" if hosted() else "development"
    database_url: str = ""
    demo_mode: bool | None = None
    admin_password_hash: str = ""
    cookie_secure: bool = hosted()
    admin_access_mode: Literal["https", "ssh_tunnel"] = "https"
    bot_token: str = ""
    support_url: str = "https://t.me/your_support"
    apicc_api_key: str = ""
    apicc_url: str = "https://api.cc"
    apicc_usd_rate: str = ""
    cryptopay_token: str = ""
    cryptopay_url: str = "https://pay.crypt.bot/api"
    xrocket_token: str = ""
    xrocket_url: str = "https://pay.xrocket.tg"
    toncenter_api_key: str = ""
    toncenter_url: str = "https://toncenter.com/api/v3"
    ton_wallet: str = ""
    ton_testnet: bool = False
    payment_ttl_seconds: int = 1800
    poll_seconds: int = 15

    @model_validator(mode="after")
    def infer_demo(self):
        if self.demo_mode is None:
            self.demo_mode = not bool(self.apicc_api_key)
        return self

    @property
    def resolved_database_url(self):
        return database_url(self.database_url)

    def validate_runtime(self):
        if self.app_env == "production":
            if not self.cookie_secure and self.admin_access_mode != "ssh_tunnel":
                raise ValueError("Use secure cookies for HTTPS or explicit ADMIN_ACCESS_MODE=ssh_tunnel")
            if not self.admin_password_hash.startswith("scrypt:"):
                raise ValueError("Set ADMIN_PASSWORD_HASH using python -m app.cli password")
        if not self.demo_mode and self.apicc_api_key:
            if (self.cryptopay_token and "testnet" in self.cryptopay_url) or (
                self.ton_wallet and self.ton_testnet
            ):
                raise ValueError(
                    "Do not mix real api.cc purchases with testnet payments. Use separate environments."
                )
        if self.poll_seconds < 5 or not 300 <= self.payment_ttl_seconds <= 86400:
            raise ValueError("Invalid polling interval or invoice lifetime")


settings = Settings()
