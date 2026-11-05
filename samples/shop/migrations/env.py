from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine

from shop.db import Base
from shop.settings import settings

if context.config.config_file_name is not None:
    fileConfig(context.config.config_file_name)

with create_engine(settings.database_url).connect() as connection:
    context.configure(connection=connection, target_metadata=Base.metadata)
    with context.begin_transaction():
        context.run_migrations()
