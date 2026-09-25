import os

os.environ["DATABASE_URL"] = "sqlite://"
os.environ["CREDENTIAL_MASTER_KEY"] = "test-master-key"
os.environ["JWT_SECRET"] = "test-jwt-secret"
os.environ["ADMIN_EMAIL"] = "admin@example.com"
os.environ["ADMIN_PASSWORD"] = "admin-pass-123"
os.environ["INFERENCE_ENGINE_API_KEY"] = ""
os.environ["INFERENCE_ENGINE_BASE_URL"] = ""
os.environ["UPLOAD_DIR"] = "/tmp/traj-test-uploads"
os.environ["JOBS_MODE"] = "inline"

import pytest
from fastapi.testclient import TestClient

from app.db import init_db
from app.main import app
from app import runtime


@pytest.fixture()
def client():
    init_db("sqlite://")
    runtime.judge = None
    runtime.searcher = None
    with TestClient(app) as test_client:
        yield test_client
    runtime.judge = None
    runtime.searcher = None
