from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from uuid6 import uuid7

from src.config.db import Base
# Se importa para que "UserModel" quede registrado antes de resolver la relacion.
from src.model.user import UserModel  # noqa: F401


class AuditLogModel(Base):
    """Bitacora inmutable (append-only) de acciones y requests HTTP.

    - `outcome`: 'success', 'denied' o 'failed'.
    - `before_data` / `after_data`: estado anterior y nuevo en JSON.
    - Los campos HTTP (`method`, `path`, `status_code`, `request_headers`,
      `query_params`, `duration_ms`, `user_agent`) se llenan desde el
      middleware de auditoria.
    - `request_id` correlaciona la request HTTP con eventos de negocio.
    """
    __tablename__ = "audit_log"
    __table_args__ = (
        CheckConstraint(
            "outcome IN ('success', 'denied', 'failed')",
            name="ck_audit_outcome",
        ),
        CheckConstraint(
            "status_code IS NULL OR status_code BETWEEN 100 AND 599",
            name="ck_audit_status",
        ),
        CheckConstraint(
            "method IS NULL OR method IN "
            "('GET','POST','PUT','PATCH','DELETE','HEAD','OPTIONS')",
            name="ck_audit_method",
        ),
        CheckConstraint(
            "duration_ms IS NULL OR duration_ms >= 0",
            name="ck_audit_duration",
        ),
        CheckConstraint(
            "request_headers IS NULL OR pg_column_size(request_headers) <= 32768",
            name="ck_audit_request_headers_size",
        ),
        CheckConstraint(
            "query_params IS NULL OR pg_column_size(query_params) <= 32768",
            name="ck_audit_query_params_size",
        ),
        CheckConstraint(
            "before_data IS NULL OR pg_column_size(before_data) <= 65536",
            name="ck_audit_before_data_size",
        ),
        CheckConstraint(
            "after_data IS NULL OR pg_column_size(after_data) <= 65536",
            name="ck_audit_after_data_size",
        ),
        Index("idx_audit_timestamp", "timestamp"),
        Index("idx_audit_user", "user_id"),
        Index("idx_audit_entity", "entity", "entity_id"),
        Index("idx_audit_status", "status_code"),
        Index("idx_audit_request", "request_id"),
        Index("idx_audit_action", "action"),
        Index("idx_audit_outcome", "outcome"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    timestamp = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # SET NULL no se usa: un borrado de usuario no debe modificar la auditoria.
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("private.user.id", ondelete="RESTRICT"),
        nullable=True,
    )
    # Snapshot del actor para que la auditoria siga siendo legible.
    user_email = Column(String(255), nullable=True)

    ip_address = Column(String(255), nullable=False)

    # Metadata HTTP
    method = Column(String(10), nullable=True)
    path = Column(String(500), nullable=True)
    status_code = Column(SmallInteger, nullable=True)
    user_agent = Column(String(512), nullable=True)
    request_headers = Column(JSONB, nullable=True)
    query_params = Column(JSONB, nullable=True)
    duration_ms = Column(Integer, nullable=True)
    request_id = Column(UUID(as_uuid=True), nullable=True)

    # Evento de negocio
    entity = Column(String(255), nullable=False)      # p. ej. "Project"
    action = Column(String(255), nullable=False)      # p. ej. "update_priority"
    entity_id = Column(String(100), nullable=True)
    outcome = Column(
        String(20), nullable=False, default="success", server_default="success"
    )
    error_code = Column(String(100), nullable=True)
    error_detail = Column(String(1000), nullable=True)
    before_data = Column(JSONB, nullable=True)        # estado anterior
    after_data = Column(JSONB, nullable=True)         # estado nuevo

    user = relationship("UserModel")
