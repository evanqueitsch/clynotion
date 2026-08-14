"""Clynotion platform shell — multi-tenant practice foundation.

Supervision notes capture is a tool inside Clynotion. Practices are first-class
tenants; every session and roster row is scoped by practice_id. The due engine
feeds Home bands (overdue / this week).
"""

from app.platform.due import DueEngine, Obligation, due_engine
from app.platform.practices import Practice, PracticeStore, practice_store

__all__ = [
    "DueEngine",
    "Obligation",
    "due_engine",
    "Practice",
    "PracticeStore",
    "practice_store",
]
