from sqlalchemy import Column, String, SmallInteger, DateTime, ForeignKey, CheckConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from uuid6 import uuid7

from src.config.db import Base
# Se importa para que "VendorModel" quede registrado antes de resolver la relacion.
from src.model.vendor import VendorModel  # noqa: F401
# Para resolver las relaciones de la reserva con proyecto y usuario.
from src.model.project import ProjectModel  # noqa: F401


class StatusEquipamentModel(Base):
    """Estado operacional del equipo: Disponible, En uso, En mantenimiento."""
    __tablename__ = "status_equipament"

    id = Column(SmallInteger, primary_key=True, autoincrement=True)  # SMALLSERIAL
    name = Column(String(50), nullable=False)
    description = Column(String(255), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    units = relationship("EquipamentSupplierModel", back_populates="status")


class EquipmentModel(Base):
    """Catalogo de equipos/maquinaria (tipo de equipo, sin unidades fisicas)."""
    __tablename__ = "equipment"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    name = Column(String(150), nullable=False)
    description = Column(String(1000), nullable=True)
    create_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    units = relationship("EquipamentSupplierModel", back_populates="equipment")


class EquipamentSupplierModel(Base):
    """Unidad fisica de un equipo comprada a un proveedor, con su estado operacional."""
    __tablename__ = "equipament_supplier"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    id_equipament = Column(
        UUID(as_uuid=True),
        ForeignKey("private.equipment.id", ondelete="RESTRICT"),
        nullable=False,
    )
    id_vendors = Column(
        UUID(as_uuid=True),
        ForeignKey("private.vendors.id", ondelete="RESTRICT"),
        nullable=False,
    )
    id_status = Column(
        SmallInteger,
        ForeignKey("private.status_equipament.id", ondelete="RESTRICT"),
        nullable=False,
    )
    model = Column(String(100), nullable=True)
    serial_number = Column(String(100), nullable=True)
    create_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    equipment = relationship("EquipmentModel", back_populates="units")
    vendor = relationship("VendorModel")
    status = relationship("StatusEquipamentModel", back_populates="units")
    reservations = relationship("ReservationEquipamentModel", back_populates="equipament_supplier")


class ReservationEquipamentModel(Base):
    """Reserva de una unidad fisica de equipo para un proyecto en un rango de tiempo."""
    __tablename__ = "reservation_equipament"
    __table_args__ = (
        CheckConstraint("end_date > reservation_date", name="ck_reservation_dates"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    id_equipament_supplier = Column(
        UUID(as_uuid=True),
        ForeignKey("private.equipament_supplier.id", ondelete="RESTRICT"),
        nullable=False,
    )
    id_project = Column(
        UUID(as_uuid=True),
        ForeignKey('private."Project".id', ondelete="RESTRICT"),
        nullable=False,
    )
    # Usuario que hizo la reserva
    id_user = Column(
        UUID(as_uuid=True),
        ForeignKey("private.user.id", ondelete="RESTRICT"),
        nullable=False,
    )
    reservation_date = Column(DateTime(timezone=True), nullable=False)  # inicio
    end_date = Column(DateTime(timezone=True), nullable=False)
    purpose = Column(String(2000), nullable=True)
    created_date = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    equipament_supplier = relationship("EquipamentSupplierModel", back_populates="reservations")
    project = relationship("ProjectModel")
    user = relationship("UserModel")
