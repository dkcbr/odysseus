"""Lightweight timing helpers. No external dependencies -- logs a single
structured line via the standard logger, matching this codebase's existing
logger.info/logger.error usage. Not a metrics backend; if one gets added
later, these labels map cleanly onto it."""
import time
import functools
import logging

logger = logging.getLogger(__name__)


def time_block(name: str, *, level: int = logging.INFO, extra: dict | None = None):
    """Context manager to time a block and log a single-line message."""
    class _Timer:
        def __enter__(self):
            self.start = time.perf_counter()
            return self

        def __exit__(self, exc_type, exc, tb):
            elapsed_ms = (time.perf_counter() - self.start) * 1000.0
            payload = {"component": name, "elapsed_ms": round(elapsed_ms, 3)}
            if extra:
                payload.update(extra)
            logger.log(level, "%s %s", name, payload)
    return _Timer()


def timed(name: str = None, *, level: int = logging.INFO, threshold_ms: float | None = None):
    """Decorator that logs elapsed time and warns if above threshold_ms."""
    def decorator(fn):
        label = name or f"{fn.__module__}.{fn.__qualname__}"

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            start = time.perf_counter()
            try:
                return fn(*args, **kwargs)
            finally:
                elapsed_ms = (time.perf_counter() - start) * 1000.0
                payload = {"component": label, "elapsed_ms": round(elapsed_ms, 3)}
                if threshold_ms is not None and elapsed_ms > threshold_ms:
                    logger.warning("%s %s", label, payload)
                else:
                    logger.log(level, "%s %s", label, payload)
        return wrapper
    return decorator
