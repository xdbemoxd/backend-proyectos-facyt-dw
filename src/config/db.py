import os
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

engine_kwargs = {
    "echo": False,
    "pool_pre_ping": True,
    "connect_args": connect_args,
}

# Con el pooler de Supabase (PgBouncer en modo transaccion) no conviene que
# SQLAlchemy retenga conexiones: cada conexion queda atada a un event loop.
if "pooler.supabase.com" in SQLALCHEMY_DATABASE_URL or ":6543" in SQLALCHEMY_DATABASE_URL:
    engine_kwargs["poolclass"] = NullPool

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

