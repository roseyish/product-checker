"""Standalone product analysis engine: no external AI calls."""
from .analyzer import analyze, compute_truth_score
from .knowledge import get_kb

__all__ = ["analyze", "compute_truth_score", "get_kb"]
