from sqlalchemy import Column, String, SmallInteger, Numeric, DateTime, ForeignKey, CheckConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from uuid6 import uuid7

from src.config.db import Base
# Se importa para que "UserModel" quede registrado antes de resolver la relacion.
from src.model.user import UserModel  # noqa: F401


class StatusProjectModel(Base):
    """Estatus operativo: Propuesta, En ejecucion, Suspendido, Finalizado."""
    __tablename__ = "status_project"

    id = Column(SmallInteger, primary_key=True, autoincrement=True)  # SMALLSERIAL
    name = Column(String(50), nullable=False)
    description = Column(String(255), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    projects = relationship("ProjectModel", back_populates="status")


class PriorityProjectModel(Base):
    """Nivel de prioridad institucional: Critica, Alta, Media, Baja."""
    __tablename__ = "priority_project"

    id = Column(SmallInteger, primary_key=True, autoincrement=True)  # SMALLSERIAL
    name = Column(String(50), nullable=False)
    description = Column(String(255), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    projects = relationship("ProjectModel", back_populates="priority")


class ProjectModel(Base):
    # La tabla se creo con comillas ("Project"); SQLAlchemy la cita sola por la mayuscula.
    __tablename__ = "Project"
    __table_args__ = (
        CheckConstraint("progress BETWEEN 0 AND 100", name="ck_project_progress"),
        CheckConstraint("budget_execution BETWEEN 0 AND 100", name="ck_project_budget_execution"),
    )

    # UUIDv7 generado en Python (igual que UserModel); la BD tiene uuid_generate_v7() de respaldo.
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    unique_code = Column(String(50), nullable=False, unique=True)
    name = Column(String(150), nullable=False)
    description = Column(String(255), nullable=True)

    id_status = Column(
        SmallInteger,
        ForeignKey("private.status_project.id", ondelete="RESTRICT"),
        nullable=False,
    )
    id_priority = Column(
        SmallInteger,
        ForeignKey("private.priority_project.id", ondelete="RESTRICT"),
        nullable=False,
    )

    progress = Column(Numeric(5, 2), nullable=False, default=0, server_default="0.00")
    budget_execution = Column(Numeric(5, 2), nullable=False, default=0, server_default="0.00")

    # Coordinador responsable del proyecto
    id_user = Column(
        UUID(as_uuid=True),
        ForeignKey("private.user.id", ondelete="RESTRICT"),
        nullable=False,
    )

    create_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    status = relationship("StatusProjectModel", back_populates="projects")
    priority = relationship("PriorityProjectModel", back_populates="projects")
    user = relationship("UserModel")
    audits = relationship("AudithProjectModel", back_populates="project")


class AudithProjectModel(Base):
    """Historial de cambios de estatus/prioridad de un proyecto con su justificacion.
    Es INSERT-only: no se edita ni se borra."""
    __tablename__ = "audith_project"
    __table_args__ = (
        CheckConstraint(
            "char_length(trim(explaining)) >= 5",
            name="ck_audith_project_explaining",
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    id_project = Column(
        UUID(as_uuid=True),
        ForeignKey('private."Project".id', ondelete="RESTRICT"),
        nullable=False,
    )
    unique_code = Column(String(50), nullable=False)
    id_status = Column(
        SmallInteger,
        ForeignKey("private.status_project.id", ondelete="RESTRICT"),
        nullable=False,
    )
    id_priority = Column(
        SmallInteger,
        ForeignKey("private.priority_project.id", ondelete="RESTRICT"),
        nullable=False,
    )
    # Usuario que hizo el cambio
    id_user = Column(
        UUID(as_uuid=True),
        ForeignKey("private.user.id", ondelete="RESTRICT"),
        nullable=False,
    )
    explaining = Column(String(2000), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    project = relationship("ProjectModel", back_populates="audits")
    status = relationship("StatusProjectModel")
    priority = relationship("PriorityProjectModel")
    user = relationship("UserModel")
