"""Clynotion platform shell — multi-tenant practice foundation.

Supervision notes capture is a tool inside Clynotion. Practices are first-class
tenants; every session and roster row is scoped by practice_id.
"""

from app.platform.practices import Practice, PracticeStore, practice_store

__all__ = ["Practice", "PracticeStore", "practice_store"]
