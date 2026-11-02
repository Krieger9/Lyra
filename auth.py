import os
from functools import lru_cache
from urllib.parse import urlsplit
from uuid import UUID

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer


bearer = HTTPBearer(auto_error=False)


class EntraKeys(jwt.PyJWKClient):
    """Keep shared Microsoft signing keys and keys scoped to this tenant's issuer."""

    def __init__(self, uri: str, tenant: str):
        super().__init__(uri, timeout=5)
        self.tenant = tenant

    def fetch_data(self):
        data = super().fetch_data()
        issuer = f"https://login.microsoftonline.com/{self.tenant}/v2.0"
        data["keys"] = [
            key for key in data["keys"]
            if key.get("issuer", issuer).replace("{tenantid}", self.tenant) == issuer
        ]
        return data


class EntraAuth:
    def __init__(self):
        self.tenant = str(UUID(os.environ["ENTRA_TENANT_ID"]))
        self.frontend = str(UUID(os.environ["ENTRA_FE_CLIENT_ID"]))
        self.backend = str(UUID(os.environ["ENTRA_BE_CLIENT_ID"]))
        self.resource = os.environ["ENTRA_API_URI"].strip().rstrip("/")
        self.scope = os.environ["ENTRA_CHAT_SCOPE"].strip()
        self.roles = {r.strip() for r in os.environ["ENTRA_CHAT_ROLES"].split(",") if r.strip()}
        self.admin_scope = os.getenv("ENTRA_ADMIN_SCOPE", "").strip()
        self.admin_roles = {r.strip() for r in os.getenv("ENTRA_ADMIN_ROLES", "User.Admin").split(",") if r.strip()}
        self.redirect_uri = os.environ["ENTRA_REDIRECT_URI"].strip()
        uri = urlsplit(self.redirect_uri)
        local = uri.scheme == "http" and uri.hostname in {"localhost", "127.0.0.1"}
        if (not (uri.scheme == "https" or local) or not uri.hostname or uri.query or uri.fragment
                or uri.username is not None or uri.path != "/auth/callback"):
            raise ValueError("ENTRA_REDIRECT_URI must point to Lyra's /auth/callback")
        if (not self.roles or not self.resource or not self.scope or "/" in self.scope
                or self.scope == ".default" or any(c.isspace() for c in self.scope)):
            raise ValueError("Set the API URI, delegated scope name and permitted chat roles")
        self.authority = f"https://login.microsoftonline.com/{self.tenant}"
        self.keys = {
            "1.0": EntraKeys(f"{self.authority}/discovery/keys", self.tenant),
            "2.0": EntraKeys(f"{self.authority}/discovery/v2.0/keys", self.tenant),
        }

    def check_admin_config(self):
        if (not self.admin_roles or not self.admin_scope or self.admin_scope in {self.scope, ".default"}
                or "/" in self.admin_scope or any(c.isspace() for c in self.admin_scope)):
            raise HTTPException(503, "Configure a distinct ENTRA_ADMIN_SCOPE and permitted admin roles.")

    def authorize(self, token: str, *, admin: bool = False):
        if admin:
            self.check_admin_config()
        try:
            # Only select between the two fixed tenant configurations before verification.
            version = jwt.decode(token, options={"verify_signature": False}).get("ver")
            if version not in ("1.0", "2.0"):
                raise jwt.InvalidTokenError()
            v2 = version == "2.0"
            claims = jwt.decode(
                token, self.keys[version].get_signing_key_from_jwt(token).key, algorithms=["RS256"],
                audience=self.backend if v2 else [self.resource, self.backend],
                issuer=f"{self.authority}/v2.0" if v2 else f"https://sts.windows.net/{self.tenant}/",
                options={"require": ["exp", "nbf", "tid", "sub"]},
            )
            if claims["tid"] != self.tenant:
                raise jwt.InvalidTokenError()
        except jwt.PyJWKClientConnectionError as exc:
            raise HTTPException(503, "Could not reach the Entra signing key service.") from exc
        except jwt.PyJWTError as exc:
            raise HTTPException(401, "Invalid or expired access token.", headers={"WWW-Authenticate": "Bearer"}) from exc
        if claims.get("azp" if v2 else "appid") != self.frontend:
            raise HTTPException(403, "Access token was not issued to the Lyra frontend.")
        scopes = claims.get("scp")
        required_scope = self.admin_scope if admin else self.scope
        if not isinstance(scopes, str) or required_scope not in scopes.split():
            raise HTTPException(403, f"Access requires the {required_scope} delegated scope.")
        roles = claims.get("roles")
        allowed_roles = self.admin_roles if admin else self.roles
        if not isinstance(roles, list) or not any(role in roles for role in allowed_roles):
            raise HTTPException(403, "Your account does not have an assigned role for this area.")
        return roles


@lru_cache(maxsize=1)
def settings() -> EntraAuth:
    try:
        return EntraAuth()
    except (KeyError, ValueError) as exc:
        raise HTTPException(503, "Complete the Entra settings in .env and restart Lyra.") from exc


def require_chat_access(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
    if credentials is None:
        raise HTTPException(401, "Sign in to use chat.", headers={"WWW-Authenticate": "Bearer"})
    return settings().authorize(credentials.credentials)


def require_admin_access(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
    if credentials is None:
        raise HTTPException(401, "Sign in to configure Lyra.", headers={"WWW-Authenticate": "Bearer"})
    settings().authorize(credentials.credentials, admin=True)
