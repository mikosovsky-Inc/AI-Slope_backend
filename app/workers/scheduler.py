import argparse
import logging
import signal
import threading

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db.session import get_engine
from app.modules.scheduler.service import plan_once

logger = logging.getLogger("app.scheduler")


def main() -> None:
    parser = argparse.ArgumentParser(description="Plan today's videos for active channels")
    parser.add_argument("--once", action="store_true", help="Run one planning pass and exit")
    args = parser.parse_args()
    settings = get_settings()
    configure_logging(settings.log_level)
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    engine = get_engine()
    try:
        while not stop.is_set():
            try:
                result = plan_once(engine, settings, should_stop=stop.is_set)
                logger.info("Daily planning pass completed", extra=result.model_dump())
                if args.once:
                    print(result.model_dump_json())
                    if result.errors:
                        raise SystemExit(1)
            except Exception as exc:
                logger.warning("Scheduler unavailable", extra={"error_type": type(exc).__name__})
                if args.once:
                    raise SystemExit(1) from None
            if args.once:
                break
            stop.wait(settings.scheduler_interval_seconds)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
