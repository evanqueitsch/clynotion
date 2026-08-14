"""Clinical · Compliance registry (calendar clocks → due engine)."""

from app.comply.registry import ensure_seeded_clocks, list_seed_catalog
from app.platform.catalog import COMPLIANCE_CLOCKS

# Back-compat alias
SEEDED_CLOCKS = COMPLIANCE_CLOCKS

__all__ = [
    "COMPLIANCE_CLOCKS",
    "SEEDED_CLOCKS",
    "ensure_seeded_clocks",
    "list_seed_catalog",
]
