from sqlalchemy import CheckConstraint, Column, DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from uuid6 import uuid7

from src.config.db import Base
# Se importa para que "UserModel" quede registrado antes de resolver la relacion.
from src.model.user import UserModel  # noqa: F401


class AuditLogModel(Base):
    """Log inmutable de acciones del sistema (INSERT-only).

    Dos tipos de fila:
    - entity="HTTP": una por request relevante (la escribe el middleware).
    - Eventos de negocio (entity="User", "Project"...): antes/despues de la entidad.
    `outcome`: 'success', 'denied' o 'failed'.
    """
    __tablename__ = "audit_log"
    __table_args__ = (
        CheckConstraint(
            "outcome IN ('success', 'denied', 'failed')",
            name="ck_audit_outcome",
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    timestamp = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    # Correlaciona la fila HTTP con los eventos de negocio de la misma request.
    request_id = Column(UUID(as_uuid=True), nullable=True, index=True)

    # SET NULL: si se borra el usuario, el registro de auditoria se conserva.
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("private.user.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    user_email = Column(String(255), nullable=True)  # siempre NULL: sin PII
    ip_address = Column(String(255), nullable=False)

    # Metadata HTTP
    method = Column(String(10), nullable=True)
    path = Column(String(500), nullable=True)
    status_code = Column(Integer, nullable=True)
    user_agent = Column(String(512), nullable=True)
    request_headers = Column(JSONB, nullable=True)  # saneados
    query_params = Column(JSONB, nullable=True)     # saneados
    duration_ms = Column(Integer, nullable=True)

    # Evento
    entity = Column(String(255), nullable=False)      # p. ej. "Project" o "HTTP"
    action = Column(String(255), nullable=False)      # p. ej. "update_priority"
    entity_id = Column(String(100), nullable=True)
    outcome = Column(String(20), nullable=False, default="success", server_default="success")
    error_code = Column(String(100), nullable=True)
    error_detail = Column(String(1000), nullable=True)

    before_data = Column(JSONB, nullable=True)  # estado anterior
    after_data = Column(JSONB, nullable=True)   # estado nuevo

    user = relationship("UserModel")
