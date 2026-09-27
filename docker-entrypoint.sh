#!/bin/sh
set -eu
# One API instance in local Compose. Apply migrations before accepting traffic.
alembic upgrade head
exec "$@"
