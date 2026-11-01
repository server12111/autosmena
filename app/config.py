import logging
import re
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.storage import database_url, hosted

# Request URLs can contain Telegram tokens or private SMS reception tokens.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


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
    # Telegram IDs with access to the in-bot admin panel, comma separated.
    admin_ids: str = "7145919720"
    referral_percent: int = 5
    # Public HTTPS address shown to resellers; the admin "Вебхуки" domain overrides it.
    public_url: str = "https://bot-1793540100-9906-srvnkproject.bothost.tech"
    # Where Telegram fetches section banners; empty = this app's /static/brand on public_url.
    banner_base_url: str = ""
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
        if hosted():
            self.demo_mode = False
        elif self.demo_mode is None:
            self.demo_mode = False
        return self

    @property
    def admin_id_set(self):
        return {int(value) for value in re.findall(r"\d+", self.admin_ids)}

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
