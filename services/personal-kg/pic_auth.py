"""Authentication for PIC endpoints.

Static-key auth (X-PIC-Read-Key / X-PIC-Admin-Key) plus an optional JIT
(zero-tolerance) grant check against the approval-service for write operations.

Reads use the static key only. Writes (POST/PUT/PATCH/DELETE) require the admin
key and -- when the caller presents a JIT grant id via the X-JIT-Grant-Id
header -- the approval-service is consulted to confirm the grant is still
active (status == 'approved' and not expired).

Graceful degradation: if the approval-service is unreachable (connection error,
timeout, or 5xx) the JIT check is SKIPPED and the request is allowed on the
strength of the static admin key alone -- the approval infrastructure must not
become a hard dependency that blocks every write when it is down. Only a grant
that is presented but definitively invalid/expired/not-found causes a write to
be rejected.

Set JIT_REQUIRE_GRANT=true to enforce that *every* write present a valid grant
(full zero-tolerance). With the default (false) writes without a grant header
continue to work, but any grant that IS presented must be valid/active.
"""

import logging
from datetime import datetime, timezone

import httpx
from fastapi import Request, HTTPException

from config import get_settings

logger = logging.getLogger("pic_auth")

settings = get_settings()

# HTTP methods that mutate state -> require the admin key (+ JIT grant check).
WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

# Header a caller uses to present an active JIT grant (an approval-service
# approval id whose status is 'approved').
JIT_GRANT_HEADER = "X-JIT-Grant-Id"


async def _check_jit_grant(request: Request) -> str:
    """Consult the approval-service for an active JIT grant on a write.

    Returns one of:
      * "passed"  - a grant id was presented and verified active (approved, not
                    expired); the write is allowed.
      * "failed"  - a grant id was presented but is not active / not found, OR
                    no grant was presented while JIT_REQUIRE_GRANT is enabled;
                    the write is rejected.
      * "skipped" - the check could not be performed (no grant presented while
                    enforcement is disabled, or the approval-service was
                    unreachable / returned an error); the write is allowed.
    """
    grant_id = request.headers.get(JIT_GRANT_HEADER)

    # No grant presented.
    if not grant_id:
        if settings.jit_require_grant:
            logger.info(
                "JIT check failed: no %s header presented (JIT_REQUIRE_GRANT enabled)",
                JIT_GRANT_HEADER,
            )
            return "failed"
        logger.info(
            "JIT check skipped: no %s header presented (JIT_REQUIRE_GRANT disabled)",
            JIT_GRANT_HEADER,
        )
        return "skipped"

    base_url = (settings.approval_service_url or "").rstrip("/")
    url = f"{base_url}/api/approvals/{grant_id}"
    headers = {"X-API-Key": settings.approval_api_key}

    try:
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(url, headers=headers)
    except Exception as exc:  # network / connection / timeout errors
        logger.warning("JIT check skipped: approval-service unreachable (%s)", exc)
        return "skipped"

    # Service-side failures are treated as "unreachable" -> degrade gracefully.
    if resp.status_code >= 500:
        logger.warning(
            "JIT check skipped: approval-service returned HTTP %s", resp.status_code
        )
        return "skipped"

    if resp.status_code == 404:
        logger.info("JIT check failed: grant %s not found", grant_id)
        return "failed"

    if resp.status_code != 200:
        # Unexpected non-success (e.g. 401 from misconfigured key): don't
        # hard-block writes, but surface it so it can be fixed.
        logger.warning(
            "JIT check skipped: approval-service returned HTTP %s for grant %s",
            resp.status_code,
            grant_id,
        )
        return "skipped"

    try:
        body = resp.json()
        approval = body.get("approval", body) if isinstance(body, dict) else {}
        status = approval.get("status")
        expires_at = approval.get("expires_at")
    except Exception as exc:
        logger.warning(
            "JIT check skipped: could not parse approval-service response (%s)", exc
        )
        return "skipped"

    expired = False
    if expires_at:
        try:
            exp = datetime.fromisoformat(str(expires_at).replace("Z", "+00:00"))
            expired = exp < datetime.now(timezone.utc)
        except Exception:
            # Unparseable expiry -> don't assume expired.
            expired = False

    if status == "approved" and not expired:
        logger.info("JIT check passed: grant %s is active (status=approved)", grant_id)
        return "passed"

    logger.info(
        "JIT check failed: grant %s is not active (status=%s, expired=%s)",
        grant_id,
        status,
        expired,
    )
    return "failed"


async def verify_pic_auth(request: Request):
    """Verify PIC authentication headers.

    All protected endpoints require a static X-PIC-Read-Key or X-PIC-Admin-Key.
    Write operations additionally require the admin key, and then perform an
    optional JIT grant check against the approval-service (see
    _check_jit_grant).
    """
    if request.url.path == "/health":
        return

    # Protect both the legacy /api/pic namespace and the current /api/pcg one.
    # Same keys (X-PIC-Read-Key / X-PIC-Admin-Key) -- see AGENTIFICATION_PLAN.md.
    if not (
        request.url.path.startswith("/api/pic")
        or request.url.path.startswith("/api/pcg")
    ):
        return

    auth_header = request.headers.get("X-PIC-Read-Key") or request.headers.get("X-PIC-Admin-Key")

    if not auth_header:
        raise HTTPException(
            status_code=401,
            detail="Missing PIC authentication header (X-PIC-Read-Key or X-PIC-Admin-Key)"
        )

    if auth_header not in [settings.pic_read_key, settings.pic_admin_key]:
        raise HTTPException(
            status_code=403,
            detail="Invalid PIC authentication key"
        )

    if request.method in WRITE_METHODS:
        if auth_header != settings.pic_admin_key:
            raise HTTPException(
                status_code=403,
                detail="Admin key required for write operations"
            )

        # --- JIT / zero-tolerance integration ---
        # After the static admin key check passes, consult the approval-service
        # to verify the caller has an active JIT grant (when one is presented).
        # Graceful degradation: an unreachable approval-service never blocks
        # writes -- only a presented-but-invalid grant does.
        jit_result = await _check_jit_grant(request)
        if jit_result == "failed":
            raise HTTPException(
                status_code=403,
                detail="JIT grant check failed: no active approval-service grant for this write operation"
            )
