from typing import Annotated

from fastapi import Header
from fastapi.responses import JSONResponse

from app.modules.tasks.schemas import TaskRead

TaskKey = Annotated[str | None, Header(alias="Idempotency-Key", min_length=1, max_length=200)]


def accepted(task: TaskRead) -> JSONResponse:
    return JSONResponse(
        status_code=202,
        content=task.model_dump(mode="json"),
        headers={"Location": f"/api/v1/tasks/{task.id}"},
    )
