from typing import Annotated

from fastapi import Depends

from app.core.config import Settings, get_settings
from app.modules.director.pricing import ConfiguredVisualEstimator, VisualEstimates
from app.modules.director.service import DirectorService


def get_director(settings: Annotated[Settings, Depends(get_settings)]) -> DirectorService:
    return DirectorService(
        ConfiguredVisualEstimator(
            VisualEstimates(
                image_usd=settings.director_image_estimate_usd,
                video_second_usd=settings.director_video_second_estimate_usd,
            )
        ),
        settings.director_visual_budget_fraction,
    )


CurrentDirector = Annotated[DirectorService, Depends(get_director)]
