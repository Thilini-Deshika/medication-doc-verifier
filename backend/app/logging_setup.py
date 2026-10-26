"""Structured JSON logging with a per-task context.

Every record carries the job id, document id and stage that are bound in the
current context, so one document can be traced end to end with a single grep
on `job_id`. Document text is never logged, only ids, counts, scores and
timings.
"""

import contextlib
import contextvars
import json
import logging
import sys
from datetime import datetime, timezone

_ctx: contextvars.ContextVar[dict] = contextvars.ContextVar("log_ctx", default={})


@contextlib.contextmanager
def bind(**fields):
    token = _ctx.set({**_ctx.get(), **{k: str(v) for k, v in fields.items() if v is not None}})
    try:
        yield
    finally:
        _ctx.reset(token)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "event": record.getMessage(),
            **_ctx.get(),
            **getattr(record, "fields", {}),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


class PlainFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        extra = {**_ctx.get(), **getattr(record, "fields", {})}
        base = f"{record.levelname:<7} {record.name}: {record.getMessage()}"
        if extra:
            base += " " + " ".join(f"{k}={v}" for k, v in extra.items())
        if record.exc_info:
            base += "\n" + self.formatException(record.exc_info)
        return base


def configure_logging(level: str = "INFO", as_json: bool = True) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter() if as_json else PlainFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    for noisy in ("httpx", "httpx2", "httpcore", "urllib3", "openai", "uvicorn.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def log(logger: logging.Logger, event: str, level: int = logging.INFO, **fields) -> None:
    logger.log(level, event, extra={"fields": fields})
