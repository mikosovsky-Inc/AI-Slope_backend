"""One-shot Compose initializer: write only the metrics credential, never print it."""

from pathlib import Path

from app.core.config import get_settings


def main():
    settings = get_settings()
    if not settings.metrics_enabled or not settings.metrics_token:
        raise SystemExit("Set METRICS_ENABLED=true and METRICS_TOKEN before using monitoring")
    path = Path("/credentials/token")
    temporary = path.with_suffix(".tmp")
    temporary.write_text(settings.metrics_token.get_secret_value(), encoding="ascii")
    temporary.chmod(0o444)
    temporary.replace(path)


if __name__ == "__main__":
    main()
