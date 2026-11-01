from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from ai_workspace.api.dependencies.rankings import get_pypi_ranking_service
from ai_workspace.rankings.errors import RankingUnavailable
from ai_workspace.rankings.schemas import PypiRankingResponse, RankingCategory
from ai_workspace.rankings.service import PypiRankingService

router = APIRouter(prefix="/rankings", tags=["rankings"])


@router.get("/pypi", response_model=PypiRankingResponse)
async def list_pypi_rankings(
    response: Response,
    service: Annotated[PypiRankingService, Depends(get_pypi_ranking_service)],
    query: Annotated[str, Query(max_length=100)] = "",
    category: RankingCategory | None = None,
    projects: Annotated[list[str] | None, Query(max_length=20)] = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 50,
) -> PypiRankingResponse:
    """公开只读接口；不读取登录、凭据、会话或个人学习数据。"""
    response.headers["Cache-Control"] = "no-store"
    try:
        return await service.list_rankings(
            query=query, category=category, projects=projects, limit=limit
        )
    except RankingUnavailable:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "rankings_unavailable",
                "message": "PyPI rankings are temporarily unavailable",
            },
            headers={"Cache-Control": "no-store", "Retry-After": "60"},
        ) from None
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "rankings_invalid_query",
                "message": "Invalid ranking query",
            },
            headers={"Cache-Control": "no-store"},
        ) from None
