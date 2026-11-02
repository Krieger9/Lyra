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
            "ENTRA_ADMIN_SCOPE": "access-as-admin", "ENTRA_ADMIN_ROLES": "User.Admin",
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

    def headers(self, **changes):
        token = jwt.encode(self.claims | changes, self.private_key, algorithm="RS256", headers={"kid": "test"})
        return {"Authorization": "Bearer " + token}

    async def chat(self, **changes):
        return await self.browser.post("/api/chat", json={"message": "hi"}, headers=self.headers(**changes))

    async def test_admin_home_requires_admin_scope_and_role(self):
        config = (await self.browser.get("/auth/config?site=admin")).json()
        self.assertEqual(config["scope"], self.resource + "/access-as-admin")
        self.assertEqual((await self.browser.get("/api/admin/home")).status_code, 401)
        for scope, role in [("access-as-user", "User.Admin"), ("access-as-admin", "User.Chat")]:
            response = await self.browser.get("/api/admin/home", headers=self.headers(scp=scope, roles=[role]))
            self.assertEqual(response.status_code, 403)
        self.studio.prompts.library.name = "lyra"
        self.studio.model.model.id = "test-model"
        response = await self.browser.get("/api/admin/home", headers=self.headers(scp="access-as-admin", roles=["User.Admin"]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"prompt_library": "lyra", "chat_model": "test-model"})

    async def test_definition_endpoints_require_admin_scope_and_role(self):
        requests = [
            ("GET", "/api/admin/catalog"),
            ("POST", "/api/admin/agents"),
            ("PUT", "/api/admin/agents/" + str(uuid4())),
            ("POST", "/api/admin/prompts"),
        ]
        for method, path in requests:
            response = await self.browser.request(method, path)
            self.assertEqual(response.status_code, 401)
            for scope, role in [("access-as-user", "User.Admin"), ("access-as-admin", "User.Chat")]:
                response = await self.browser.request(method, path, headers=self.headers(scp=scope, roles=[role]))
                self.assertEqual(response.status_code, 403)

    async def test_chat_navigation_uses_verified_roles(self):
        path = "/api/chat/session"
        self.assertEqual((await self.browser.get(path)).status_code, 401)
        for role, visible in [("User.Chat", False), ("User.Admin", True)]:
            response = await self.browser.get(path, headers=self.headers(roles=[role]))
            self.assertEqual(response.json(), {"can_configure": visible})
            self.assertEqual(response.headers["Cache-Control"], "no-store")

    async def test_missing_admin_config_leaves_chat_available(self):
        with patch.dict("os.environ", {"ENTRA_ADMIN_SCOPE": ""}):
            settings.cache_clear()
            self.assertEqual((await self.browser.get("/auth/config?site=admin")).status_code, 503)
            self.assertEqual((await self.chat()).status_code, 200)

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
