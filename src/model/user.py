from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.sql import func
from sqlalchemy.orm import relationship
from uuid6 import uuid7

# Importas tu clase Base desde tu archivo de configuración
from src.config.db import Base


class RolModel(Base):
    __tablename__ = "rol"

    # Generación de UUIDv7 desde Python
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    name = Column(String(255), nullable=False)
    description = Column(String(1000), nullable=True)

    # Relación bidireccional (Para acceder a los usuarios de este rol fácilmente)
    users = relationship("UserModel", back_populates="rol")


class UserModel(Base):
    __tablename__ = "user"
    
    # IMPORTANTE: Definir explícitamente que la tabla pertenece al esquema 'private'

    # Generación de UUIDv7 desde Python
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid7)
    
    # name y lastname ahora son opcionales (nullable=True, default=None)
    name = Column(String(255), nullable=True, default=None)
    lastname = Column(String(255), nullable=True, default=None)
    
    email = Column(String(255), unique=True, nullable=False, index=True)
    create_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # is_active ahora es False por defecto, tal como en tu SQL
    is_active = Column(Boolean, default=False)

    # Version de token: se incrementa al cerrar sesion o cambiar rol/estado.
    # Un JWT con una version distinta deja de ser valido inmediatamente.
    token_version = Column(
        Integer, nullable=False, default=0, server_default="0"
    )
    
    # Clave foránea apuntando a la tabla rol dentro del esquema private
    id_rol = Column(
        UUID(as_uuid=True), 
        ForeignKey("private.rol.id", ondelete="RESTRICT", onupdate="CASCADE"), 
        nullable=False
    )

    # Relación bidireccional (asumiendo que tu RolModel también está en el esquema private)
    rol = relationship("RolModel", back_populates="users")