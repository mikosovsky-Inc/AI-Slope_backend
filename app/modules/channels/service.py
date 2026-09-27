from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func
from sqlmodel import Session, delete, select

from app.modules.channels.models import Channel, ChannelBlueprint, ChannelStatus, ContentPillar
from app.modules.channels.schemas import (
    BlueprintConfiguration,
    BlueprintInput,
    BlueprintRead,
    ChannelCreate,
    ChannelDetail,
    ChannelPage,
    ChannelRead,
    ChannelUpdate,
    PillarRead,
)


class ChannelNotFound(Exception):
    pass


def owned_channel(db: Session, owner_id: UUID, channel_id: UUID, *, lock: bool = False) -> Channel:
    query = select(Channel).where(Channel.id == channel_id, Channel.owner_id == owner_id)
    if lock:
        query = query.with_for_update()
    channel = db.exec(query).one_or_none()
    if channel is None:
        raise ChannelNotFound
    return channel


def detail(db: Session, channel: Channel) -> ChannelDetail:
    blueprint = db.exec(
        select(ChannelBlueprint).where(ChannelBlueprint.channel_id == channel.id)
    ).one()
    pillars = db.exec(
        select(ContentPillar)
        .where(ContentPillar.blueprint_id == blueprint.id)
        .order_by(ContentPillar.position)
    ).all()
    return ChannelDetail(
        **ChannelRead.model_validate(channel).model_dump(),
        blueprint=BlueprintRead(
            id=blueprint.id,
            configuration=BlueprintConfiguration.model_validate(blueprint.configuration),
            content_pillars=[PillarRead.model_validate(p) for p in pillars],
            updated_at=blueprint.updated_at,
        ),
    )


def create_channel(db: Session, owner_id: UUID, data: ChannelCreate) -> ChannelDetail:
    values = data.model_dump()
    values["name"] = data.name or data.idea[:120]
    channel = Channel(owner_id=owner_id, **values)
    try:
        db.add(channel)
        db.flush()
        db.add(
            ChannelBlueprint(
                channel_id=channel.id,
                configuration=BlueprintConfiguration().model_dump(mode="json"),
            )
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(channel)
    return detail(db, channel)


def list_channels(db: Session, owner_id: UUID, limit: int, offset: int) -> ChannelPage:
    total = db.exec(
        select(func.count()).select_from(Channel).where(Channel.owner_id == owner_id)
    ).one()
    channels = db.exec(
        select(Channel)
        .where(Channel.owner_id == owner_id)
        .order_by(Channel.created_at.desc(), Channel.id.desc())
        .offset(offset)
        .limit(limit)
    ).all()
    return ChannelPage(
        items=[ChannelRead.model_validate(c) for c in channels],
        total=total,
        limit=limit,
        offset=offset,
    )


def replace_blueprint(db: Session, channel: Channel, data: BlueprintInput) -> None:
    blueprint = db.exec(
        select(ChannelBlueprint).where(ChannelBlueprint.channel_id == channel.id)
    ).one()
    blueprint.configuration = data.configuration.model_dump(mode="json")
    blueprint.updated_at = datetime.now(UTC)
    db.add(blueprint)
    db.exec(delete(ContentPillar).where(ContentPillar.blueprint_id == blueprint.id))
    for position, pillar in enumerate(data.content_pillars):
        db.add(ContentPillar(blueprint_id=blueprint.id, position=position, **pillar.model_dump()))


def update_channel(
    db: Session, owner_id: UUID, channel_id: UUID, data: ChannelUpdate
) -> ChannelDetail:
    channel = owned_channel(db, owner_id, channel_id, lock=True)
    try:
        for field, value in data.model_dump(exclude_unset=True, exclude={"blueprint"}).items():
            setattr(channel, field, value)
        channel.updated_at = datetime.now(UTC)
        db.add(channel)
        if data.blueprint is not None:
            replace_blueprint(db, channel, data.blueprint)
        db.commit()
    except Exception:
        db.rollback()
        raise
    db.refresh(channel)
    return detail(db, channel)


def change_status(
    db: Session, owner_id: UUID, channel_id: UUID, status: ChannelStatus
) -> ChannelDetail:
    channel = owned_channel(db, owner_id, channel_id, lock=True)
    if channel.status != status:
        channel.status = status
        channel.updated_at = datetime.now(UTC)
        try:
            db.add(channel)
            db.commit()
        except Exception:
            db.rollback()
            raise
        db.refresh(channel)
    return detail(db, channel)


def delete_channel(db: Session, owner_id: UUID, channel_id: UUID) -> None:
    channel = owned_channel(db, owner_id, channel_id, lock=True)
    try:
        db.delete(channel)
        db.commit()
    except Exception:
        db.rollback()
        raise
