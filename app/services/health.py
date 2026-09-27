from redis import Redis
from redis.exceptions import ConnectionError
from sqlalchemy import text
from sqlmodel import Session


def check_dependencies(db: Session, redis: Redis) -> None:
    db.execute(text("SELECT 1"))
    if not redis.ping():
        raise ConnectionError("Redis did not acknowledge PING")
