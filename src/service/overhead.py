"""Servicio de cierre de periodo y prorrateo de overhead (RF-6, RN-6).

Reglas:

- Solo se reparte entre proyectos con estado ``En ejecución``.
- El consumo real sale de ``dispatch_log`` dentro del rango (inclusive).
- Criterio ``proporcional`` exige consumo total > 0; ``equitativo`` no.
- La suma de las asignaciones es **exactamente** el total del periodo. Los
  montos se calculan con ``Decimal`` y el residuo de centavos se ajusta en la
  ultima asignacion (redondeo hacia abajo en las anteriores para no producir
  montos negativos).
- Los periodos cerrados no se editan ni se borran: no hay endpoints de update
  ni delete y un segundo cierre del mismo rango responde ``409``.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import ROUND_DOWN, ROUND_HALF_UP, Decimal, InvalidOperation

from fastapi import HTTPException, Request, status
from sqlalchemy import and_, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload
from uuid6 import uuid7

from src.model.inventory import DispatchLogModel
from src.model.overhead import OverheadAllocationModel, OverheadPeriodModel
from src.model.project import ProjectModel, StatusProjectModel
from src.model.user import UserModel
from src.schema.overhead import (
    OverheadAllocationOut,
    OverheadAllocationPreviewOut,
    OverheadPeriodCreate,
    OverheadPeriodOut,
    OverheadPreviewOut,
    OverheadProjectOut,
    OverheadUserOut,
    PaginatedOverheadPeriods,
)
from src.schema.user import CurrentUser
from src.utils.audit import record_audit

logger = logging.getLogger(__name__)

ACTIVE_PROJECT_STATUS = "En ejecución"
CRITERIA = ("proporcional", "equitativo")
CENT = Decimal("0.01")
SHARE_PRECISION = Decimal("0.000001")
MAX_TOTAL = Decimal("999999999999.99")

# Clave fija para serializar cierres concurrentes (una sola por base).
CLOSE_LOCK_KEY = 746_310_501


@dataclass(frozen=True)
class ConsumptionRow:
    project_id: uuid.UUID
    unique_code: str
    name: str
    consumption: Decimal


@dataclass(frozen=True)
class AllocationRow:
    project_id: uuid.UUID
    unique_code: str
    name: str
    consumption: Decimal
    share: Decimal
    amount: Decimal


def periods_overlap(
    start_a: date, end_a: date, start_b: date, end_b: date
) -> bool:
    """Rangos inclusivos: se solapan si comparten al menos un dia."""
    return start_a <= end_b and end_a >= start_b


def _validate_period(start: date, end: date, total: Decimal) -> Decimal:
    if end < start:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="La fecha de fin no puede ser anterior al inicio",
        )
    if total <= 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="El costo total debe ser mayor que 0",
        )
    if total > MAX_TOTAL:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="El costo total es demasiado grande",
        )
    try:
        quantized = total.quantize(CENT)
    except InvalidOperation as exc:  # pragma: no cover - Decimal invalido
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Costo total inválido",
        ) from exc
    if quantized != total:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="El costo total admite máximo 2 decimales",
        )
    return quantized


def allocate_amounts(
    total: Decimal, criterion: str, rows: list[ConsumptionRow]
) -> list[AllocationRow]:
    """Reparte ``total`` y garantiza que la suma sea exactamente ``total``.

    Lanza ``ValueError`` si no hay proyectos activos o si el criterio
    proporcional no tiene consumo. El ultimo proyecto recibe el residuo.
    """
    if criterion not in CRITERIA:
        raise ValueError("Criterio de reparto inválido")
    if total <= 0:
        raise ValueError("El costo total debe ser mayor que 0")
    if not rows:
        raise ValueError("No hay proyectos activos en el período")

    total = total.quantize(CENT)
    total_consumption = sum(
        (row.consumption for row in rows), Decimal("0.00")
    )
    if criterion == "proporcional" and total_consumption <= 0:
        raise ValueError(
            "No hubo consumo registrado en el período. Usa el criterio equitativo"
        )

    count = len(rows)
    assigned = Decimal("0.00")
    result: list[AllocationRow] = []

    for index, row in enumerate(rows):
        if criterion == "equitativo":
            share = Decimal(1) / Decimal(count)
        else:
            share = row.consumption / total_consumption

        if index == count - 1:
            # Residuo exacto de centavos: la suma siempre cuadra.
            amount = (total - assigned).quantize(CENT)
        else:
            # Hacia abajo para que el residuo final nunca sea negativo.
            amount = (total * share).quantize(CENT, rounding=ROUND_DOWN)

        assigned += amount
        result.append(
            AllocationRow(
                project_id=row.project_id,
                unique_code=row.unique_code,
                name=row.name,
                consumption=row.consumption,
                share=share.quantize(
                    SHARE_PRECISION, rounding=ROUND_HALF_UP
                ),
                amount=amount,
            )
        )

    if sum((row.amount for row in result), Decimal("0.00")) != total:
        # Invariante defensivo: no deberia ocurrir con la logica anterior.
        raise ValueError("No se pudo cuadrar el reparto")  # pragma: no cover

    return result


def _full_name(user: UserModel | None) -> str:
    if user is None:
        return "Sin responsable"
    parts = [part for part in (user.name, user.lastname) if part]
    return " ".join(parts) or user.email or "Sin responsable"


def _period_query():
    return select(OverheadPeriodModel).options(
        selectinload(OverheadPeriodModel.allocations).selectinload(
            OverheadAllocationModel.project
        ),
        joinedload(OverheadPeriodModel.closed_by_user),
    )


def _period_out(period: OverheadPeriodModel) -> OverheadPeriodOut:
    if period.closed_by_user is None:  # pragma: no cover - FK NOT NULL
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Error"
        )
    allocations = sorted(
        period.allocations,
        key=lambda item: (
            item.project.unique_code if item.project else "",
            str(item.id),
        ),
    )
    return OverheadPeriodOut(
        id=period.id,
        start_date=period.start_date,
        end_date=period.end_date,
        criterion=period.criterion,
        total_cost=period.total_cost,
        closed_by=OverheadUserOut(
            id=period.closed_by_user.id,
            full_name=_full_name(period.closed_by_user),
        ),
        closed_at=period.closed_at,
        allocations=[
            OverheadAllocationOut(
                id=allocation.id,
                project=OverheadProjectOut(
                    id=allocation.project.id,
                    unique_code=allocation.project.unique_code,
                    name=allocation.project.name,
                ),
                consumption=allocation.consumption,
                amount=allocation.amount,
            )
            for allocation in allocations
            if allocation.project is not None
        ],
    )


def period_snapshot(period: OverheadPeriodModel) -> dict:
    """Estado auditable del cierre y su reparto."""
    return {
        "id": str(period.id),
        "start_date": period.start_date,
        "end_date": period.end_date,
        "criterion": period.criterion,
        "total_cost": period.total_cost,
        "closed_by": str(period.closed_by),
        "closed_at": period.closed_at,
        "allocations": [
            {
                "project_id": str(allocation.id_project),
                "amount": allocation.amount,
                "consumption": allocation.consumption,
            }
            for allocation in period.allocations
        ],
    }


def _range_bounds(start: date, end: date) -> tuple[datetime, datetime]:
    start_dt = datetime.combine(start, time.min, tzinfo=timezone.utc)
    end_dt = datetime.combine(
        end + timedelta(days=1), time.min, tzinfo=timezone.utc
    )
    return start_dt, end_dt


async def active_consumption(
    db: AsyncSession, start: date, end: date
) -> list[ConsumptionRow]:
    """Proyectos activos y consumo real de despachos en el rango."""
    start_dt, end_dt = _range_bounds(start, end)
    rows = (
        await db.execute(
            select(
                ProjectModel.id,
                ProjectModel.unique_code,
                ProjectModel.name,
                func.coalesce(
                    func.sum(DispatchLogModel.use_raw_materials),
                    Decimal("0.00"),
                ).label("consumption"),
            )
            .join(
                StatusProjectModel,
                StatusProjectModel.id == ProjectModel.id_status,
            )
            .outerjoin(
                DispatchLogModel,
                and_(
                    DispatchLogModel.id_project == ProjectModel.id,
                    DispatchLogModel.material_requested_at >= start_dt,
                    DispatchLogModel.material_requested_at < end_dt,
                ),
            )
            .where(StatusProjectModel.name == ACTIVE_PROJECT_STATUS)
            .group_by(
                ProjectModel.id,
                ProjectModel.unique_code,
                ProjectModel.name,
            )
            .order_by(ProjectModel.unique_code.asc())
        )
    ).all()

    return [
        ConsumptionRow(
            project_id=row.id,
            unique_code=row.unique_code,
            name=row.name,
            consumption=Decimal(row.consumption or 0),
        )
        for row in rows
    ]


async def preview_period(
    db: AsyncSession,
    *,
    start_date: date,
    end_date: date,
    total_cost: Decimal,
    criterion: str,
) -> OverheadPreviewOut:
    """Calcula el reparto sin guardar nada."""
    if criterion not in CRITERIA:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Criterio de reparto inválido",
        )
    total = _validate_period(start_date, end_date, total_cost)
    rows = await active_consumption(db, start_date, end_date)
    try:
        allocations = allocate_amounts(total, criterion, rows)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc

    return OverheadPreviewOut(
        start_date=start_date,
        end_date=end_date,
        criterion=criterion,
        total_cost=total,
        total_consumption=sum(
            (row.consumption for row in rows), Decimal("0.00")
        ),
        allocations=[
            OverheadAllocationPreviewOut(
                project=OverheadProjectOut(
                    id=row.project_id,
                    unique_code=row.unique_code,
                    name=row.name,
                ),
                consumption=row.consumption,
                share=row.share,
                amount=row.amount,
            )
            for row in allocations
        ],
    )


async def list_periods(
    db: AsyncSession,
    *,
    limit: int = 50,
    offset: int = 0,
) -> PaginatedOverheadPeriods:
    if limit < 1 or limit > 200 or offset < 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Paginación inválida",
        )

    total = int(
        await db.scalar(
            select(func.count()).select_from(OverheadPeriodModel)
        )
        or 0
    )
    rows = (
        await db.execute(
            _period_query()
            .order_by(
                OverheadPeriodModel.start_date.desc(),
                OverheadPeriodModel.closed_at.desc(),
            )
            .limit(limit)
            .offset(offset)
        )
    ).scalars().all()

    return PaginatedOverheadPeriods(
        items=[_period_out(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


async def get_period(
    db: AsyncSession, period_id: uuid.UUID
) -> OverheadPeriodOut:
    period = await db.scalar(
        _period_query().where(OverheadPeriodModel.id == period_id)
    )
    if period is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Período no encontrado",
        )
    return _period_out(period)


async def close_period(
    db: AsyncSession,
    payload: OverheadPeriodCreate,
    actor: CurrentUser,
    request: Request | None = None,
) -> OverheadPeriodOut:
    """Cierra el periodo y guarda periodo + reparto en una transaccion."""
    total = _validate_period(
        payload.start_date, payload.end_date, payload.total_cost
    )

    try:
        # Serializa cierres concurrentes aunque la tabla no tenga filas.
        await db.execute(select(func.pg_advisory_xact_lock(CLOSE_LOCK_KEY)))

        clash = await db.scalar(
            select(OverheadPeriodModel.id)
            .where(
                and_(
                    OverheadPeriodModel.start_date <= payload.end_date,
                    OverheadPeriodModel.end_date >= payload.start_date,
                )
            )
            .limit(1)
        )
        if clash is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="El período se solapa con uno ya cerrado",
            )

        rows = await active_consumption(
            db, payload.start_date, payload.end_date
        )
        try:
            allocations = allocate_amounts(
                total, payload.criterion, rows
            )
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=str(exc),
            ) from exc

        period = OverheadPeriodModel(
            id=uuid7(),
            start_date=payload.start_date,
            end_date=payload.end_date,
            criterion=payload.criterion,
            total_cost=total,
            closed_by=actor.id,
            closed_at=datetime.now(timezone.utc),
        )
        # Se asigna antes de adjuntar la fila para no disparar una carga lazy
        # de la coleccion (MissingGreenlet en async).
        period.allocations = [
            OverheadAllocationModel(
                id=uuid7(),
                id_overhead_period=period.id,
                id_project=row.project_id,
                amount=row.amount,
                consumption=row.consumption,
            )
            for row in allocations
        ]
        db.add(period)
        await db.flush()

        await record_audit(
            db,
            entity="OverheadPeriod",
            action="close_period",
            entity_id=period.id,
            user_id=actor.id,
            request=request,
            status_code=status.HTTP_201_CREATED,
            after=period_snapshot(period),
        )

        await db.commit()
    except HTTPException:
        await db.rollback()
        raise
    except IntegrityError as exc:
        await db.rollback()
        logger.error(
            "Cierre de overhead rechazado por la BD (%s)",
            getattr(getattr(exc, "orig", None), "sqlstate", "unknown"),
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="No se pudo cerrar el período",
        ) from exc
    except Exception as exc:  # pragma: no cover - depende de la BD
        await db.rollback()
        logger.error(
            "Error cerrando período de overhead (%s)", type(exc).__name__
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Error"
        ) from exc

    return await get_period(db, period.id)
