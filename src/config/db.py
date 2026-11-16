import os
from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base
from dotenv import load_dotenv

load_dotenv();

#my data base 
SQLALCHEMY_DATABASE_URL = os.getenv( "DATABASE_URL" );
engine = create_async_engine( SQLALCHEMY_DATABASE_URL, echo = False )


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

