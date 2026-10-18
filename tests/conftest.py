import pytest

from app.config import Settings
from app.seed import seed

KEY_A = "test-tenant-a-" + "a" * 32
KEY_B = "test-tenant-b-" + "b" * 32


@pytest.fixture
def settings(tmp_path):
    value = Settings(db_path=tmp_path / "billing.sqlite3", stripe_secret="sk_test_fixture_only",
                     webhook_secret="whsec_fixture_only", price_id="price_pro")
    seed(value, {"tenant_a": KEY_A, "tenant_b": KEY_B})
    return value

