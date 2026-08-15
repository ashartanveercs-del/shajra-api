from config import get_settings
from fastapi import HTTPException


def require_public_writes() -> None:
    if not get_settings().public_writes_enabled:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "PUBLIC_WRITES_DISABLED",
                "message": "Submissions are temporarily unavailable.",
            },
        )


def require_relationship_writes() -> None:
    if not get_settings().relationship_writes_enabled:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "RELATIONSHIP_WRITES_DISABLED",
                "message": "Relationship editing is temporarily unavailable.",
            },
        )
