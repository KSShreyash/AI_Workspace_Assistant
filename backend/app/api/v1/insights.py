from fastapi import APIRouter, Depends

from app.core.deps import CurrentUser, get_current_user
from app.db.session import get_pool
from app.repositories.insights import InsightsRepository

router = APIRouter(prefix="/api/insights", tags=["insights"])


@router.get("")
async def get_insights(current_user: CurrentUser = Depends(get_current_user)):
    pool = await get_pool()
    insights = InsightsRepository(pool)
    user_id = current_user.user_id

    return {
        "you_owe": await insights.you_owe(user_id),
        "owed_to_you": await insights.owed_to_you(user_id),
        "top_senders": await insights.top_senders(user_id),
        "pipeline": await insights.pipeline(user_id),
    }
