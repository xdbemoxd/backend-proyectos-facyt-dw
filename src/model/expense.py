from sqlalchemy import Column, String, SmallInteger, DateTime, ForeignKey, Numeric, CheckConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from uuid6 import uuid7

from src.config.db import Base
# Para resolver las relaciones con proyecto y stock por proveedor.
from src.model.project import ProjectModel  # noqa: F401
from src.model.inventory import RawMaterialSupplierModel  # noqa: F401


class StatusExpenseApprovalModel(Base):
    """Estados de la maquina de aprobacion de solicitudes de gasto:
    Borrador, Pendiente de Aprobacion, Aprobado, Rechazado, Ejecutado.

    Usa `code` (estable, sin acentos) en la logica del codigo; `name` es para mostrar.
    """
    __tablename__ = "status_expense_approval"

    id = Column(SmallInteger, primary_key=True, autoincrement=True)  # SMALLSERIAL
    name = Column(String(50), nullable=False, unique=True)
    code = Column(String(30), nullable=False, unique=True)
    description = Column(String(255), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    expenses = relationship("ExpenseApprovalModel", back_populates="status")


class ExpenseApprovalModel(Base):
    """Solicitud de gasto de un proyecto, con su estado en la maquina de aprobacion."""
    # En el SQL esta sin comillas (private.Expense_approval), asi que Postgres
    # la guarda en minusculas: expense_approval.
    __tablename__ = "expense_approval"
    __table_args__ = (
        CheckConstraint("count_raw_materials > 0.00", name="ck_expense_amount_positive"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    id_project = Column(
        UUID(as_uuid=True),
        ForeignKey('private."Project".id', ondelete="RESTRICT"),
        nullable=False,
    )
    # Opcional: la solicitud puede no estar ligada a un material concreto.
    id_raw_materials_suppliers = Column(
        UUID(as_uuid=True),
        ForeignKey("private.raw_material_supplier.id", ondelete="RESTRICT"),
        nullable=True,
    )
    id_status = Column(
        SmallInteger,
        ForeignKey("private.status_expense_approval.id", ondelete="RESTRICT"),
        nullable=False,
    )
    description = Column(String(255), nullable=False)
    count_raw_materials = Column(Numeric(14, 2), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    # Nombre de columna igual que en el SQL ("modifiet_at").
    modifiet_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    project = relationship("ProjectModel")
    raw_material_supplier = relationship("RawMaterialSupplierModel")
    status = relationship("StatusExpenseApprovalModel", back_populates="expenses")
