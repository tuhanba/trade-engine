"""
core/signal_diagnostics.py — Neden trade açılmadı analitiği.
Her scan döngüsünde skip_reason istatistiklerini toplar.
"""
from collections import Counter
import logging

logger = logging.getLogger("ax.diagnostics")

_skip_counts: Counter = Counter()
_scan_count: int = 0


def record_skip(reason: str):
    _skip_counts[reason] += 1


def record_scan():
    global _scan_count
    _scan_count += 1


def get_summary() -> dict:
    return {
        "total_scans": _scan_count,
        "skip_reasons": dict(_skip_counts.most_common(10)),
    }


def log_hourly():
    if _scan_count > 0 and _scan_count % 60 == 0:
        logger.info(f"[Diagnostics] Skip summary: {dict(_skip_counts.most_common(5))}")
