"""Connection-free declarative base shared by runtime and Alembic."""
from sqlalchemy.orm import declarative_base


Base = declarative_base()


__all__ = ["Base"]
