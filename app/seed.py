import os
import secrets

from dotenv import set_key

from app.config import ROOT, Settings
from app.db import connect, key_hash, migrate
from app.metering import utc_now


def seed(settings: Settings, keys: dict[str, str]):
    if any(len(key) < 32 for key in keys.values()) or len(set(keys.values())) != len(keys):
        raise ValueError("Demo API keys must be distinct and at least 32 characters")
    migrate(settings.db_path)
    with connect(settings.db_path, write=True) as db:
        for tenant_id, key in keys.items():
            existing = db.execute("SELECT api_key_hash FROM tenants WHERE id = ?", (tenant_id,)).fetchone()
            if existing and existing["api_key_hash"] != key_hash(key):
                raise ValueError("Seed refuses to rotate existing tenant keys; restore the original .env")
            db.execute("INSERT OR IGNORE INTO tenants (id,name,api_key_hash) VALUES (?,?,?)",
                       (tenant_id, tenant_id.replace("_", " ").title(), key_hash(key)))
            db.execute("INSERT OR IGNORE INTO subscriptions VALUES (?,?,?,?,?)",
                       (tenant_id, "free", "free", None, utc_now().isoformat()))


def main():
    settings = Settings.from_env()
    keys = {}
    for tenant_id, variable in (("tenant_a", "DEMO_TENANT_A_KEY"), ("tenant_b", "DEMO_TENANT_B_KEY")):
        value = os.getenv(variable) or secrets.token_urlsafe(32)
        if not os.getenv(variable):
            set_key(ROOT / ".env", variable, value)
        keys[tenant_id] = value
    seed(settings, keys)
    print("Seeded tenant_a and tenant_b without resetting usage or subscriptions. Keys are in .env; never commit it.")


if __name__ == "__main__":
    main()
