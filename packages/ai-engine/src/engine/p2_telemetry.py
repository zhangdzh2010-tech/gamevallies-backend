"""P2.1 Telemetry — structured-INFO emission for P1 runtime paths.

Design notes
------------
* Zero hard dependencies beyond stdlib ``logging``.
* ``emit(event, **fields)`` is the only public surface. It never raises; on
  any formatting or serialization failure it degrades to a best-effort
  ``logger.debug`` line so pipelines never fail because of telemetry.
* Output format is a single structured line::

    p1_telemetry event=<name> k1=v1 k2=v2 ...

  This keeps grep/log-analyzer parsing trivial without committing to a
  specific observability backend. Individual emit sites are still free to
  additionally push to ``progress_cb``/``_notify`` elsewhere.
* Field values are coerced to primitive string/number; None / empty strings
  are dropped so the emitted line stays compact.
* A master switch ``settings.P2_TELEMETRY_ENABLED`` (default True) lets ops
  short-circuit emission without redeploying.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

_EVENT_PREFIX = "p1_telemetry"


def _coerce(value: Any) -> str | None:
    """Return a flat printable form or None if the field should be dropped."""
    if value is None:
        return None
    if isinstance(value, bool):
        # bools BEFORE int check: True/False are nicer than 1/0 in logs
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        # Keep numeric precision bounded so logs stay compact
        if isinstance(value, float):
            # round to 4 decimals; trim trailing zeros for readability
            s = f"{value:.4f}".rstrip("0").rstrip(".")
            return s or "0"
        return str(value)
    s = str(value).strip()
    if not s:
        return None
    # Escape spaces/equals to keep the single-line contract parseable
    return s.replace(" ", "_").replace("=", ":")[:160]


def _is_enabled() -> bool:
    try:
        from ..config.settings import settings  # type: ignore
    except Exception:  # pragma: no cover - settings optional in minimal deploys
        return True
    return bool(getattr(settings, "P2_TELEMETRY_ENABLED", True))


def emit(event: str, **fields: Any) -> None:
    """Emit one structured telemetry line at INFO level. Never raises."""
    try:
        if not event or not _is_enabled():
            return
        parts = [f"{_EVENT_PREFIX} event={event}"]
        for key, raw in fields.items():
            val = _coerce(raw)
            if val is None:
                continue
            parts.append(f"{key}={val}")
        logger.info(" ".join(parts))
    except Exception:  # pragma: no cover - telemetry must never crash caller
        try:
            logger.debug("p2_telemetry emit failed for event=%s", event, exc_info=True)
        except Exception:
            pass
