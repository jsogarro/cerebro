"""
Base models and database configuration for Research Platform.
"""

from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import DeclarativeMeta

# Create base class for SQLAlchemy models
Base: DeclarativeMeta = declarative_base()

# This will be used by all database models
__all__ = ["Base"]
