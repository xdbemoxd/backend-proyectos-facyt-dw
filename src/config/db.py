import os
from uuid import uuid4
from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base
from sqlalchemy.pool import NullPool
from dotenv import load_dotenv

load_dotenv();

#my data base 
SQLALCHEMY_DATABASE_URL = os.getenv( "DATABASE_URL" );

if not SQLALCHEMY_DATABASE_URL:
    raise RuntimeError("La variable de entorno DATABASE_URL no esta configurada")

# Supabase/PgBouncer en modo transaccion (puerto 6543) no soporta prepared
# statements de asyncpg. Desactivar el cache evita DuplicatePreparedStatement.
connect_args = {}
if SQLALCHEMY_DATABASE_URL.startswith("postgresql+asyncpg"):
    connect_args["statement_cache_size"] = 0
    # SQLAlchemy tambien prepara sentencias con nombre: sin cache y con
    # nombres unicos para que dos conexiones del pooler no choquen.
    connect_args["prepared_statement_cache_size"] = 0
    connect_args["prepared_statement_name_func"] = lambda: f"__asyncpg_{uuid4()}__"

engine_kwargs = {
    "echo": False,
    "pool_pre_ping": True,
    "connect_args": connect_args,
}

# Las pruebas usan NullPool para no atar conexiones a un event loop que se
# cierra entre tests. La aplicacion reutiliza conexiones (pool) para evitar
# el costo de abrir una conexion nueva en cada request contra Supabase.
_testing = bool(os.getenv("TEST_DATABASE_URL")) or any(
    os.getenv(name, "").strip().lower() in ("1", "true", "yes")
    for name in (
        "INVENTORY_LIVE_TESTS",
        "EQUIPMENT_LIVE_TESTS",
        "FINANCE_LIVE_TESTS",
    )
)
if _testing:
    engine_kwargs["poolclass"] = NullPool
else:
    engine_kwargs.update(
        pool_size=5,
        max_overflow=5,
        pool_recycle=1800,
    )

engine = create_async_engine(SQLALCHEMY_DATABASE_URL, **engine_kwargs)


#crete query engine
AsyncSessionLocal = async_sessionmaker(
    bind=engine, 
    class_=AsyncSession, 
    expire_on_commit=False
)


schema_database = os.getenv( 'SCHEMA' )

metadata_local = MetaData( schema = schema_database )

Base = declarative_base( metadata = metadata_local )


# Función adaptada a asíncrona
async def get_db():
    db = AsyncSessionLocal()
    try:
        yield db
    finally:
        await db.close()

