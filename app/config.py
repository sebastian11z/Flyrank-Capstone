import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
PRICING_VERSION = "illustrative-usd-v1"
API_CALL_MICRO_USD = 1_000
INPUT_TOKEN_MICRO_USD = 2
CACHED_TOKEN_MICRO_USD = 1
OUTPUT_TOKEN_MICRO_USD = 8
PRO_MONTHLY_CENTS = 1_900


@dataclass(frozen=True)
class Settings:
    db_path: Path
    stripe_secret: str = ""
    webhook_secret: str = ""
    price_id: str = ""
    base_url: str = "http://127.0.0.1:8000"

    def __post_init__(self):
        if self.stripe_secret and not self.stripe_secret.startswith("sk_test_"):
            raise ValueError("Only Stripe sk_test_ keys are permitted")
        if self.webhook_secret and not self.webhook_secret.startswith("whsec_"):
            raise ValueError("Stripe webhook secret must start with whsec_")
        if self.price_id and not self.price_id.startswith("price_"):
            raise ValueError("Stripe price ID must start with price_")
        if not self.base_url.startswith(("http://", "https://")):
            raise ValueError("BASE_URL must be an HTTP(S) URL")

    @classmethod
    def from_env(cls):
        load_dotenv(ROOT / ".env")
        return cls(
            db_path=ROOT / os.getenv("DATABASE_PATH", "data/billing.sqlite3"),
            stripe_secret=os.getenv("STRIPE_SECRET_KEY", ""),
            webhook_secret=os.getenv("STRIPE_WEBHOOK_SECRET", ""),
            price_id=os.getenv("STRIPE_PRO_PRICE_ID", ""),
            base_url=os.getenv("BASE_URL", "http://127.0.0.1:8000").rstrip("/"),
        )

