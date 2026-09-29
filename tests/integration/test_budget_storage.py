from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier
from uuid import UUID

from sqlmodel import Session, select

from app.modules.costs.models import CostEvent
from app.modules.costs.service import BudgetExceeded, BudgetService


def test_concurrent_reservations_cannot_overspend(pg_client, postgres_engine, story_factory):
    _, _, video, _ = story_factory(pg_client)
    barrier = Barrier(2)

    def reserve(key):
        with Session(postgres_engine) as db:
            barrier.wait(timeout=10)
            try:
                BudgetService.reserve(
                    db,
                    UUID(video),
                    key=key,
                    amount=Decimal("0.15"),
                    provider="test",
                    operation="test",
                    model="test",
                )
                db.commit()
                return True
            except BudgetExceeded:
                db.rollback()
                return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(reserve, ["first", "second"]))
    assert sorted(results) == [False, True]
    with Session(postgres_engine) as db:
        assert BudgetService.spent(db, UUID(video)) == Decimal("0.15")


def test_concurrent_replay_reserves_once(pg_client, postgres_engine, story_factory):
    _, _, video, _ = story_factory(pg_client)
    barrier = Barrier(2)

    def reserve(_):
        with Session(postgres_engine, expire_on_commit=False) as db:
            barrier.wait(timeout=10)
            event = BudgetService.reserve(
                db,
                UUID(video),
                key="same",
                amount=Decimal("0.15"),
                provider="test",
                operation="test",
                model="test",
            )
            db.commit()
            return event.id

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(reserve, range(2)))
    assert results[0] == results[1]
    with Session(postgres_engine) as db:
        assert len(db.exec(select(CostEvent)).all()) == 1
