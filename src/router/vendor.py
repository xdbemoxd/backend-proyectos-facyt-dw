"""Endpoints de proveedores (RF-4). Solo Administrador y Aprobador."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.config.db import get_db
from src.schema.user import CurrentUser
from src.schema.vendor import (
    PaginatedVendors,
    VendorCreate,
    VendorOut,
    VendorUpdate,
)
from src.service import vendor as vendor_service
from src.utils.auth import require_roles
from src.utils.roles import ADMIN_ROLE, APPROVER_ROLE

router_vendors = APIRouter(prefix="/vendors", tags=["vendors"])

_MANAGERS = (ADMIN_ROLE, APPROVER_ROLE)


@router_vendors.get("", response_model=PaginatedVendors)
async def list_vendors(
    _: CurrentUser = Depends(require_roles(*_MANAGERS)),
    db: AsyncSession = Depends(get_db),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    search: str | None = Query(default=None, max_length=255),
):
    return await vendor_service.list_vendors(
        db, limit=limit, offset=offset, search=search
    )


@router_vendors.get("/{vendor_id}", response_model=VendorOut)
async def get_vendor(
    vendor_id: uuid.UUID,
    current_user: CurrentUser = Depends(require_roles(*_MANAGERS)),
    db: AsyncSession = Depends(get_db),
):
    return await vendor_service.get_vendor(db, vendor_id, current_user)


@router_vendors.post(
    "", response_model=VendorOut, status_code=status.HTTP_201_CREATED
)
async def create_vendor(
    payload: VendorCreate,
    request: Request,
    current_user: CurrentUser = Depends(require_roles(*_MANAGERS)),
    db: AsyncSession = Depends(get_db),
):
    return await vendor_service.create_vendor(
        db, payload, current_user, request=request
    )


@router_vendors.patch("/{vendor_id}", response_model=VendorOut)
async def update_vendor(
    vendor_id: uuid.UUID,
    payload: VendorUpdate,
    request: Request,
    current_user: CurrentUser = Depends(require_roles(*_MANAGERS)),
    db: AsyncSession = Depends(get_db),
):
    return await vendor_service.update_vendor(
        db, vendor_id, payload, current_user, request=request
    )
