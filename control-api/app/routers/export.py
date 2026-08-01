"""
export.py - data-export destinations the UI can offer. Read-only catalog for
now; triggering an export runs in the tenant's own environment (via the fleet
agent), where their kdb+ and cloud credentials live - see data-plane/export.
"""
from fastapi import APIRouter, Depends

from ..export_catalog import EXPORT_SINKS
from .auth import CurrentUser, require_tenant_scope

router = APIRouter(prefix="/export", tags=["export"])


@router.get("/sinks")
def list_sinks(user: CurrentUser = Depends(require_tenant_scope)):
    """Catalog of export destinations - which run offline (parquet) vs need
    cloud credentials, and what each requires."""
    return EXPORT_SINKS
