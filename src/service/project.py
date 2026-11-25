"""Servicio de proyectos, edicion y priorizacion (RF-2, RF-3, RN-2, RN-3).

Decisiones de alcance y permisos (documentadas tambien en el handoff):

- Administrador: ve y edita cualquier proyecto; puede priorizar.
- Aprobador / Gestor de Inventario: ve todos los proyectos y **solo** puede
  cambiar la prioridad (con justificacion). No edita datos generales.
- Coordinador de Proyecto: ve y edita **solo** los proyectos donde
  ``Project.id_user`` es su propio id. No puede cambiar prioridades.

La prioridad se persiste en ``"Project"`` y en ``audith_project`` dentro de la
misma transaccion; ademas se deja un evento en ``audit_log`` (RN-3, RN-8).
La edicion general usa bloqueo optimista con ``expected_updated_at``.
"""

from __future__ import annotations

import logging
import unicodedata
import uuid
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import HTTPException, Request, status
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased, joinedload

from src.model.user import RolModel, UserModel
from src.model.project import (
    AudithProjectModel,
    PriorityProjectModel,
    ProjectModel,
    StatusProjectModel,
)
from src.schema.project import (
    CoordinatorOut,
    ProjectCreate,
    PaginatedPriorityLog,
    PaginatedProjects,
    PriorityChangeIn,
    PriorityChangeOut,
    PriorityChangeProjectOut,
    PriorityChangeUserOut,
    PriorityProjectOut,
    ProjectCatalogs,
    ProjectOut,
    ProjectUpdate,
    ProjectUserOut,
    StatusProjectOut,
)
from src.schema.user import CurrentUser
from src.utils.audit import record_audit
from src.utils.permissions import can_access_project
from src.utils.roles import (
    ADMIN_ROLE,
    APPROVER_ROLE,
    COORDINATOR_ROLE,
)

logger = logging.getLogger(__name__)

# Nombres canonicos del catalogo (coinciden con db/seeds/01_catalogs.sql).
STATUS_PROPOSAL = "Propuesta"
STATUS_IN_PROGRESS = "En ejecución"
STATUS_SUSPENDED = "Suspendido"
STATUS_FINISHED = "Finalizado"

# Maquina de estados de proyecto. Finalizado es terminal: no vuelve a abrirse
# ni se edita (RN-7 se aplica a solicitudes; esta es la regla equivalente para
# proyectos). El mismo estado siempre es una transicion valida (sin cambio).
_STATUS_TRANSITIONS: dict[str, frozenset[str]] = {
    STATUS_PROPOSAL: frozenset({STATUS_IN_PROGRESS, STATUS_SUSPENDED}),
    STATUS_IN_PROGRESS: frozenset({STATUS_SUSPENDED, STATUS_FINISHED}),
    STATUS_SUSPENDED: frozenset({STATUS_IN_PROGRESS, STATUS_FINISHED}),
    STATUS_FINISHED: frozenset(),
}

_STATUS_ALIASES = {
    "propuesta": STATUS_PROPOSAL,
    "en ejecucion": STATUS_IN_PROGRESS,
    "suspendido": STATUS_SUSPENDED,
    "finalizado": STATUS_FINISHED,
}


def _fold(value: str | None) -> str:
    """Normaliza un nombre para comparar sin acentos ni mayusculas."""
    if not value:
        return ""
    decomposed = unicodedata.normalize("NFD", value)
    without_accents = "".join(
        char for char in decomposed if unicodedata.category(char) != "Mn"
    )
    return " ".join(without_accents.casefold().split())


def canonical_status_name(name: str | None) -> str | None:
    """Devuelve el nombre canonico del estado o ``None`` si no se reconoce."""
    return _STATUS_ALIASES.get(_fold(name))


def can_transition_status(current_name: str | None, target_name: str | None) -> bool:
    """True si ``current_name -> target_name`` es una transicion permitida."""
    current = canonical_status_name(current_name)
    target = canonical_status_name(target_name)
    if current is None or target is None:
        return False
    if current == target:
        return True
    return target in _STATUS_TRANSITIONS[current]


def is_project_locked(project: ProjectModel) -> bool:
    """Un proyecto finalizado queda cerrado para edicion."""
    return canonical_status_name(
        project.status.name if project.status else None
    ) == STATUS_FINISHED


def can_edit_project(role: str | None) -> bool:
    """Administrador y coordinador editan datos generales; aprobador no."""
    return role in (ADMIN_ROLE, COORDINATOR_ROLE)


def can_change_priority(role: str | None) -> bool:
    """Solo administrador y aprobador cambian prioridad (RN-3)."""
    return role in (ADMIN_ROLE, APPROVER_ROLE)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def is_stale_update(expected: datetime, current: datetime) -> bool:
    """True si el cliente edito sobre una version distinta a la actual."""
    return _as_utc(expected) != _as_utc(current)


def _like_pattern(term: str) -> str:
    """Escapa comodines de LIKE; la consulta sigue siendo parametrizada."""
    escaped = (
        term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    )
    return f"%{escaped}%"


def _full_name(user) -> str:
    if user is None:
        return "Sin responsable"
    parts = [part for part in (user.name, user.lastname) if part]
    return " ".join(parts) or user.email or "Sin responsable"


def _status_out(status: StatusProjectModel | None) -> StatusProjectOut:
    if status is None:  # pragma: no cover - la FK es NOT NULL
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error",
        )
    return StatusProjectOut.model_validate(status)


def _priority_out(priority: PriorityProjectModel | None) -> PriorityProjectOut:
    if priority is None:  # pragma: no cover - la FK es NOT NULL
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error",
        )
    return PriorityProjectOut.model_validate(priority)


def _project_out(project: ProjectModel) -> ProjectOut:
    return ProjectOut(
        id=project.id,
        unique_code=project.unique_code,
        name=project.name,
        description=project.description,
        status=_status_out(project.status),
        priority=_priority_out(project.priority),
        progress=float(project.progress or 0),
        budget_execution=float(project.budget_execution or 0),
        user=ProjectUserOut(
            id=project.user.id,
            full_name=_full_name(project.user),
            email=project.user.email,
        ),
        create_at=project.create_at,
        updated_at=project.updated_at,
    )


def project_snapshot(project: ProjectModel) -> dict:
    """Estado auditables sin PII: ids, catalogos y porcentajes."""
    return {
        "id": str(project.id),
        "unique_code": project.unique_code,
        "name": project.name,
        "id_status": project.id_status,
        "status": project.status.name if project.status else None,
        "id_priority": project.id_priority,
        "priority": project.priority.name if project.priority else None,
        "progress": project.progress,
        "budget_execution": project.budget_execution,
        "id_user": str(project.id_user),
    }


def _project_query():
    return select(ProjectModel).options(
        joinedload(ProjectModel.status),
        joinedload(ProjectModel.priority),
        joinedload(ProjectModel.user),
    )


async def _get_project(
    db: AsyncSession, project_id: uuid.UUID, *, lock: bool = False
) -> ProjectModel:
    statement = _project_query().where(ProjectModel.id == project_id)
    if lock:
        # Serializa ediciones/priorizaciones concurrentes del mismo proyecto.
        # ``of`` evita bloquear las tablas de catalogo del joinedload.
        statement = statement.with_for_update(of=ProjectModel)
    result = await db.execute(statement)
    project = result.scalar_one_or_none()
    if project is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Proyecto no encontrado",
        )
    return project


def _enforce_scope(project: ProjectModel, actor: CurrentUser) -> None:
    if not can_access_project(actor.rol, actor.id, project.id_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No autorizado",
        )


async def _get_project_for_actor(
    db: AsyncSession,
    project_id: uuid.UUID,
    actor: CurrentUser,
    *,
    lock: bool = False,
) -> ProjectModel:
    project = await _get_project(db, project_id, lock=lock)
    _enforce_scope(project, actor)
    return project


def _pagination(limit: int, offset: int) -> None:
    if limit < 1 or limit > 200 or offset < 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Paginación inválida",
        )


async def list_catalogs(db: AsyncSession) -> ProjectCatalogs:
    statuses = (
        await db.execute(select(StatusProjectModel).order_by(StatusProjectModel.id))
    ).scalars().all()
    priorities = (
        await db.execute(
            select(PriorityProjectModel).order_by(PriorityProjectModel.id)
        )
    ).scalars().all()
    return ProjectCatalogs(
        statuses=[StatusProjectOut.model_validate(item) for item in statuses],
        priorities=[
            PriorityProjectOut.model_validate(item) for item in priorities
        ],
    )


async def list_projects(
    db: AsyncSession,
    actor: CurrentUser,
    *,
    limit: int = 50,
    offset: int = 0,
    search: str | None = None,
    id_status: int | None = None,
    id_priority: int | None = None,
    user_id: uuid.UUID | None = None,
) -> PaginatedProjects:
    """Lista proyectos con alcance por rol, busqueda y filtros."""
    _pagination(limit, offset)

    conditions = []
    if actor.rol == COORDINATOR_ROLE:
        # Un coordinador no puede escapar de su alcance con ?user_id=otro.
        if user_id is not None and user_id != actor.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No autorizado",
            )
        conditions.append(ProjectModel.id_user == actor.id)
    elif user_id is not None:
        conditions.append(ProjectModel.id_user == user_id)

    if search:
        pattern = _like_pattern(search.strip())
        conditions.append(
            or_(
                ProjectModel.name.ilike(pattern, escape="\\"),
                ProjectModel.unique_code.ilike(pattern, escape="\\"),
            )
        )
    if id_status is not None:
        conditions.append(ProjectModel.id_status == id_status)
    if id_priority is not None:
        conditions.append(ProjectModel.id_priority == id_priority)

    count_stmt = select(func.count()).select_from(ProjectModel)
    statement = _project_query()
    if conditions:
        count_stmt = count_stmt.where(*conditions)
        statement = statement.where(*conditions)

    total = int(await db.scalar(count_stmt) or 0)
    result = await db.execute(
        statement.order_by(
            ProjectModel.id_priority.asc(),
            ProjectModel.updated_at.desc(),
            ProjectModel.unique_code.asc(),
        )
        .limit(limit)
        .offset(offset)
    )
    projects = result.scalars().all()

    return PaginatedProjects(
        items=[_project_out(project) for project in projects],
        total=total,
        limit=limit,
        offset=offset,
    )


async def get_project(
    db: AsyncSession, project_id: uuid.UUID, actor: CurrentUser
) -> ProjectOut:
    project = await _get_project_for_actor(db, project_id, actor)
    return _project_out(project)


async def update_project(
    db: AsyncSession,
    project_id: uuid.UUID,
    payload: ProjectUpdate,
    actor: CurrentUser,
    request: Request | None = None,
) -> ProjectOut:
    """Edita datos generales con alcance por rol y bloqueo optimista."""
    if not can_edit_project(actor.rol):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No autorizado",
        )

    project = await _get_project_for_actor(db, project_id, actor, lock=True)

    if is_project_locked(project):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Un proyecto finalizado no se puede editar",
        )

    if is_stale_update(payload.expected_updated_at, project.updated_at):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "El proyecto fue modificado por otra persona. "
                "Recarga la página para ver los cambios"
            ),
        )

    updates = payload.model_dump(exclude_unset=True)
    updates.pop("expected_updated_at", None)
    if not updates:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Nada que actualizar",
        )

    # En PATCH, un null explicito en campos no anulables es un payload
    # invalido, no un "no cambiar".
    for field in ("name", "id_status", "progress", "budget_execution"):
        if field in updates and updates[field] is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"El campo {field} no puede ser nulo",
            )

    new_status: StatusProjectModel | None = None
    if "id_status" in updates:
        new_status = await db.get(StatusProjectModel, updates["id_status"])
        if new_status is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Estatus inválido",
            )
        if new_status.id != project.id_status and not can_transition_status(
            project.status.name if project.status else None,
            new_status.name,
        ):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Transición de estado no permitida",
            )

    before = project_snapshot(project)
    changed = False

    if "name" in updates and updates["name"] != project.name:
        project.name = updates["name"]
        changed = True

    if "description" in updates:
        # Descripcion vacia se guarda como NULL, no como cadena vacia.
        description = updates["description"] or None
        if description != project.description:
            project.description = description
            changed = True

    if new_status is not None and new_status.id != project.id_status:
        project.id_status = new_status.id
        project.status = new_status
        changed = True

    if "progress" in updates:
        progress = Decimal(str(updates["progress"]))
        if progress != project.progress:
            project.progress = progress
            changed = True

    if "budget_execution" in updates:
        budget = Decimal(str(updates["budget_execution"]))
        if budget != project.budget_execution:
            project.budget_execution = budget
            changed = True

    if not changed:
        # PATCH idempotente: no se escribe auditoria de un cambio inexistente.
        return _project_out(project)

    project.updated_at = datetime.now(timezone.utc)
    await record_audit(
        db,
        entity="Project",
        action="update_project",
        entity_id=project.id,
        user_id=actor.id,
        request=request,
        status_code=status.HTTP_200_OK,
        before=before,
        after=project_snapshot(project),
    )

    try:
        await db.commit()
    except Exception as exc:  # pragma: no cover - depende de la BD
        await db.rollback()
        logger.error(
            "Error actualizando proyecto (%s)", type(exc).__name__
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error",
        ) from exc

    return _project_out(project)


async def change_priority(
    db: AsyncSession,
    project_id: uuid.UUID,
    payload: PriorityChangeIn,
    actor: CurrentUser,
    request: Request | None = None,
) -> ProjectOut:
    """Cambia la prioridad y registra el historial en la misma transaccion."""
    if not can_change_priority(actor.rol):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No autorizado",
        )

    project = await _get_project_for_actor(db, project_id, actor, lock=True)

    new_priority = await db.get(PriorityProjectModel, payload.id_priority)
    if new_priority is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Prioridad inválida",
        )

    if project.id_priority == new_priority.id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="El proyecto ya tiene esa prioridad",
        )

    before = project_snapshot(project)
    project.id_priority = new_priority.id
    project.priority = new_priority
    project.updated_at = datetime.now(timezone.utc)

    db.add(
        AudithProjectModel(
            id_project=project.id,
            unique_code=project.unique_code,
            id_status=project.id_status,
            id_priority=new_priority.id,
            id_user=actor.id,
            explaining=payload.explaining,
        )
    )

    after = project_snapshot(project)
    await record_audit(
        db,
        entity="Project",
        action="update_priority",
        entity_id=project.id,
        user_id=actor.id,
        request=request,
        status_code=status.HTTP_200_OK,
        before=before,
        after=after,
    )

    try:
        await db.commit()
    except Exception as exc:  # pragma: no cover - depende de la BD
        await db.rollback()
        logger.error(
            "Error cambiando prioridad de proyecto (%s)", type(exc).__name__
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error",
        ) from exc

    return _project_out(project)


async def list_priority_log(
    db: AsyncSession,
    actor: CurrentUser,
    *,
    limit: int = 50,
    offset: int = 0,
    project_id: uuid.UUID | None = None,
) -> PaginatedPriorityLog:
    """Historial de cambios de prioridad con la prioridad anterior resuelta.

    La prioridad anterior se obtiene con ``lag()`` sobre las filas del mismo
    proyecto; no se guarda en ``audith_project`` (append-only con solo el
    estado nuevo). Un coordinador solo ve el historial de sus proyectos.
    """
    _pagination(limit, offset)

    conditions = []
    if actor.rol == COORDINATOR_ROLE:
        conditions.append(ProjectModel.id_user == actor.id)
    if project_id is not None:
        conditions.append(AudithProjectModel.id_project == project_id)

    count_stmt = (
        select(func.count())
        .select_from(AudithProjectModel)
        .join(ProjectModel, ProjectModel.id == AudithProjectModel.id_project)
    )
    if conditions:
        count_stmt = count_stmt.where(*conditions)
    total = int(await db.scalar(count_stmt) or 0)

    lag_priority = func.lag(AudithProjectModel.id_priority).over(
        partition_by=AudithProjectModel.id_project,
        order_by=[AudithProjectModel.created_at, AudithProjectModel.id],
    )
    lag_subquery = select(
        AudithProjectModel.id.label("audit_id"),
        lag_priority.label("from_priority_id"),
    ).subquery()

    previous_priority = aliased(PriorityProjectModel)
    statement = (
        select(AudithProjectModel, previous_priority.name)
        .join(lag_subquery, lag_subquery.c.audit_id == AudithProjectModel.id)
        .outerjoin(
            previous_priority,
            previous_priority.id == lag_subquery.c.from_priority_id,
        )
        .join(ProjectModel, ProjectModel.id == AudithProjectModel.id_project)
        .options(
            joinedload(AudithProjectModel.project),
            joinedload(AudithProjectModel.priority),
            joinedload(AudithProjectModel.user),
        )
    )
    if conditions:
        statement = statement.where(*conditions)

    result = await db.execute(
        statement.order_by(
            AudithProjectModel.created_at.desc(),
            AudithProjectModel.id.desc(),
        )
        .limit(limit)
        .offset(offset)
    )

    items: list[PriorityChangeOut] = []
    for audit, from_priority_name in result.all():
        items.append(
            PriorityChangeOut(
                id=audit.id,
                project=PriorityChangeProjectOut(
                    id=audit.project.id,
                    unique_code=audit.project.unique_code,
                    name=audit.project.name,
                ),
                from_priority=from_priority_name,
                to_priority=(
                    audit.priority.name if audit.priority else "Desconocida"
                ),
                user=PriorityChangeUserOut(
                    id=audit.user.id,
                    full_name=_full_name(audit.user),
                ),
                explaining=audit.explaining,
                created_at=audit.created_at,
            )
        )

    return PaginatedPriorityLog(
        items=items, total=total, limit=limit, offset=offset
    )


# ---------------------------------------------------------------------------
# Alta de proyectos
# ---------------------------------------------------------------------------

PRIORITY_DEFAULT = "media"
INITIAL_PRIORITY_NOTE = "Prioridad inicial asignada al registrar el proyecto."
_CODE_ATTEMPTS = 5


def can_create_project(role: str | None) -> bool:
    """Administrador y coordinador registran proyectos; aprobador no."""
    return role in (ADMIN_ROLE, COORDINATOR_ROLE)


def build_unique_code(area: str, year: int, sequence: int) -> str:
    """``QUI``, 2026, 7 -> ``QUI-2026-007``."""
    return f"{area.upper()}-{year}-{sequence:03d}"


def next_sequence(existing_codes: list[str], prefix: str) -> int:
    """Siguiente correlativo para ``prefix`` (``QUI-2026-``) segun los codigos actuales."""
    highest = 0
    for code in existing_codes:
        tail = code[len(prefix):] if code.startswith(prefix) else ""
        if tail.isdigit():
            highest = max(highest, int(tail))
    return highest + 1


async def list_coordinators(db: AsyncSession) -> list[CoordinatorOut]:
    """Coordinadores activos, para elegir el responsable al crear un proyecto."""
    result = await db.execute(
        select(UserModel)
        .join(RolModel, UserModel.id_rol == RolModel.id)
        .where(RolModel.name == COORDINATOR_ROLE, UserModel.is_active.is_(True))
        .order_by(UserModel.name.asc().nulls_last(), UserModel.email.asc())
    )
    return [
        CoordinatorOut(id=user.id, full_name=_full_name(user), email=user.email)
        for user in result.scalars().all()
    ]


async def _resolve_coordinator(
    db: AsyncSession, payload: ProjectCreate, actor: CurrentUser
) -> UserModel:
    if actor.rol == COORDINATOR_ROLE:
        # Un coordinador no puede registrar proyectos a nombre de otro.
        if payload.id_user is not None and payload.id_user != actor.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="No autorizado",
            )
        target_id = actor.id
    else:
        if payload.id_user is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Elige el coordinador responsable",
            )
        target_id = payload.id_user

    result = await db.execute(
        select(UserModel)
        .options(joinedload(UserModel.rol))
        .where(UserModel.id == target_id)
    )
    user = result.scalar_one_or_none()
    if (
        user is None
        or not bool(user.is_active)
        or user.rol is None
        or user.rol.name != COORDINATOR_ROLE
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="El responsable debe ser un coordinador activo",
        )
    return user


async def _initial_catalogs(
    db: AsyncSession,
) -> tuple[StatusProjectModel, PriorityProjectModel]:
    statuses = (await db.execute(select(StatusProjectModel))).scalars().all()
    initial_status = next(
        (s for s in statuses if canonical_status_name(s.name) == STATUS_PROPOSAL),
        None,
    )
    priorities = (
        await db.execute(select(PriorityProjectModel).order_by(PriorityProjectModel.id))
    ).scalars().all()
    initial_priority = next(
        (p for p in priorities if _fold(p.name) == PRIORITY_DEFAULT),
        priorities[-1] if priorities else None,
    )
    if initial_status is None or initial_priority is None:
        logger.error("Faltan catalogos de proyecto (Propuesta / prioridades)")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Error",
        )
    return initial_status, initial_priority


async def create_project(
    db: AsyncSession,
    payload: ProjectCreate,
    actor: CurrentUser,
    request: Request | None = None,
) -> ProjectOut:
    """Registra un proyecto en ``Propuesta`` con codigo correlativo por area y año."""
    if not can_create_project(actor.rol):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No autorizado",
        )

    area = payload.area_code.upper()
    year = datetime.now(timezone.utc).year
    prefix = f"{area}-{year}-"
    description = payload.description or None

    for attempt in range(_CODE_ATTEMPTS):
        coordinator = await _resolve_coordinator(db, payload, actor)
        initial_status, initial_priority = await _initial_catalogs(db)

        existing = (
            await db.execute(
                select(ProjectModel.unique_code).where(
                    ProjectModel.unique_code.like(f"{prefix}%")
                )
            )
        ).scalars().all()
        unique_code = build_unique_code(area, year, next_sequence(list(existing), prefix))

        project = ProjectModel(
            unique_code=unique_code,
            name=payload.name,
            description=description,
            id_status=initial_status.id,
            id_priority=initial_priority.id,
            progress=Decimal("0"),
            budget_execution=Decimal("0"),
            id_user=coordinator.id,
        )
        project.status = initial_status
        project.priority = initial_priority
        project.user = coordinator
        db.add(project)

        try:
            await db.flush()
            # Primera fila del historial: deja trazada la prioridad inicial.
            db.add(
                AudithProjectModel(
                    id_project=project.id,
                    unique_code=project.unique_code,
                    id_status=project.id_status,
                    id_priority=project.id_priority,
                    id_user=actor.id,
                    explaining=INITIAL_PRIORITY_NOTE,
                )
            )
            await record_audit(
                db,
                entity="Project",
                action="create_project",
                entity_id=project.id,
                user_id=actor.id,
                request=request,
                status_code=status.HTTP_201_CREATED,
                after=project_snapshot(project),
            )
            await db.commit()
            break
        except IntegrityError as exc:
            # Dos altas simultaneas en la misma area pueden tomar el mismo
            # correlativo: el UNIQUE de unique_code decide y se reintenta.
            await db.rollback()
            if "unique_code" in str(getattr(exc, "orig", exc)).lower() and attempt < _CODE_ATTEMPTS - 1:
                continue
            logger.error("Error de integridad creando proyecto (%s)", type(exc).__name__)
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="No se pudo generar un código único. Inténtalo de nuevo",
            ) from exc
        except HTTPException:
            await db.rollback()
            raise
        except Exception as exc:  # pragma: no cover - depende de la BD
            await db.rollback()
            logger.error("Error creando proyecto (%s)", type(exc).__name__)
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Error",
            ) from exc

    # Relee con fechas del servidor y relaciones cargadas.
    result = await db.execute(
        _project_query()
        .where(ProjectModel.id == project.id)
        .execution_options(populate_existing=True)
    )
    return _project_out(result.scalar_one())

