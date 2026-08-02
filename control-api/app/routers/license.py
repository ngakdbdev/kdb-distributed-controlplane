"""
license.py - report the product licence status (edition, expiry, days left).
Reads LICENSE_KEY from the environment. Enforcement (blocking startup on an
invalid/expired key) is handled in main.py behind LICENSE_ENFORCE.
"""
import os

from fastapi import APIRouter, Depends

from .. import licensing
from .auth import CurrentUser, require_auth

router = APIRouter(prefix="/license", tags=["license"])


@router.get("")
def license_status(user: CurrentUser = Depends(require_auth)):
    info = licensing.validate(os.environ.get("LICENSE_KEY", ""))
    return {
        "valid": info.valid,
        "reason": info.reason,
        "edition": info.edition,
        "issued": info.issued.isoformat() if info.issued else None,
        "expiry": info.expiry.isoformat() if info.expiry else None,
        "days_remaining": info.days_remaining,
        "trial": info.is_trial,
    }
