"""Vistas de la BD mapeadas como modelos de SOLO LECTURA.

Usan su propio `MetaData` (mismo esquema que `Base`) para que
`Base.metadata.create_all()` NO intente crearlas como tablas.
Las vistas se crean con su SQL (`CREATE OR REPLACE VIEW ...`).
"""
from sqlalchemy import Column, String, Numeric, Boolean, MetaData
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import declarative_base

from src.config.db import schema_database

ViewBase = declarative_base(metadata=MetaData(schema=schema_database))


class InventoryStockStatusView(ViewBase):
    """private.v_inventory_stock_status: stock por proveedor con su estado."""
    __tablename__ = "v_inventory_stock_status"

    # Una vista no tiene PK; SQLAlchemy necesita una. rms.id es unico por fila.
    supplier_item_id = Column(UUID(as_uuid=True), primary_key=True)
    resource_id = Column(UUID(as_uuid=True), nullable=False)
    resource_name = Column(String(255), nullable=False)
    resource_type = Column(String(255), nullable=False)
    unit = Column(String(30), nullable=False)
    vendor_name = Column(String(150), nullable=False)
    current_stock = Column(Numeric(14, 2), nullable=False)
    minimun_stock = Column(Numeric(14, 2), nullable=False)
    stock_status = Column(String(30), nullable=False)  # Agotado | Alerta Stock Mínimo | Normal
    is_critical = Column(Boolean, nullable=False)
