import os
import tempfile

os.environ["DATABASE_URL"] = "sqlite://"
os.environ["CREDENTIAL_MASTER_KEY"] = "test-master-key"
os.environ["JWT_SECRET"] = "test-jwt-secret"
os.environ["ADMIN_EMAIL"] = "admin@example.com"
os.environ["ADMIN_PASSWORD"] = "admin-pass-123"
os.environ["INFERENCE_ENGINE_API_KEY"] = ""
os.environ["INFERENCE_ENGINE_BASE_URL"] = ""
os.environ["UPLOAD_DIR"] = "/tmp/traj-test-uploads"
# Batched runs write files; tests keep them out of the checkout. Tests that inspect them set their own.
os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="traj-test-data-")
os.environ["JOBS_MODE"] = "inline"

import pytest
from fastapi.testclient import TestClient

from app.db import init_db
from app.main import app
from app import runtime
from app.providers import KeyCheck


class OfflineFetcher:
    """Tests never fetch from the network unless a test installs a fetcher that serves pages."""

    def fetch(self, url: str, kind: str):
        from app.fetch import FetchError

        raise FetchError("the network is off in tests")

    def download(self, url: str, destination, max_bytes: int):
        from app.fetch import FetchError

        raise FetchError("the network is off in tests")


class AcceptingKeyChecker:
    """Tests never reach a provider: every well-formed key is accepted unless a test installs another checker."""

    def check(self, provider: str, key: str) -> KeyCheck:
        return KeyCheck("valid", "accepted in tests")


@pytest.fixture()
def client():
    init_db("sqlite://")
    runtime.judge = None
    runtime.searcher = None
    runtime.key_checker = AcceptingKeyChecker()
    runtime.fetcher = OfflineFetcher()
    with TestClient(app) as test_client:
        yield test_client
    runtime.judge = None
    runtime.searcher = None
    runtime.key_checker = None
    runtime.fetcher = None
