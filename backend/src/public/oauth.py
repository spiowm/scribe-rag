import hashlib
import secrets
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    OAuthAuthorizationServerProvider,
    RefreshToken,
)
from mcp.shared.auth import OAuthClientInformationFull
from mcp.shared.auth import OAuthToken as TokenResponse
from sqlalchemy import delete, select

from src.config import settings
from src.db import async_session_maker
from src.models.oauth import OAuthClient, OAuthToken

ACCESS_TTL = timedelta(hours=1)
REFRESH_TTL = timedelta(days=90)
PENDING_TTL = 10 * 60  # стільки людина має, щоб дійти до бота й повернутися з кодом
CODE_TTL = 5 * 60


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


@dataclass
class Pending:
    """Запит /authorize, що чекає, поки людина введе код із бота."""

    client_id: str
    params: AuthorizationParams
    expires_at: float


class ScribeOAuthProvider(
    OAuthAuthorizationServerProvider[AuthorizationCode, RefreshToken, AccessToken]
):
    """Сховище для OAuth-частини SDK: що зберегти й що знайти на кожному кроці.

    Клієнти й токени — у Postgres, бо мусять пережити перезапуск. Незавершені
    входи й одноразові коди живуть хвилини, тож лежать у пам'яті процесу
    (застосунок працює в один worker).
    """

    def __init__(self, login_url: str, resource_url: str):
        self.login_url = login_url
        self.resource_url = resource_url
        self.pending: dict[str, Pending] = {}
        self.codes: dict[str, AuthorizationCode] = {}

    # --- клієнти (DCR) ---

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        async with async_session_maker() as db:
            row = await db.get(OAuthClient, client_id)
        return OAuthClientInformationFull.model_validate(row.info) if row else None

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        async with async_session_maker() as db:
            db.add(
                OAuthClient(
                    client_id=client_info.client_id,
                    info=client_info.model_dump(mode="json", exclude_none=True),
                )
            )
            await db.commit()

    # --- вхід ---

    async def authorize(
        self, client: OAuthClientInformationFull, params: AuthorizationParams
    ) -> str:
        pending_id = secrets.token_urlsafe(32)
        self.pending[pending_id] = Pending(
            client.client_id, params, time.time() + PENDING_TTL
        )
        return f"{self.login_url}?pending={pending_id}"

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        code = self.codes.get(authorization_code)
        if code is None or code.client_id != client.client_id:
            return None
        if code.expires_at < time.time():
            del self.codes[authorization_code]
            return None
        return code

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> TokenResponse:
        self.codes.pop(authorization_code.code, None)  # код одноразовий
        assert authorization_code.subject is not None  # його ставить сторінка входу
        access, refresh = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        now = datetime.now(UTC)
        async with async_session_maker() as db:
            db.add(
                OAuthToken(
                    user_id=int(authorization_code.subject),
                    client_id=client.client_id,
                    access_hash=_hash(access),
                    access_expires_at=now + ACCESS_TTL,
                    refresh_hash=_hash(refresh),
                    refresh_expires_at=now + REFRESH_TTL,
                    # Ресурс один, тож клієнт, що його не назвав (RFC 8707 цього
                    # вимагає, але не всі клієнти слухаються), отримує токен саме
                    # для нього — інакше /mcp такий токен відкине.
                    resource=authorization_code.resource or self.resource_url,
                )
            )
            await db.commit()
        return TokenResponse(
            access_token=access,
            expires_in=int(ACCESS_TTL.total_seconds()),
            refresh_token=refresh,
        )

    # --- токени ---

    async def load_access_token(self, token: str) -> AccessToken | None:
        async with async_session_maker() as db:
            row = await db.scalar(
                select(OAuthToken).where(OAuthToken.access_hash == _hash(token))
            )
        if row is None or row.access_expires_at < datetime.now(UTC):
            return None
        return AccessToken(
            token=token,
            client_id=row.client_id,
            scopes=[],
            expires_at=int(row.access_expires_at.timestamp()),
            resource=row.resource,
            subject=str(row.user_id),
        )

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> RefreshToken | None:
        async with async_session_maker() as db:
            row = await db.scalar(
                select(OAuthToken).where(
                    OAuthToken.refresh_hash == _hash(refresh_token)
                )
            )
        if (
            row is None
            or row.client_id != client.client_id
            or row.refresh_expires_at < datetime.now(UTC)
        ):
            return None
        return RefreshToken(
            token=refresh_token,
            client_id=row.client_id,
            scopes=[],
            expires_at=int(row.refresh_expires_at.timestamp()),
            resource=row.resource,
            subject=str(row.user_id),
        )

    async def exchange_refresh_token(
        self,
        client: OAuthClientInformationFull,
        refresh_token: RefreshToken,
        scopes: list[str],
    ) -> TokenResponse:
        access, refresh = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        now = datetime.now(UTC)
        async with async_session_maker() as db:
            row = await db.scalar(
                select(OAuthToken).where(
                    OAuthToken.refresh_hash == _hash(refresh_token.token)
                )
            )
            assert row is not None  # SDK щойно знайшов його через load_refresh_token
            # Ротація: той самий рядок, нові хеші — старі токени більше не діють.
            row.access_hash = _hash(access)
            row.access_expires_at = now + ACCESS_TTL
            row.refresh_hash = _hash(refresh)
            row.refresh_expires_at = now + REFRESH_TTL
            await db.commit()
        return TokenResponse(
            access_token=access,
            expires_in=int(ACCESS_TTL.total_seconds()),
            refresh_token=refresh,
        )

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        hashed = _hash(token.token)
        async with async_session_maker() as db:
            await db.execute(
                delete(OAuthToken).where(
                    (OAuthToken.access_hash == hashed)
                    | (OAuthToken.refresh_hash == hashed)
                )
            )
            await db.commit()


provider = ScribeOAuthProvider(
    login_url=f"{settings.PUBLIC_URL}/login",
    resource_url=f"{settings.PUBLIC_URL}/mcp",
)
