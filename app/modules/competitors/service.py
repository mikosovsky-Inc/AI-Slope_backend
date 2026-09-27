from datetime import UTC, datetime
from uuid import UUID

from pydantic import TypeAdapter, ValidationError
from sqlalchemy import func
from sqlmodel import Session, delete, select

from app.modules.channels.models import ChannelBlueprint
from app.modules.channels.schemas import BlueprintConfiguration
from app.modules.channels.service import owned_channel
from app.modules.competitors.models import Competitor, CompetitorContent
from app.modules.competitors.schemas import CompetitorPage, CompetitorRead, ResearchSummary
from app.modules.intelligence.research import (
    CompetitorResearchProvider,
    CompetitorResearchQuery,
    CompetitorResearchResult,
)


class ResearchNotReady(Exception):
    pass


class ResearchConflict(Exception):
    pass


class ResearchInvalidOutput(Exception):
    pass


class ResearchUnavailable(Exception):
    pass


def read_competitor(db: Session, row: Competitor) -> CompetitorRead:
    titles = db.exec(
        select(CompetitorContent)
        .where(CompetitorContent.competitor_id == row.id)
        .order_by(CompetitorContent.position)
    ).all()
    return CompetitorRead(**row.model_dump(), example_titles=[item.title for item in titles])


def list_competitors(
    db: Session, owner_id: UUID, channel_id: UUID, limit: int, offset: int
) -> CompetitorPage:
    owned_channel(db, owner_id, channel_id)
    total = db.exec(
        select(func.count()).select_from(Competitor).where(Competitor.channel_id == channel_id)
    ).one()
    rows = db.exec(
        select(Competitor)
        .where(Competitor.channel_id == channel_id)
        .order_by(Competitor.name, Competitor.id)
        .offset(offset)
        .limit(limit)
    ).all()
    return CompetitorPage(
        items=[read_competitor(db, row) for row in rows], total=total, limit=limit, offset=offset
    )


def research_competitors(
    db: Session, owner_id: UUID, channel_id: UUID, provider: CompetitorResearchProvider
) -> ResearchSummary:
    channel = owned_channel(db, owner_id, channel_id)
    version = channel.updated_at
    blueprint = db.exec(
        select(ChannelBlueprint).where(ChannelBlueprint.channel_id == channel_id)
    ).one()
    config = BlueprintConfiguration.model_validate(blueprint.configuration)
    if not config.seed_keywords:
        raise ResearchNotReady
    query = CompetitorResearchQuery(language=channel.language, seed_keywords=config.seed_keywords)
    db.rollback()
    try:
        records = TypeAdapter(list[CompetitorResearchResult]).validate_python(
            provider.research(query)
        )
        if len(records) > query.limit or any(r.language != query.language for r in records):
            raise ResearchInvalidOutput
        urls = [str(record.url) for record in records]
        if len(set(urls)) != len(urls):
            raise ResearchInvalidOutput
    except ValidationError:
        raise ResearchInvalidOutput from None
    try:
        db.expire_all()
        channel = owned_channel(db, owner_id, channel_id, lock=True)
        if channel.updated_at != version:
            raise ResearchConflict
        rows = []
        for record in records:
            row = db.exec(
                select(Competitor).where(
                    Competitor.channel_id == channel_id, Competitor.url == str(record.url)
                )
            ).one_or_none()
            values = record.model_dump(mode="json", exclude={"example_titles"})
            if row is None:
                row = Competitor(channel_id=channel_id, **values)
            else:
                for key, value in values.items():
                    setattr(row, key, value)
            row.updated_at = datetime.now(UTC)
            db.add(row)
            db.flush()
            db.exec(delete(CompetitorContent).where(CompetitorContent.competitor_id == row.id))
            for position, title in enumerate(dict.fromkeys(record.example_titles)):
                db.add(CompetitorContent(competitor_id=row.id, title=title, position=position))
            rows.append(row)
        # Serializes concurrent research results and edits with the channel row lock.
        if records:
            channel.updated_at = datetime.now(UTC)
            db.add(channel)
        db.flush()
        db.refresh(channel)
        for row in rows:
            db.refresh(row)
        result = ResearchSummary(processed=len(rows), items=[read_competitor(db, r) for r in rows])
        db.commit()
        return result
    except Exception:
        db.rollback()
        raise
