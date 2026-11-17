from sqlalchemy import Column, String, SmallInteger, DateTime
from sqlalchemy.sql import func

from src.config.db import Base


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
