from sqlalchemy import Column, String, Date, DateTime, ForeignKey, Numeric, CheckConstraint, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from uuid6 import uuid7

from src.config.db import Base
# Se importa para que "UserModel" quede registrado antes de resolver la relacion.
from src.model.user import UserModel  # noqa: F401
# Para resolver la relacion del reparto con el proyecto.
from src.model.project import ProjectModel  # noqa: F401


class OverheadPeriodModel(Base):
    """Cierre de un periodo contable para prorratear consumibles compartidos.
    `criterion`: 'proporcional' (segun consumo) o 'equitativo' (partes iguales)."""
    __tablename__ = "overhead_period"
    __table_args__ = (
        CheckConstraint("end_date >= start_date", name="ck_overhead_period_range"),
        CheckConstraint(
            "criterion IN ('proporcional', 'equitativo')",
            name="ck_overhead_criterion",
        ),
        CheckConstraint("total_cost >= 0.00", name="ck_overhead_total_nonneg"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    start_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=False)
    criterion = Column(String(20), nullable=False)
    total_cost = Column(Numeric(14, 2), nullable=False)
    # Usuario que cerro el periodo
    closed_by = Column(
        UUID(as_uuid=True),
        ForeignKey("private.user.id", ondelete="RESTRICT"),
        nullable=False,
    )
    closed_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    closed_by_user = relationship("UserModel")
    allocations = relationship("OverheadAllocationModel", back_populates="period")


class OverheadAllocationModel(Base):
    """Cuanto le toca pagar a cada proyecto en un cierre de periodo.
    La suma de `amount` del periodo debe igualar `OverheadPeriodModel.total_cost`."""
    __tablename__ = "overhead_allocation"
    __table_args__ = (
        CheckConstraint("amount >= 0.00", name="ck_overhead_alloc_amount_nonneg"),
        CheckConstraint("consumption >= 0.00", name="ck_overhead_alloc_consump_nonneg"),
        UniqueConstraint("id_overhead_period", "id_project", name="uq_overhead_period_project"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    id_overhead_period = Column(
        UUID(as_uuid=True),
        ForeignKey("private.overhead_period.id", ondelete="RESTRICT"),
        nullable=False,
    )
    id_project = Column(
        UUID(as_uuid=True),
        ForeignKey("private.Project.id", ondelete="RESTRICT"),
        nullable=False,
    )
    amount = Column(Numeric(14, 2), nullable=False)
    consumption = Column(Numeric(14, 2), nullable=False, default=0, server_default="0.00")

    period = relationship("OverheadPeriodModel", back_populates="allocations")
    project = relationship("ProjectModel")
