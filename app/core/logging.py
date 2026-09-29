import json
import logging
from datetime import UTC, datetime
from logging.config import dictConfig

from app.core.observability import context


class JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {
            "timestamp": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        fields = context.get() | record.__dict__
        for key in (
            "request_id",
            "task_id",
            "video_id",
            "channel_id",
            "scene_id",
            "event",
            "task_kind",
            "queue",
            "status",
            "status_code",
            "method",
            "route",
            "duration_ms",
            "provider",
            "provider_type",
            "operation",
            "outcome",
            "error_category",
            "attempts",
            "scanned",
            "planned",
            "videos_created",
            "idea_tasks_created",
            "errors",
        ):
            value = fields.get(key)
            if isinstance(value, (str, int, float, bool)):
                entry[key] = value
        if hasattr(record, "error_type"):
            entry["error_type"] = record.error_type
        # Avoid serializing exception messages, SQL parameters or request bodies.
        if record.exc_info and record.exc_info[0]:
            entry["error_type"] = record.exc_info[0].__name__
        return json.dumps(entry, ensure_ascii=False)


def configure_logging(level: str) -> None:
    dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {"json": {"()": JSONFormatter}},
            "handlers": {
                "console": {
                    "class": "logging.StreamHandler",
                    "formatter": "json",
                    "stream": "ext://sys.stdout",
                }
            },
            "loggers": {
                "app": {"handlers": ["console"], "level": level, "propagate": False},
                # The application middleware logs route templates, without raw query strings.
                "uvicorn.access": {"handlers": [], "level": "WARNING", "propagate": False},
            },
        }
    )
