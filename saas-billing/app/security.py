"""Password hashing (Argon2id) and JWT access tokens."""
import uuid
from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from app.config import get_settings
from app.exceptions import AuthenticationError

_hasher = PasswordHasher()
# Verified against when the account does not exist, so response time doesn't reveal
# whether an email is registered (user-enumeration timing attack).
DUMMY_HASH = _hasher.hash("timing-equalisation-placeholder")


def hash_password(plain: str) -> str:
    return _hasher.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return _hasher.verify(hashed, plain)
    except (VerificationError, InvalidHashError):
        return False


def needs_rehash(hashed: str) -> bool:
    return _hasher.check_needs_rehash(hashed)


def create_access_token(*, user_id: uuid.UUID, tenant_id: uuid.UUID) -> tuple[str, int]:
    """Returns (token, expires_in_seconds). The tenant id is embedded as the `tid` claim and
    is the ONLY source of tenant identity for authenticated requests."""
    s = get_settings()
    now = datetime.now(UTC)
    expires = timedelta(minutes=s.access_token_expire_minutes)
    payload = {
        "sub": str(user_id),
        "tid": str(tenant_id),
        "iss": s.jwt_issuer,
        "iat": now,
        "exp": now + expires,
    }
    token = jwt.encode(payload, s.secret_key.get_secret_value(), algorithm=s.jwt_algorithm)
    return token, int(expires.total_seconds())


def decode_access_token(token: str) -> tuple[uuid.UUID, uuid.UUID]:
    """Returns (user_id, tenant_id) or raises AuthenticationError."""
    s = get_settings()
    try:
        claims = jwt.decode(
            token,
            s.secret_key.get_secret_value(),
            algorithms=[s.jwt_algorithm],
            issuer=s.jwt_issuer,
            options={"require": ["exp", "sub", "tid", "iss"]},
        )
        return uuid.UUID(claims["sub"]), uuid.UUID(claims["tid"])
    except (jwt.PyJWTError, ValueError, KeyError) as exc:
        raise AuthenticationError("Invalid or expired token") from exc
