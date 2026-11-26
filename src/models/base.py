"""
Base models and database configuration for Research Platform.
"""

from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import DeclarativeMeta

Base: DeclarativeMeta = declarative_base()

# This will be used by all database models
__all__ = ["Base"]
