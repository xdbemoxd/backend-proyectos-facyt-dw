"""Servicio de solicitudes de gasto (RF-5, RN-7).

Maquina de estados estricta:

    Borrador -> Pendiente -> Aprobado -> Ejecutado
                          -> Rechazado (terminal)

Permisos:

- Crear: Administrador o Coordinador (el coordinador solo en sus proyectos).
- Enviar a Pendiente: el solicitante o el Administrador.
- Aprobar / Rechazar: Administrador o Aprobador; un Aprobador **no** puede
  aprobar ni rechazar una solicitud creada por el mismo (autoaprobacion).
  El Administrador si puede, por decision operativa documentada.
- Ejecutar: Administrador o Aprobador.
- Leer: Administrador y Aprobador ven todo; Coordinador solo sus proyectos.

Toda mutacion se audita en la misma transaccion con ``before`` y ``after``.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from fastapi import HTTPException, Request, status
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from src.model.expense import (
    ExpenseApprovalModel,
    StatusExpenseApprovalModel,
)
from src.model.inventory import RawMaterialSupplierModel
from src.model.project import ProjectModel
from src.model.user import UserModel
from src.schema.expense import (
    ExpenseCreate,
    ExpenseOut,
    ExpenseProjectOut,
    ExpenseStatusOut,
    ExpenseStatusUpdate,
    ExpenseSupplierItemOut,
    ExpenseUserOut,
    PaginatedExpenses,
)
from src.schema.user import CurrentUser
from src.utils.audit import record_audit
from src.utils.permissions import can_access_project
from src.utils.roles import ADMIN_ROLE, APPROVER_ROLE, COORDINATOR_ROLE

logger = logging.getLogger(__name__)

STATUS_DRAFT = "draft"
STATUS_PENDING = "pending"
STATUS_APPROVED = "approved"
STATUS_REJECTED = "rejected"
STATUS_EXECUTED = "executed"

STATUS_CODES: frozenset[str] = frozenset(
    {
        STATUS_DRAFT,
        STATUS_PENDING,
        STATUS_APPROVED,
        STATUS_REJECTED,
        STATUS_EXECUTED,
    }
)

# Ejecutado/Rechazado son terminales: no hay vuelta a borrador.
_TRANSITIONS: dict[str, frozenset[str]] = {
    STATUS_DRAFT: frozenset({STATUS_PENDING}),
    STATUS_PENDING: frozenset({STATUS_APPROVED, STATUS_REJECTED}),
    STATUS_APPROVED: frozenset({STATUS_EXECUTED}),
    STATUS_REJECTED: frozenset(),
    STATUS_EXECUTED: frozenset(),
}

MANAGER_ROLES: frozenset[str] = frozenset({ADMIN_ROLE, APPROVER_ROLE})
CREATOR_ROLES: frozenset[str] = frozenset({ADMIN_ROLE, COORDINATOR_ROLE})


def can_transition_expense(current_code: str | None, target_code: str | None) -> bool:
    """True si ``current -> target`` es una transicion permitida (RN-7)."""
    if current_code not in _TRANSITIONS or target_code not in STATUS_CODES:
        return False
    return target_code in _TRANSITIONS[current_code]


def can_create_expense(role: str | None) -> bool:
    """Solo Administrador y Coordinador crean solicitudes."""
    return role in CREATOR_ROLES


def can_send_to_pending(
    role: str | None, actor_id: uuid.UUID, requester_id: uuid.UUID
) -> bool:
    """El solicitante o el Administrador envian un borrador a aprobacion."""
    return role == ADMIN_ROLE or actor_id == requester_id


def can_review_expense(
    role: str | None, actor_id: uuid.UUID, requester_id: uuid.UUID
) -> bool:
    """Aprobar/rechazar: admin siempre; aprobador nunca su propia solicitud."""
    if role not in MANAGER_ROLES:
        return False
    if role == APPROVER_ROLE and actor_id == requester_id:
        return False
    return True


def can_execute_expense(role: str | None) -> bool:
    """Marcar como ejecutado: Administrador o Aprobador."""
    return role in MANAGER_ROLES


def _pagination(limit: int, offset: int) -> None:
    if limit < 1 or limit > 200 or offset < 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Paginación inválida",
        )


def _like_pattern(term: str) -> str:
    escaped = (
        term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    )
    return f"%{escaped}%"


def _full_name(user: UserModel | None) -> str:
    if user is None:
        return "Sin solicitante"
    parts = [part for part in (user.name, user.lastname) if part]
    return " ".join(parts) or user.email or "Sin solicitante"


def _expense_query():
    return (
        select(ExpenseApprovalModel)
        .join(ProjectModel, ProjectModel.id == ExpenseApprovalModel.id_project)
        .join(UserModel, UserModel.id == ExpenseApprovalModel.id_user)
        .options(
            joinedload(ExpenseApprovalModel.project),
            joinedload(ExpenseApprovalModel.status),
            joinedload(ExpenseApprovalModel.requester),
            joinedload(
                ExpenseApprovalModel.raw_material_supplier
            ).joinedload(RawMaterialSupplierModel.raw_material),
            joinedload(
                ExpenseApprovalModel.raw_material_supplier
            ).joinedload(RawMaterialSupplierModel.vendor),
        )
    )


def _expense_out(expense: ExpenseApprovalModel) -> ExpenseOut:
    supplier_item = None
    supplier = expense.raw_material_supplier
    if supplier is not None and supplier.raw_material is not None:
        supplier_item = ExpenseSupplierItemOut(
            id=supplier.id,
            resource_name=supplier.raw_material.name,
            vendor_name=(
                supplier.vendor.name if supplier.vendor else "Sin proveedor"
            ),
            unit=supplier.raw_material.unit,
        )

    if expense.status is None:  # pragma: no cover - FK NOT NULL
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Error"
        )
    if expense.project is None:  # pragma: no cover - FK NOT NULL
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Error"
        )

    return ExpenseOut(
        id=expense.id,
        project=ExpenseProjectOut(
            id=expense.project.id,
            unique_code=expense.project.unique_code,
            name=expense.project.name,
        ),
        supplier_item=supplier_item,
        status=ExpenseStatusOut.model_validate(expense.status),
        requester=ExpenseUserOut(
            id=expense.requester.id,
            full_name=_full_name(expense.requester),
        ),
        description=expense.description,
        count_raw_materials=expense.count_raw_materials,
        created_at=expense.created_at,
        modifiet_at=expense.modifiet_at,
    )


def expense_snapshot(expense: ExpenseApprovalModel) -> dict:
    """Estado auditable sin PII: ids, codigo de estado y montos."""
    return {
        "id": str(expense.id),
        "project_id": str(expense.id_project),
        "supplier_item_id": (
            str(expense.id_raw_materials_suppliers)
            if expense.id_raw_materials_suppliers
            else None
        ),
        "status_id": expense.id_status,
        "status": expense.status.code if expense.status else None,
        "requester_id": str(expense.id_user),
        "description": expense.description,
        "count_raw_materials": expense.count_raw_materials,
        "created_at": expense.created_at,
        "modifiet_at": expense.modifiet_at,
    }


async def _status_by_code(
    db: AsyncSession, code: str
) -> StatusExpenseApprovalModel:
    catalog = await db.scalar(
        select(StatusExpenseApprovalModel).where(
            StatusExpenseApprovalModel.code == code
        )
    )
    if catalog is None:  # pragma: no cover - catalogo incompleto
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Error"
        )
    return catalog


async def _get_expense(
    db: AsyncSession, expense_id: uuid.UUID, *, lock: bool = False
) -> ExpenseApprovalModel:
    statement = _expense_query().where(ExpenseApprovalModel.id == expense_id)
    if lock:
        # Serializa transiciones concurrentes de la misma solicitud.
        statement = statement.with_for_update(of=ExpenseApprovalModel)
    expense = (await db.execute(statement)).scalar_one_or_none()
    if expense is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Solicitud no encontrada",
        )
    return expense


def _enforce_project_scope(project: ProjectModel, actor: CurrentUser) -> None:
    if not can_access_project(actor.rol, actor.id, project.id_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="No autorizado"
        )


async def list_expenses(
    db: AsyncSession,
    actor: CurrentUser,
    *,
    limit: int = 50,
    offset: int = 0,
    search: str | None = None,
    status_code: str | None = None,
    project_id: uuid.UUID | None = None,
) -> PaginatedExpenses:
    """Lista solicitudes con alcance por rol y filtros."""
    _pagination(limit, offset)

    if status_code is not None:
        normalized = status_code.strip().lower()
        if normalized not in STATUS_CODES:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Estado inválido",
            )
    else:
        normalized = None

    conditions = []
    if actor.rol == COORDINATOR_ROLE:
        conditions.append(ProjectModel.id_user == actor.id)

    if project_id is not None:
        if actor.rol == COORDINATOR_ROLE:
            owner_id = await db.scalar(
                select(ProjectModel.id_user).where(
                    ProjectModel.id == project_id
                )
            )
            if owner_id is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Proyecto no encontrado",
                )
            if owner_id != actor.id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="No autorizado",
                )
        conditions.append(ExpenseApprovalModel.id_project == project_id)

    if normalized is not None:
        conditions.append(
            ExpenseApprovalModel.status.has(
                StatusExpenseApprovalModel.code == normalized
            )
        )

    if search:
        pattern = _like_pattern(search.strip())
        conditions.append(
            or_(
                ExpenseApprovalModel.description.ilike(pattern, escape="\\"),
                ProjectModel.unique_code.ilike(pattern, escape="\\"),
                ProjectModel.name.ilike(pattern, escape="\\"),
                UserModel.name.ilike(pattern, escape="\\"),
                UserModel.lastname.ilike(pattern, escape="\\"),
            )
        )

    count_stmt = (
        select(func.count())
        .select_from(ExpenseApprovalModel)
        .join(
            ProjectModel, ProjectModel.id == ExpenseApprovalModel.id_project
        )
        .join(UserModel, UserModel.id == ExpenseApprovalModel.id_user)
    )
    statement = _expense_query()
    if conditions:
        count_stmt = count_stmt.where(*conditions)
        statement = statement.where(*conditions)

    total = int(await db.scalar(count_stmt) or 0)
    rows = (
        await db.execute(
            statement.order_by(
                ExpenseApprovalModel.created_at.desc(),
                ExpenseApprovalModel.id.desc(),
            )
            .limit(limit)
            .offset(offset)
        )
    ).scalars().all()

    return PaginatedExpenses(
        items=[_expense_out(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


async def get_expense(
    db: AsyncSession, expense_id: uuid.UUID, actor: CurrentUser
) -> ExpenseOut:
    expense = await _get_expense(db, expense_id)
    _enforce_project_scope(expense.project, actor)
    return _expense_out(expense)


async def create_expense(
    db: AsyncSession,
    payload: ExpenseCreate,
    actor: CurrentUser,
    request: Request | None = None,
) -> ExpenseOut:
    """Crea la solicitud en Borrador; el solicitante es el usuario del token."""
    if not can_create_expense(actor.rol):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="No autorizado"
        )

    project = await db.get(ProjectModel, payload.id_project)
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Proyecto no encontrado",
        )
    _enforce_project_scope(project, actor)

    if payload.id_raw_materials_suppliers is not None:
        supplier = await db.get(
            RawMaterialSupplierModel, payload.id_raw_materials_suppliers
        )
        if supplier is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Material no válido",
            )

    draft = await _status_by_code(db, STATUS_DRAFT)
    expense = ExpenseApprovalModel(
        id_project=payload.id_project,
        id_raw_materials_suppliers=payload.id_raw_materials_suppliers,
        id_status=draft.id,
        id_user=actor.id,
        description=payload.description,
        count_raw_materials=payload.count_raw_materials,
    )
    db.add(expense)

    try:
        await db.flush()
        await record_audit(
            db,
            entity="Expense",
            action="create",
            entity_id=expense.id,
            user_id=actor.id,
            request=request,
            status_code=status.HTTP_201_CREATED,
            after=expense_snapshot(expense),
        )
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        logger.error(
            "Solicitud de gasto rechazada por la BD (%s)",
            getattr(getattr(exc, "orig", None), "sqlstate", "unknown"),
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Proyecto o material no válido",
        ) from exc
    except Exception as exc:  # pragma: no cover - depende de la BD
        await db.rollback()
        logger.error(
            "Error creando solicitud de gasto (%s)", type(exc).__name__
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Error"
        ) from exc

    return _expense_out(await _get_expense(db, expense.id))


async def update_expense_status(
    db: AsyncSession,
    expense_id: uuid.UUID,
    payload: ExpenseStatusUpdate,
    actor: CurrentUser,
    request: Request | None = None,
) -> ExpenseOut:
    """Aplica una transicion valida con permisos y auditoria before/after."""
    expense = await _get_expense(db, expense_id, lock=True)
    _enforce_project_scope(expense.project, actor)

    target = payload.status
    current = expense.status.code if expense.status else None

    if target == STATUS_PENDING:
        if not can_send_to_pending(actor.rol, actor.id, expense.id_user):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Solo el solicitante o un administrador pueden enviarla",
            )
    elif target in (STATUS_APPROVED, STATUS_REJECTED):
        if not can_review_expense(actor.rol, actor.id, expense.id_user):
            if actor.rol == APPROVER_ROLE and actor.id == expense.id_user:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="No puedes aprobar o rechazar tu propia solicitud",
                )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No autorizado",
            )
    elif target == STATUS_EXECUTED:
        if not can_execute_expense(actor.rol):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No autorizado",
            )

    if not can_transition_expense(current, target):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Transición de estado no permitida",
        )

    new_status = await _status_by_code(db, target)
    before = expense_snapshot(expense)

    expense.id_status = new_status.id
    expense.status = new_status
    expense.modifiet_at = datetime.now(timezone.utc)

    await record_audit(
        db,
        entity="Expense",
        action="update_status",
        entity_id=expense.id,
        user_id=actor.id,
        request=request,
        status_code=status.HTTP_200_OK,
        before=before,
        after=expense_snapshot(expense),
    )

    try:
        await db.commit()
    except Exception as exc:  # pragma: no cover - depende de la BD
        await db.rollback()
        logger.error(
            "Error cambiando estado de solicitud (%s)", type(exc).__name__
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Error"
        ) from exc

    return _expense_out(await _get_expense(db, expense.id))
