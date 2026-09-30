from datetime import UTC, datetime
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func
from sqlmodel import Session, select

from app.core.config import Settings
from app.models.user import User
from app.modules.assets.models import Asset, AssetType
from app.modules.quality.inspection import QualityInspector
from app.modules.quality.models import QualityCheck, QualityStatus
from app.modules.quality.provider import VisualQualityProvider
from app.modules.quality.schemas import QualityCheckRead, QualityPolicy
from app.modules.render.service import RenderManifest
from app.modules.scripts.service import owned_video
from app.modules.tasks.models import Task, TaskKind, TaskStatus
from app.modules.tasks.schemas import TaskRead
from app.modules.tasks.service import enqueue
from app.modules.videos.models import VideoStatus
from app.modules.videos.service import transition_video
from app.shared.storage import StorageProvider


class QualityPending(Exception):
    """Wait for already submitted scene repairs; this is not another inspection attempt."""


def enqueue_quality(db: Session, owner_id: UUID, render: Task, *, commit: bool = True) -> TaskRead:
    return enqueue(
        db,
        owner_id,
        TaskKind.QUALITY,
        video_id=render.video_id,
        parameters={"render_task_id": str(render.id)}
        | (
            {
                "revision_id": render.parameters["revision_id"],
                "revision_attempt": render.parameters.get("revision_attempt", 1),
            }
            if render.parameters.get("revision_id")
            else {}
        ),
        key=f"quality:{render.id}",
        commit=commit,
    )


def submit_quality(db: Session, owner_id: UUID, video_id: UUID) -> TaskRead:
    owned_video(db, owner_id, video_id)
    render = db.exec(
        select(Task)
        .where(
            Task.video_id == video_id,
            Task.owner_id == owner_id,
            Task.kind == TaskKind.RENDER,
            Task.status == TaskStatus.SUCCEEDED,
        )
        .order_by(Task.created_at.desc(), Task.id.desc())
    ).first()
    if render is None:
        raise HTTPException(409, "Complete a render before requesting quality control")
    return enqueue_quality(db, owner_id, render)


def read_checks(db: Session, owner_id: UUID, video_id: UUID) -> list[QualityCheckRead]:
    owned_video(db, owner_id, video_id)
    return [
        QualityCheckRead.model_validate(q)
        for q in db.exec(
            select(QualityCheck)
            .where(QualityCheck.video_id == video_id)
            .order_by(QualityCheck.attempt)
        ).all()
    ]


def lock_owner(db: Session, owner_id: UUID) -> None:
    db.exec(select(User).where(User.id == owner_id).with_for_update()).one()


class QualityService:
    def __init__(
        self, settings: Settings, storage: StorageProvider, visual: VisualQualityProvider
    ) -> None:
        self.settings, self.storage, self.visual = settings, storage, visual

    def run(self, db: Session, task: Task) -> dict:
        lock_owner(db, task.owner_id)
        video = owned_video(db, task.owner_id, task.video_id, lock=True)
        render_id = UUID(task.parameters["render_task_id"])
        render = db.get(Task, render_id)
        if (
            render is None
            or render.video_id != video.id
            or render.owner_id != task.owner_id
            or render.kind != TaskKind.RENDER
            or render.status != TaskStatus.SUCCEEDED
        ):
            raise ValueError("Quality control requires an owned, completed render")
        check = db.exec(
            select(QualityCheck).where(QualityCheck.render_task_id == render_id)
        ).first()
        if check and check.status in (
            QualityStatus.PASSED,
            QualityStatus.REPAIRED,
            QualityStatus.FAILED,
        ):
            result = QualityCheckRead.model_validate(check).model_dump(mode="json")
            db.commit()
            return result
        if video.status != VideoStatus.QUALITY_CHECK:
            raise ValueError("Video is not awaiting quality control")
        if check is None:
            attempt = (
                db.exec(
                    select(func.max(QualityCheck.attempt)).where(QualityCheck.video_id == video.id)
                ).one()
                or 0
            ) + 1
            identifier = (render.result or {}).get("asset_id")
            final = db.get(Asset, UUID(identifier)) if identifier else None
            if final is not None and (
                final.video_id != video.id or final.type != AssetType.FINAL_VIDEO
            ):
                final = None
            check = QualityCheck(
                video_id=video.id,
                render_task_id=render.id,
                task_id=task.id,
                final_asset_id=final.id if final else None,
                attempt=attempt,
                policy_json=QualityPolicy(
                    duration_tolerance_seconds=self.settings.quality_duration_tolerance_seconds,
                    max_scene_retries=self.settings.quality_max_scene_retries,
                    repair_timeout_seconds=self.settings.quality_repair_timeout_seconds,
                ).model_dump(),
            )
            db.add(check)
            db.commit()
        if check.status == QualityStatus.REPAIRING:
            return self.finish_repairs(db, task, check, render)
        policy = QualityPolicy.model_validate(check.policy_json)
        final = db.get(Asset, check.final_asset_id) if check.final_asset_id else None
        db.commit()
        report = QualityInspector(self.settings, self.storage, self.visual).inspect(
            db, video, render, final, policy
        )
        lock_owner(db, task.owner_id)
        video = owned_video(db, task.owner_id, task.video_id, lock=True)
        if video.status != VideoStatus.QUALITY_CHECK:
            raise ValueError("Video changed during inspection")
        check.report_json = report.model_dump(mode="json")
        failures = report.failures
        if not failures:
            check.status = QualityStatus.PASSED
            check.completed_at = datetime.now(UTC)
            transition_video(
                db, video.id, VideoStatus.READY, reason="Technical quality checks passed"
            )
        elif (
            all(f.scene_id is not None and f.repair is not None for f in failures)
            and task.parameters.get("revision_attempt", check.attempt) <= policy.max_scene_retries
        ):
            repairs = sorted({(str(f.scene_id), f.repair) for f in failures})
            children = []
            for scene_id, kind in repairs:
                child = enqueue(
                    db,
                    task.owner_id,
                    TaskKind(kind),
                    video_id=video.id,
                    scene_id=UUID(scene_id),
                    parameters={"quality_check_id": str(check.id)},
                    key=f"quality-repair:{check.id}:{scene_id}:{kind}",
                    commit=False,
                )
                children.append({"task_id": str(child.id), "scene_id": scene_id, "kind": kind})
            check.repair_tasks = children
            check.status = QualityStatus.REPAIRING
        else:
            self.fail(
                db,
                check,
                "scene_retry_limit"
                if all(f.repair for f in failures)
                else "technical_quality_failed",
            )
        db.add(check)
        db.commit()
        if check.status == QualityStatus.REPAIRING:
            raise QualityPending
        return QualityCheckRead.model_validate(check).model_dump(mode="json")

    def finish_repairs(self, db: Session, task: Task, check: QualityCheck, render: Task) -> dict:
        policy = QualityPolicy.model_validate(check.policy_json)
        children = [(item, db.get(Task, UUID(item["task_id"]))) for item in check.repair_tasks]
        expired = (
            datetime.now(UTC) - check.created_at.replace(tzinfo=UTC)
        ).total_seconds() > policy.repair_timeout_seconds
        failed = not children or any(
            child is None or child.status in (TaskStatus.FAILED, TaskStatus.NEEDS_REVIEW)
            for _, child in children
        )
        if expired or failed:
            self.fail(db, check, "scene_repair_timeout" if expired else "scene_repair_failed")
        elif any(child.status != TaskStatus.SUCCEEDED for _, child in children):
            db.commit()
            raise QualityPending
        else:
            manifest = RenderManifest.model_validate(render.checkpoint["render_manifest"])
            for item, child in children:
                scene_id = UUID(item["scene_id"])
                result = child.result or {}
                try:
                    asset_id = UUID(result.get("asset_id") or result["id"])
                except (ValueError, KeyError, TypeError):
                    self.fail(db, check, "scene_repair_result_invalid")
                    break
                asset = db.get(Asset, asset_id)
                if (
                    asset is None
                    or asset.video_id != check.video_id
                    or asset.scene_id != scene_id
                    or asset.type.value != item["kind"]
                ):
                    self.fail(db, check, "scene_repair_result_invalid")
                    break
                scene = next(s for s in manifest.scenes if s.scene_id == scene_id)
                if item["kind"] == "audio":
                    scene.audio_asset_id = asset.id
                else:
                    scene.visual_asset_id = asset.id
            else:
                # Preserve every healthy asset and the original script; replace only failed inputs.
                transition_video(
                    db,
                    check.video_id,
                    VideoStatus.GENERATING_ASSETS,
                    reason="Quality control repaired selected scene assets",
                    expected_status=VideoStatus.QUALITY_CHECK,
                )
                child = enqueue(
                    db,
                    task.owner_id,
                    TaskKind.RENDER,
                    video_id=check.video_id,
                    parameters={"input_manifest": manifest.model_dump(mode="json")}
                    | (
                        {
                            "revision_id": task.parameters["revision_id"],
                            "revision_attempt": task.parameters.get("revision_attempt", 1) + 1,
                        }
                        if task.parameters.get("revision_id")
                        else {}
                    ),
                    key=f"quality-rerender:{check.id}",
                    commit=False,
                )
                check.rerender_task_id = child.id
                check.status = QualityStatus.REPAIRED
                check.completed_at = datetime.now(UTC)
        db.add(check)
        db.commit()
        return QualityCheckRead.model_validate(check).model_dump(mode="json")

    @staticmethod
    def fail(db: Session, check: QualityCheck, error: str) -> None:
        check.status, check.error, check.completed_at = (
            QualityStatus.FAILED,
            error,
            datetime.now(UTC),
        )
        transition_video(
            db,
            check.video_id,
            VideoStatus.FAILED,
            reason=error,
            expected_status=VideoStatus.QUALITY_CHECK,
        )
