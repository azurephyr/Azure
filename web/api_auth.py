import hmac
import logging
import os
import secrets
import threading
import time
from collections import defaultdict
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel

logger = logging.getLogger("web.auth")

router = APIRouter()

# Security config - NO DEFAULTS FOR PRODUCTION
SECRET_KEY = os.environ.get("AZURE_WEB_SECRET")
if not SECRET_KEY:
    # Import-safe development fallback. start_web_server() rejects this when
    # the effective web feature is enabled outside explicit development mode.
    SECRET_KEY = secrets.token_urlsafe(32)
    logger.warning(
        "⚠️ AZURE_WEB_SECRET not set! Using random secret. "
        "All tokens will be invalidated on restart. "
        "Set AZURE_WEB_SECRET in .env for production."
    )

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7  # 1 week

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/token")

# Rate limiting: 5 attempts per minute per IP
_login_attempts: dict[str, list[float]] = defaultdict(list)
_login_attempts_lock = threading.RLock()
RATE_LIMIT_WINDOW = 60  # seconds
RATE_LIMIT_MAX = 5
_LAST_LOGIN_PURGE = time.time()

# CSRF token store (per-session)
_csrf_tokens: dict[str, float] = {}
_csrf_lock = threading.Lock()
CSRF_TOKEN_TTL = 3600  # 1 hour
_LAST_CSRF_PURGE = time.time()
_CSRF_PURGE_INTERVAL = 300  # purge expired tokens every 5 minutes


def _purge_stale_login_attempts():
    """Remove IPs with no recent attempts to prevent dict growth."""
    global _LAST_LOGIN_PURGE
    with _login_attempts_lock:
        now = time.time()
        if now - _LAST_LOGIN_PURGE < 300:  # once every 5 minutes
            return
        _LAST_LOGIN_PURGE = now
        cutoff = now - RATE_LIMIT_WINDOW
        stale = [ip for ip, attempts in _login_attempts.items() if not attempts or max(attempts) < cutoff]
        for ip in stale:
            del _login_attempts[ip]


def _purge_stale_csrf_tokens():
    """Remove expired CSRF tokens to prevent dict growth."""
    global _LAST_CSRF_PURGE
    now = time.time()
    if now - _LAST_CSRF_PURGE < _CSRF_PURGE_INTERVAL:
        return
    _LAST_CSRF_PURGE = now
    cutoff = now - CSRF_TOKEN_TTL
    with _csrf_lock:
        stale = [t for t, ts in _csrf_tokens.items() if ts < cutoff]
        for t in stale:
            _csrf_tokens.pop(t, None)


def _rate_limit_check(ip: str) -> bool:
    """Return True if the IP is within rate limit, False if blocked."""
    with _login_attempts_lock:
        _purge_stale_login_attempts()
        now = time.time()
        cutoff = now - RATE_LIMIT_WINDOW
        _login_attempts[ip] = [t for t in _login_attempts[ip] if t > cutoff]
        if len(_login_attempts[ip]) >= RATE_LIMIT_MAX:
            return False
        _login_attempts[ip].append(now)
        return True


def generate_csrf_token() -> str:
    """Generate a CSRF token for form submissions."""
    _purge_stale_csrf_tokens()
    token = secrets.token_urlsafe(32)
    with _csrf_lock:
        _csrf_tokens[token] = time.time()
    return token


def validate_csrf_token(token: str | None) -> bool:
    """Validate a CSRF token. Returns True if valid."""
    _purge_stale_csrf_tokens()
    if not token:
        return False
    with _csrf_lock:
        ts = _csrf_tokens.pop(token, None)
    if ts is None:
        return False
    return not time.time() - ts > CSRF_TOKEN_TTL

class Token(BaseModel):
    access_token: str
    token_type: str
    role: str
    username: str

def create_access_token(data: dict, expires_delta: timedelta | None = None):
    to_encode = data.copy()
    now = datetime.now(UTC)
    expire = now + expires_delta if expires_delta else now + timedelta(minutes=15)
    to_encode.update({"exp": expire, "iat": now})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


def decode_token(token: str) -> dict | None:
    """Decode and validate a JWT token. Returns payload dict or None."""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except JWTError:
        return None

async def get_current_user(request: Request, token: str = Depends(oauth2_scheme)):
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    payload = decode_token(token)
    if payload is None:
        raise credentials_exception
    username: str = payload.get("sub")
    role: str = payload.get("role")
    if username is None:
        raise credentials_exception
    return {"username": username, "role": role, "exp": payload.get("exp"), "iat": payload.get("iat")}


async def require_admin(current_user: dict = Depends(get_current_user)):
    """
    Dependency that requires admin/owner role for mutation operations.

    Raises 403 Forbidden if user doesn't have sufficient privileges.
    """
    if current_user.get("role") not in ("admin", "owner"):
        logger.warning(f"Unauthorized mutation attempt by {current_user.get('username')} with role {current_user.get('role')}")
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This operation requires admin privileges"
        )
    return current_user

def _resolve_admin_credentials() -> tuple[str | None, str | None, bool]:
    """
    Resolve admin password from environment.

    Returns (bcrypt_hash, plaintext, using_default).

    SECURITY POLICY:
    - Default "admin/admin" is ONLY used when AZURE_DEV_MODE=1 is set AND
      the dashboard is enabled but no real credentials exist.
    - If neither AZURE_ADMIN_PASSWORD_HASH nor AZURE_ADMIN_PASSWORD is set
      and AZURE_DEV_MODE != 1, login is DISABLED (returns None, None, True).
    - To allow the dashboard at all without a real admin password, set
      AZURE_DEV_MODE=1. The clear-text default password is logged once as
      a warning, never returned to the client.
    """
    admin_pass_hash = os.environ.get("AZURE_ADMIN_PASSWORD_HASH")
    admin_pass_plain = os.environ.get("AZURE_ADMIN_PASSWORD")
    dev_mode = os.environ.get("AZURE_DEV_MODE", "0") == "1"

    if admin_pass_hash:
        return admin_pass_hash, admin_pass_plain, False
    if admin_pass_plain:
        # The literal weak default "admin" is never accepted outside dev mode,
        # even when set explicitly — otherwise the documented policy (default
        # creds require AZURE_DEV_MODE=1) is trivially bypassed by setting
        # AZURE_ADMIN_PASSWORD=admin in production.
        if admin_pass_plain == "admin" and not dev_mode:
            logger.error(
                "AZURE_ADMIN_PASSWORD is set to the insecure default 'admin'. "
                "Refusing to use it without AZURE_DEV_MODE=1. Set a strong password."
            )
            return None, None, True
        return admin_pass_hash, admin_pass_plain, False

    # No credentials configured. Require explicit opt-in for a default account.
    if dev_mode:
        logger.warning(
            "AZURE_DEV_MODE=1 with no password configured — defaulting to admin/admin. "
            "Do NOT use this in production."
        )
        return None, "admin", True

    logger.error(
        "Dashboard admin credentials not configured. Set AZURE_ADMIN_PASSWORD_HASH or "
        "AZURE_ADMIN_PASSWORD in .env, or set AZURE_DEV_MODE=1 to use the default password."
    )
    return None, None, True


@router.post("/token", response_model=Token)
async def login_for_access_token(
    request: Request,
    form_data: OAuth2PasswordRequestForm = Depends(),
):
    """
    Authenticate and return JWT access token.

    SECURITY: This endpoint requires configured credentials.
    - Username must be 'admin'
    - Password must be set via AZURE_ADMIN_PASSWORD_HASH (bcrypt hash)
      OR AZURE_ADMIN_PASSWORD (plaintext, less secure)
    - When neither is set, only AZURE_DEV_MODE=1 enables the default
      admin/admin account (development convenience).

    Rate limited to 5 attempts per minute per IP.

    To generate a bcrypt hash:
        python -c "from passlib.context import CryptContext; print(CryptContext(schemes=['bcrypt']).hash('your-password'))"

    Then set in .env:
        AZURE_ADMIN_PASSWORD_HASH=<bcrypt-hash>
    """
    # Rate limiting
    client_ip = request.client.host if request.client else "unknown"
    if not _rate_limit_check(client_ip):
        logger.warning(f"Rate limit exceeded for IP {client_ip}")
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many login attempts. Try again in 1 minute.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    admin_pass_hash, admin_pass_plain, using_default = _resolve_admin_credentials()
    using_default_password = using_default and admin_pass_plain == "admin"

    if not admin_pass_hash and not admin_pass_plain:
        # No credentials at all and no dev mode — refuse every attempt.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Dashboard credentials are not configured on this server.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Verify username
    if form_data.username != "admin":
        logger.warning("Failed login attempt: invalid username '%s'", form_data.username[:32])
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Verify password
    authenticated = False

    if admin_pass_hash:
        # Verify against bcrypt hash (recommended)
        try:
            authenticated = pwd_context.verify(form_data.password, admin_pass_hash)
        except Exception as exc:
            logger.error(f"Password hash verification failed: {exc}")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Authentication system error",
            ) from exc
    elif admin_pass_plain:
        # Constant-time plaintext comparison (includes default "admin").
        if using_default_password:
            logger.warning(
                "Dashboard login using AZURE_DEV_MODE=1 default creds admin/admin."
            )
        else:
            logger.warning(
                "Using plaintext password comparison. "
                "Use AZURE_ADMIN_PASSWORD_HASH for better security."
            )
        try:
            authenticated = hmac.compare_digest(
                form_data.password.encode("utf-8"),
                admin_pass_plain.encode("utf-8"),
            )
        except (AttributeError, TypeError):
            authenticated = False

    if not authenticated:
        logger.warning("Failed login attempt for user 'admin'")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Success - generate token. Use UTC datetimes for cross-version JWT compatibility.
    logger.info("Successful admin login")
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": "admin", "role": "owner"},
        expires_delta=access_token_expires
    )

    now = datetime.now(UTC)
    expires_at = now + access_token_expires

    return {
        "access_token": access_token,
        "token_type": "bearer",
        "role": "owner",
        "username": "admin",
        "expires_at": int(expires_at.timestamp()),
    }

@router.get("/me")
async def read_users_me(current_user: dict = Depends(get_current_user)):
    return current_user


class TokenRefreshRequest(BaseModel):
    token: str


_refresh_attempts: dict[str, list[float]] = defaultdict(list)
_refresh_attempts_lock = threading.Lock()
REFRESH_RATE_LIMIT_WINDOW = 60  # seconds
REFRESH_RATE_LIMIT_MAX = 10


def _purge_stale_refresh_attempts(now: float | None = None) -> None:
    """Remove entries older than REFRESH_RATE_LIMIT_WINDOW to prevent memory leak."""
    if now is None:
        now = time.time()
    cutoff = now - REFRESH_RATE_LIMIT_WINDOW
    with _refresh_attempts_lock:
        stale_keys = [ip for ip, times in _refresh_attempts.items()
                      if not times or times[-1] < cutoff]
        for ip in stale_keys:
            del _refresh_attempts[ip]


def _refresh_rate_limit_check(ip: str) -> bool:
    """Return True if the IP is within refresh rate limit."""
    now = time.time()
    _purge_stale_refresh_attempts(now)
    cutoff = now - REFRESH_RATE_LIMIT_WINDOW
    with _refresh_attempts_lock:
        _refresh_attempts[ip] = [t for t in _refresh_attempts[ip] if t > cutoff]
        if len(_refresh_attempts[ip]) >= REFRESH_RATE_LIMIT_MAX:
            return False
        _refresh_attempts[ip].append(now)
    return True


@router.post("/refresh")
async def refresh_token(req: TokenRefreshRequest, request: Request):
    """Refresh an expiring token — returns a new token with extended expiry."""
    client_ip = request.client.host if request.client else "unknown"
    if not _refresh_rate_limit_check(client_ip):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many refresh attempts. Try again in 1 minute.",
        )

    payload = decode_token(req.token)
    if payload is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
        )
    username = payload.get("sub")
    role = payload.get("role")
    if not username:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token payload",
        )

    # Only allow refresh if token expires within 24 hours
    exp = payload.get("exp", 0)
    now = datetime.now(UTC).timestamp()
    remaining = exp - now
    if remaining > 24 * 3600:
        # Token still has plenty of life — return it as-is
        return {
            "access_token": req.token,
            "token_type": "bearer",
            "expires_at": int(exp),
        }

    # Issue a fresh token
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    new_token = create_access_token(
        data={"sub": username, "role": role},
        expires_delta=access_token_expires,
    )
    new_exp = datetime.now(UTC) + access_token_expires
    logger.info("Token refreshed for user %s", username)
    return {
        "access_token": new_token,
        "token_type": "bearer",
        "expires_at": int(new_exp.timestamp()),
    }


@router.get("/csrf")
async def get_csrf_token():
    """Get a CSRF token for state-changing POST requests."""
    token = generate_csrf_token()
    return {"csrf_token": token}


def validate_auth_config() -> dict:
    """
    Validate authentication configuration at startup.

    Returns dict with:
        - configured: bool (whether auth is properly configured)
        - warnings: list of warning messages
        - errors: list of error messages
    """
    warnings = []
    errors = []
    web_enabled = os.environ.get("AZURE_FEATURE_WEB", "1").strip().lower() in ("1", "true", "yes", "on")
    legacy_enabled = os.environ.get("AZURE_WEB_DASHBOARD", "0") == "1"
    dev_mode = os.environ.get("AZURE_DEV_MODE", "0") == "1"

    # Check secret key
    if not os.environ.get("AZURE_WEB_SECRET"):
        message = "AZURE_WEB_SECRET is required when the web dashboard is enabled."
        if (web_enabled or legacy_enabled) and not dev_mode:
            errors.append(message)
        else:
            warnings.append(message)

    # Check password configuration
    has_hash = bool(os.environ.get("AZURE_ADMIN_PASSWORD_HASH"))
    has_plain = bool(os.environ.get("AZURE_ADMIN_PASSWORD"))

    if not has_hash and not has_plain:
        if dev_mode:
            warnings.append(
                "Dashboard is using AZURE_DEV_MODE=1 with default admin/admin. "
                "Do NOT expose the dashboard publicly without setting real credentials."
            )
        else:
            errors.append(
                "Dashboard credentials are NOT configured. Set AZURE_ADMIN_PASSWORD_HASH or "
                "AZURE_ADMIN_PASSWORD in .env, or set AZURE_DEV_MODE=1 to use the development default."
            )
    elif has_plain and not has_hash:
        if os.environ.get("AZURE_ADMIN_PASSWORD") == "admin" and not dev_mode:
            errors.append(
                "Dashboard password is the default 'admin'. Change AZURE_ADMIN_PASSWORD "
                "for production. The dashboard will REFUSE logins with this password unless "
                "AZURE_DEV_MODE=1 is set."
            )
        elif os.environ.get("AZURE_ADMIN_PASSWORD") == "admin":
            warnings.append(
                "Dashboard is using AZURE_DEV_MODE=1 with the default admin/admin. "
                "Do NOT expose the dashboard publicly without setting real credentials."
            )
        else:
            warnings.append(
                "Using plaintext password (AZURE_ADMIN_PASSWORD). "
                "For better security, use AZURE_ADMIN_PASSWORD_HASH instead."
            )

    # The dashboard is "configured" only if either it has real credentials or
    # AZURE_DEV_MODE is set to allow the development default.
    configured = not errors and (has_hash or has_plain or dev_mode)

    return {
        "configured": configured,
        "warnings": warnings,
        "errors": errors
    }
