"""Strategy library + a small registry the scripts use to look things up."""

from strategies.baselines import FixedSpreadMM, InventorySkewMM, MeanReversionTaker
from strategies.flagship_hybrid import FlagshipHybrid

REGISTRY = {
    "flagship_hybrid": FlagshipHybrid,
    "fixed_spread_mm": FixedSpreadMM,
    "inventory_skew_mm": InventorySkewMM,
    "mean_reversion_taker": MeanReversionTaker,
}


def make(name: str, **params):
    """Instantiate a strategy by name with optional parameter overrides."""
    if name not in REGISTRY:
        raise KeyError(f"unknown strategy {name!r}; have {list(REGISTRY)}")
    return REGISTRY[name](**params)


__all__ = ["REGISTRY", "make", "FixedSpreadMM", "InventorySkewMM",
           "MeanReversionTaker", "FlagshipHybrid"]
