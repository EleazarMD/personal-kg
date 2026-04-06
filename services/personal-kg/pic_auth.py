"""Authentication for PIC endpoints."""

from fastapi import Request, HTTPException
from config import get_settings

settings = get_settings()


async def verify_pic_auth(request: Request):
    """Verify PIC authentication headers."""
    if request.url.path == "/health":
        return
    
    if not request.url.path.startswith("/api/pic"):
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
    
    if request.method in ["POST", "PUT", "DELETE", "PATCH"]:
        if auth_header != settings.pic_admin_key:
            raise HTTPException(
                status_code=403,
                detail="Admin key required for write operations"
            )
