import os
from uuid import uuid4
from sqlalchemy.pool import NullPool
from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base
from dotenv import load_dotenv

load_dotenv();

#my data base 
SQLALCHEMY_DATABASE_URL = os.getenv( "DATABASE_URL" );
# Supabase pooler (pgbouncer, modo "transaction", puerto 6543) no soporta
# prepared statements cacheados: se desactiva la cache y se usan nombres
# unicos para que dos conexiones no choquen con "__asyncpg_stmt_1__".
# NullPool: pgbouncer ya hace el pool; evita reutilizar conexiones viejas.
engine = create_async_engine(
    SQLALCHEMY_DATABASE_URL,
    echo = False,
    poolclass = NullPool,
    connect_args = {
        "statement_cache_size": 0,
        "prepared_statement_cache_size": 0,
        "prepared_statement_name_func": lambda: f"__asyncpg_{uuid4()}__",
    },
)


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

