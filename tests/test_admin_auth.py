from __future__ import annotations

import os
import tempfile
import unittest

_tmp = tempfile.mkdtemp(prefix="luopita-auth-")
os.environ["LUOPITA_PROVIDER"] = "mock"
os.environ["LUOPITA_API_KEY"] = ""
os.environ["LUOPITA_DATABASE_URL"] = "memory://"
os.environ["LUOPITA_REDIS_URL"] = "memory://"
os.environ["LUOPITA_NAPCAT_ENABLED"] = "false"
os.environ.pop("LUOPITA_ADMIN_TOKEN", None)
os.environ["LUOPITA_IDENTITY_FILE"] = os.path.join(_tmp, "identity.yaml")
os.environ["LUOPITA_PERSON_FILE"] = os.path.join(_tmp, "person.yaml")

from fastapi.testclient import TestClient

from app.main import create_app
from interface.platform.napcat import _extract_outbound_message_id


class TestOutboundMessageId(unittest.TestCase):
    def test_extract_from_dict(self):
        self.assertEqual(_extract_outbound_message_id({"message_id": 12345}), "12345")
        self.assertEqual(_extract_outbound_message_id({"messageId": "99"}), "99")
        self.assertIsNone(_extract_outbound_message_id({}))
        self.assertIsNone(_extract_outbound_message_id(None))


class TestAdminAuth(unittest.TestCase):
    def test_api_requires_token_when_configured(self):
        app = create_app()
        with TestClient(app) as client:
            runtime = app.state.runtime
            runtime.config = runtime.config.model_copy(update={"admin_token": "secret-admin"})
            denied = client.get("/api/config")
            self.assertEqual(denied.status_code, 401)
            ok = client.get("/api/config", headers={"Authorization": "Bearer secret-admin"})
            self.assertEqual(ok.status_code, 200)
            self.assertTrue(ok.json()["config"]["admin_auth_required"])

    def test_health_stays_open(self):
        app = create_app()
        with TestClient(app) as client:
            runtime = app.state.runtime
            runtime.config = runtime.config.model_copy(update={"admin_token": "secret-admin"})
            resp = client.get("/health")
            self.assertEqual(resp.status_code, 200)


if __name__ == "__main__":
    unittest.main()
