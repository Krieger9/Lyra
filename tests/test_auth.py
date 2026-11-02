import time
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

from app import app
from auth import settings


class ChatAuthorizationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tenant, self.frontend, self.backend = [str(uuid4()) for _ in range(3)]
        self.resource = "api://" + self.backend
        env = patch.dict("os.environ", {
            "ENTRA_TENANT_ID": self.tenant, "ENTRA_FE_CLIENT_ID": self.frontend,
            "ENTRA_BE_CLIENT_ID": self.backend, "ENTRA_API_URI": self.resource,
            "ENTRA_CHAT_SCOPE": "access-as-user", "ENTRA_CHAT_ROLES": "User.Chat,User.Admin",
            "ENTRA_REDIRECT_URI": "http://localhost:8765/auth/callback",
        })
        env.start()
        self.addCleanup(env.stop)
        settings.cache_clear()
        self.addCleanup(settings.cache_clear)
        self.private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        public_key = jwt.algorithms.RSAAlgorithm.to_jwk(self.private_key.public_key(), as_dict=True)
        self.keys = patch("jwt.PyJWKClient.fetch_data", return_value={"keys": [dict(public_key, kid="test")]})
        self.keys.start()
        self.addCleanup(self.keys.stop)
        studio = patch.object(app.state, "studio", create=True, new=AsyncMock())
        self.studio = studio.start()
        self.studio.run.return_value = "hello"
        self.addCleanup(studio.stop)
        self.browser = httpx.AsyncClient(transport=httpx.ASGITransport(app), base_url="http://localhost:8765")
        self.addAsyncCleanup(self.browser.aclose)
        now = int(time.time())
        self.claims = {"ver": "2.0", "iss": f"https://login.microsoftonline.com/{self.tenant}/v2.0",
                       "aud": self.backend, "azp": self.frontend, "tid": self.tenant, "sub": "test-user",
                       "scp": "access-as-user", "roles": ["User.Chat"], "nbf": now - 10, "exp": now + 300}

    async def chat(self, **changes):
        token = jwt.encode(self.claims | changes, self.private_key, algorithm="RS256", headers={"kid": "test"})
        return await self.browser.post("/api/chat", json={"message": "hi"}, headers={"Authorization": "Bearer " + token})

    async def test_configuration_and_chat(self):
        config = (await self.browser.get("/auth/config")).json()
        self.assertEqual(config["clientId"], self.frontend)
        self.assertEqual(config["scope"], self.resource + "/access-as-user")
        self.assertEqual((await self.browser.get("/auth/callback")).status_code, 200)
        for role in ("User.Chat", "User.Admin"):
            self.assertEqual((await self.chat(roles=[role])).status_code, 200)
        legacy = {"ver": "1.0", "iss": f"https://sts.windows.net/{self.tenant}/", "aud": self.resource,
                  "appid": self.frontend}
        self.assertEqual((await self.chat(**legacy)).status_code, 200)

    async def test_reject_invalid_identity(self):
        self.assertEqual((await self.browser.post("/api/chat", json={"message": "hi"})).status_code, 401)
        for changes in ({"aud": self.frontend}, {"iss": "other"}, {"tid": str(uuid4())}, {"exp": 0}):
            with self.subTest(changes=changes):
                self.assertEqual((await self.chat(**changes)).status_code, 401)
        self.private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self.assertEqual((await self.chat()).status_code, 401)
        self.studio.run.assert_not_awaited()

    async def test_reject_insufficient_permissions(self):
        for changes in ({"azp": str(uuid4())}, {"scp": "access-as-admin"}, {"scp": ""},
                        {"roles": []}, {"roles": ["Other"]}):
            with self.subTest(changes=changes):
                self.assertEqual((await self.chat(**changes)).status_code, 403)
        self.studio.run.assert_not_awaited()
