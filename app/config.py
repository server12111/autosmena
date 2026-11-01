from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    app_env: str = "development"
    database_url: str = "sqlite:///./smena.db"
    demo_mode: bool = True
    admin_password_hash: str = ""
    cookie_secure: bool = False
    admin_access_mode: Literal["https", "ssh_tunnel"] = "https"
    bot_token: str = ""
    support_url: str = "https://t.me/your_support"
    apicc_api_key: str = ""
    apicc_url: str = "https://api.cc"
    apicc_usd_rate: str = ""
    cryptopay_token: str = ""
    cryptopay_url: str = "https://testnet-pay.crypt.bot/api"
    xrocket_token: str = ""
    xrocket_url: str = "https://pay.xrocket.tg"
    toncenter_api_key: str = ""
    toncenter_url: str = "https://testnet.toncenter.com/api/v3"
    ton_wallet: str = ""
    ton_testnet: bool = True
    payment_ttl_seconds: int = 1800
    poll_seconds: int = 15

    def validate_runtime(self):
        if self.app_env == "production":
            if self.demo_mode:
                raise ValueError("Production requires DEMO_MODE=false")
            if not self.cookie_secure and self.admin_access_mode != "ssh_tunnel":
                raise ValueError("Use secure cookies for HTTPS or explicit ADMIN_ACCESS_MODE=ssh_tunnel")
            if not self.database_url.startswith("postgresql"):
                raise ValueError("Production requires PostgreSQL")
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
