"""Attune practice product shell — multi-tenant foundation.

Clynotion (capture/notes) remains a tool inside Attune. Practices are first-class
tenants; HFC is practice-hfc, not a special codebase path.
"""

from app.attune.practices import Practice, PracticeStore, practice_store

__all__ = ["Practice", "PracticeStore", "practice_store"]
