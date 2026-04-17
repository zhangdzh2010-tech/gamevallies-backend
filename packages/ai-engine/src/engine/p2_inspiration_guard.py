"""P2.3 inspiration quality regression guard.

Purpose
-------
PR-12 adds an *inspiration lane* that injects top-k template snippets into
the system prompt for a configurable fraction of requests. That lane can
degrade output quality in two ways:

1. The template cache may be stale or low-signal for the current cohort, so
   injected snippets are *distracting* rather than helpful.
2. A recent regression (bad cache refresh, schema drift) may silently hurt
   fun_score for lane-hit generations.

This module provides a lightweight, in-process circuit breaker that
tracks ``fun_score`` for recent lane-hit and lane-miss generations per tier.
When the lane-hit mean drops materially below the lane-miss mean over a
window of observations, the guard trips and tells the lane-decider to skip
injection until enough subsequent misses bring the lane-hit mean back to
parity, at which point the guard auto-recovers.

Important properties
--------------------
* **Pure in-memory; never persists.** Resetting the process clears all
  state, which is the correct failure mode for a safety net.
* **Thread-safe via a simple module-level Lock.**
* **Never raises.** All public entrypoints are wrapped; on any error the
  guard defaults to "do not skip / do not trip" so it can never make the
  lane *worse* than PR-12 already is on its own.
* **Disabled by default.** ``P2_INSPIRATION_GUARD_ENABLED`` must be True
  in settings for the guard to do anything beyond record samples.

The window size, trip gap and recovery gap are tunable via settings:
``P2_GUARD_WINDOW_SIZE``, ``P2_GUARD_TRIP_DELTA``, ``P2_GUARD_MIN_SAMPLES``.
"""
from __future__ import annotations

import statistics
import threading
from collections import deque
from typing import Any, Deque, Dict, Optional, Tuple


_DEFAULT_WINDOW = 20
_DEFAULT_MIN_SAMPLES = 6
_DEFAULT_TRIP_DELTA = 0.75  # hit mean must be this much below miss mean

# Per-tier state: deques of recent (hit:bool, fun_score:float) tuples.
_WINDOWS: Dict[str, Deque[Tuple[bool, float]]] = {}
_TRIPPED: Dict[str, bool] = {}
# Pending decisions keyed by a correlation token supplied by the generator
# (task_id / variation_seed). Commit pops and feeds record_outcome. Bounded
# by a cap to avoid unbounded growth if the runner never commits some keys.
_PENDING: Dict[str, Tuple[str, bool]] = {}
_PENDING_CAP = 512
_LOCK = threading.Lock()


def _settings_or_defaults() -> Tuple[int, int, float, bool]:
    """Return ``(window, min_samples, trip_delta, enabled)``; never raises."""
    try:
        from ..config.settings import settings  # type: ignore
    except Exception:
        return (_DEFAULT_WINDOW, _DEFAULT_MIN_SAMPLES, _DEFAULT_TRIP_DELTA, False)
    try:
        window = int(getattr(settings, "P2_GUARD_WINDOW_SIZE", _DEFAULT_WINDOW) or _DEFAULT_WINDOW)
    except Exception:
        window = _DEFAULT_WINDOW
    try:
        min_samples = int(getattr(settings, "P2_GUARD_MIN_SAMPLES", _DEFAULT_MIN_SAMPLES) or _DEFAULT_MIN_SAMPLES)
    except Exception:
        min_samples = _DEFAULT_MIN_SAMPLES
    try:
        trip_delta = float(getattr(settings, "P2_GUARD_TRIP_DELTA", _DEFAULT_TRIP_DELTA) or _DEFAULT_TRIP_DELTA)
    except Exception:
        trip_delta = _DEFAULT_TRIP_DELTA
    enabled = bool(getattr(settings, "P2_INSPIRATION_GUARD_ENABLED", False))
    return window, max(2, min_samples), max(0.0, trip_delta), enabled


def _tier_key(tier: Any) -> str:
    if tier is None:
        return "__default__"
    raw = getattr(tier, "value", tier)
    try:
        s = str(raw).strip()
    except Exception:
        return "__default__"
    return s or "__default__"


def _get_window(key: str, size: int) -> Deque[Tuple[bool, float]]:
    w = _WINDOWS.get(key)
    if w is None or w.maxlen != size:
        w = deque(w or (), maxlen=size)
        _WINDOWS[key] = w
    return w


def _means(window: Deque[Tuple[bool, float]]) -> Tuple[Optional[float], Optional[float], int, int]:
    hits = [s for h, s in window if h]
    misses = [s for h, s in window if not h]
    hm = statistics.fmean(hits) if hits else None
    mm = statistics.fmean(misses) if misses else None
    return hm, mm, len(hits), len(misses)


def should_skip(tier: Any) -> bool:
    """Return True if the lane should be skipped for this tier."""
    try:
        _, _, _, enabled = _settings_or_defaults()
        if not enabled:
            return False
        key = _tier_key(tier)
        with _LOCK:
            return bool(_TRIPPED.get(key, False))
    except Exception:
        return False


def record_outcome(tier: Any, *, hit: bool, fun_score: Optional[float]) -> Dict[str, Any]:
    """Feed a ``fun_score`` observation into the window.

    Returns a dict describing what state changed (empty if nothing). Callers
    may use this for telemetry. Never raises.
    """
    try:
        if fun_score is None:
            return {}
        window_size, min_samples, trip_delta, enabled = _settings_or_defaults()
        if not enabled:
            # Still record samples even when disabled so that enabling the
            # flag mid-session immediately has a warm window; but do not
            # change tripped state.
            pass
        try:
            sample = float(fun_score)
        except (TypeError, ValueError):
            return {}
        key = _tier_key(tier)
        result: Dict[str, Any] = {}
        with _LOCK:
            window = _get_window(key, window_size)
            window.append((bool(hit), sample))
            hm, mm, n_hit, n_miss = _means(window)
            was_tripped = bool(_TRIPPED.get(key, False))
            # Only (un)trip when both cohorts are adequately represented.
            if hm is not None and mm is not None and min(n_hit, n_miss) >= min_samples:
                gap = mm - hm  # positive means hit is worse than miss
                if not was_tripped and gap >= trip_delta:
                    _TRIPPED[key] = True
                    result = {
                        "event": "inspiration_guard_tripped",
                        "tier": key,
                        "hit_mean": hm,
                        "miss_mean": mm,
                        "gap": gap,
                    }
                elif was_tripped and gap <= 0.0:
                    # Parity (or hit is now better): recover.
                    _TRIPPED[key] = False
                    result = {
                        "event": "inspiration_guard_recovered",
                        "tier": key,
                        "hit_mean": hm,
                        "miss_mean": mm,
                        "gap": gap,
                    }
        return result
    except Exception:
        return {}


def snapshot(tier: Any = None) -> Dict[str, Any]:
    """Return a diagnostic snapshot for tests / admin endpoints."""
    try:
        with _LOCK:
            if tier is None:
                return {
                    "tripped": dict(_TRIPPED),
                    "window_sizes": {k: len(v) for k, v in _WINDOWS.items()},
                }
            key = _tier_key(tier)
            window = _WINDOWS.get(key)
            hm, mm, nh, nm = _means(window) if window else (None, None, 0, 0)
            return {
                "tier": key,
                "tripped": bool(_TRIPPED.get(key, False)),
                "hit_mean": hm,
                "miss_mean": mm,
                "n_hit": nh,
                "n_miss": nm,
                "window_size": len(window) if window else 0,
            }
    except Exception:
        return {}


def note_lane_decision(key: Any, tier: Any, *, hit: bool) -> None:
    """Stash the (tier, hit) pair for later commit by the runner. Never raises.

    ``key`` should be a correlation token that both the generator and the
    runner can produce — task_id or variation_seed are both fine.
    """
    try:
        k = str(key).strip()
        if not k:
            return
        with _LOCK:
            if len(_PENDING) >= _PENDING_CAP:
                # Drop oldest-ish (dict iteration order) to bound memory.
                try:
                    _PENDING.pop(next(iter(_PENDING)), None)
                except Exception:
                    _PENDING.clear()
            _PENDING[k] = (_tier_key(tier), bool(hit))
    except Exception:
        return


def commit_fun_score(key: Any, fun_score: Optional[float]) -> Dict[str, Any]:
    """Pop the pending decision for ``key`` and feed ``fun_score`` into the
    window. Returns the same dict shape as :func:`record_outcome`.
    """
    try:
        k = str(key).strip() if key is not None else ""
        if not k or fun_score is None:
            return {}
        with _LOCK:
            entry = _PENDING.pop(k, None)
        if entry is None:
            return {}
        tier_key, hit = entry
        return record_outcome(tier_key, hit=hit, fun_score=fun_score)
    except Exception:
        return {}


def _reset_for_tests() -> None:
    """Clear all guard state. Intended for unit / probe tests only."""
    with _LOCK:
        _WINDOWS.clear()
        _TRIPPED.clear()
        _PENDING.clear()


__all__ = [
    "should_skip",
    "record_outcome",
    "note_lane_decision",
    "commit_fun_score",
    "snapshot",
    "_reset_for_tests",
]
