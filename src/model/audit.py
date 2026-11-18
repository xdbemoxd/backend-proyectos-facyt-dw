from sqlalchemy import Column, String, DateTime, ForeignKey, CheckConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from uuid6 import uuid7

from src.config.db import Base
# Se importa para que "UserModel" quede registrado antes de resolver la relacion.
from src.model.user import UserModel  # noqa: F401


class AuditLogModel(Base):
    """Log inmutable de acciones del sistema (INSERT-only).
    `outcome`: 'success', 'denied' o 'failed'."""
    __tablename__ = "audit_log"
    __table_args__ = (
        CheckConstraint(
            "outcome IN ('success', 'denied', 'failed')",
            name="ck_audit_outcome",
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    timestamp = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    # SET NULL: si se borra el usuario, el registro de auditoria se conserva.
    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("private.user.id", ondelete="SET NULL"),
        nullable=True,
    )
    ip_address = Column(String(255), nullable=False)
    entity = Column(String(255), nullable=False)      # p. ej. "Project"
    action = Column(String(255), nullable=False)      # p. ej. "update_priority"
    entity_id = Column(String(100), nullable=True)
    outcome = Column(String(20), nullable=False, default="success", server_default="success")
    before_str = Column(String(5000), nullable=True)  # estado anterior (JSON serializado)
    after_str = Column(String(5000), nullable=True)   # estado nuevo (JSON serializado)

    user = relationship("UserModel")
