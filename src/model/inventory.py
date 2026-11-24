from sqlalchemy import Column, String, SmallInteger, DateTime, ForeignKey, Numeric, CheckConstraint, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from uuid6 import uuid7
from typing import cast

from src.config.db import Base
# Se importa para que "VendorModel" quede registrado antes de resolver la relacion.
from src.model.vendor import VendorModel  # noqa: F401
# Para resolver las relaciones del despacho con proyecto y usuario.
from src.model.project import ProjectModel  # noqa: F401


class ResourceTypeModel(Base):
    """Tipo de recurso: p. ej. Materia prima, Consumible, Reactivo."""
    __tablename__ = "resource_type"

    id = Column(SmallInteger, primary_key=True, autoincrement=True)  # SMALLSERIAL
    name = Column(String(255), nullable=False)
    description = Column(String(1000), nullable=True)
    create_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    raw_materials = relationship("RawMaterialModel", back_populates="resource_type")


class RawMaterialModel(Base):
    """Catalogo de materia prima y consumibles."""
    __tablename__ = "raw_material"

    # El nombre de la columna en la BD es "id_inventroy" (asi esta en el SQL).
    id_inventroy = Column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    name = Column(String(255), nullable=False)
    description = Column(String(1000), nullable=True)
    resource_type_id = Column(
        SmallInteger,
        ForeignKey("private.resource_type.id", ondelete="RESTRICT"),
        nullable=False,
    )
    unit = Column(String(30), nullable=False, default="unidad", server_default="unidad")
    create_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    resource_type = relationship("ResourceTypeModel", back_populates="raw_materials")
    suppliers = relationship("RawMaterialSupplierModel", back_populates="raw_material")


class RawMaterialSupplierModel(Base):
    """Stock de una materia prima por proveedor (actual y minimo para alertas)."""
    __tablename__ = "raw_material_supplier"
    __table_args__ = (
        CheckConstraint("current_stock >= 0.00", name="ck_raw_supplier_stock_nonneg"),
        CheckConstraint("minimun_stock >= 0.00", name="ck_raw_supplier_min_stock_nonneg"),
        UniqueConstraint("raw_materials_id", "vendors_id", name="uq_raw_material_vendor"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    raw_materials_id = Column(
        UUID(as_uuid=True),
        ForeignKey("private.raw_material.id_inventroy", ondelete="RESTRICT"),
        nullable=False,
    )
    vendors_id = Column(
        UUID(as_uuid=True),
        ForeignKey("private.vendors.id", ondelete="RESTRICT"),
        nullable=False,
    )
    current_stock = Column(Numeric(14, 2), nullable=False, default=0, server_default="0.00")
    minimun_stock = Column(Numeric(14, 2), nullable=False, default=0, server_default="0.00")
    create_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    raw_material = relationship("RawMaterialModel", back_populates="suppliers")
    vendor = relationship("VendorModel")
    lots = relationship("AudithRawMaterialSupplierModel", back_populates="raw_material_supplier")
    dispatches = relationship("DispatchLogModel", back_populates="raw_material_supplier")

    @property
    def is_below_minimum(self) -> bool:
        """True si hay que lanzar la alerta de stock minimo."""
        return cast(bool, self.current_stock <= self.minimun_stock)


class AudithRawMaterialSupplierModel(Base):
    """Lote de compra / entrada de almacen (INSERT-only).
    Cada registro suma `count_raw_materials_in` al `current_stock` del proveedor."""
    __tablename__ = "audith_raw_material_supplier"
    __table_args__ = (
        CheckConstraint("count_raw_materials_in > 0.00", name="ck_lot_quantity_positive"),
        CheckConstraint("unit_cost >= 0.00", name="ck_lot_cost_nonneg"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    id_raw_materials_supplier = Column(
        UUID(as_uuid=True),
        ForeignKey("private.raw_material_supplier.id", ondelete="RESTRICT"),
        nullable=False,
    )
    in_date = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    count_raw_materials_in = Column(Numeric(14, 2), nullable=False)
    unit_cost = Column(Numeric(14, 2), nullable=False, default=0, server_default="0.00")
    invoice_reference = Column(String(100), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    raw_material_supplier = relationship("RawMaterialSupplierModel", back_populates="lots")

    @property
    def total_cost(self):
        """Costo total del lote (cantidad x costo unitario)."""
        return self.count_raw_materials_in * self.unit_cost


class DispatchLogModel(Base):
    """Bitacora de despachos (salidas de almacen) hacia un proyecto. INSERT-only.
    Cada registro resta `use_raw_materials` del `current_stock` del proveedor."""
    # En el SQL esta sin comillas (private.Dispatch_log), asi que Postgres
    # la guarda en minusculas: dispatch_log.
    __tablename__ = "dispatch_log"
    __table_args__ = (
        CheckConstraint("use_raw_materials > 0.00", name="ck_dispatch_quantity_positive"),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    id_raw_materials_suppliers = Column(
        UUID(as_uuid=True),
        ForeignKey("private.raw_material_supplier.id", ondelete="RESTRICT"),
        nullable=False,
    )
    id_project = Column(
        UUID(as_uuid=True),
        ForeignKey("private.Project.id", ondelete="RESTRICT"),
        nullable=False,
    )
    # Usuario responsable del retiro
    id_user = Column(
        UUID(as_uuid=True),
        ForeignKey("private.user.id", ondelete="RESTRICT"),
        nullable=False,
    )
    use_raw_materials = Column(Numeric(14, 2), nullable=False)
    material_requested_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    notes = Column(String(1000), nullable=True)

    raw_material_supplier = relationship("RawMaterialSupplierModel", back_populates="dispatches")
    project = relationship("ProjectModel")
    user = relationship("UserModel")
