from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

type RankingCategory = Literal["web", "ai", "data", "testing", "engineering"]


class RankingCategoryInfo(BaseModel):
    id: RankingCategory
    label: str


class PypiRankingItem(BaseModel):
    project: str
    name: str
    category: RankingCategory
    category_label: str
    description: str
    download_count: int | None
    rank: int | None
    pypi_url: str


class PypiRankingResponse(BaseModel):
    snapshot_id: str
    source: Literal["hugovk/top-pypi-packages"] = "hugovk/top-pypi-packages"
    upstream: Literal["ClickHouse"] = "ClickHouse"
    source_url: str
    period: str
    source_updated_at: datetime
    fetched_at: datetime
    checked_at: datetime
    catalog_version: str
    candidate_count: int
    matched_count: int
    source_count: int
    filtered_count: int
    stale: bool
    warnings: list[str] = Field(default_factory=list)
    categories: list[RankingCategoryInfo]
    items: list[PypiRankingItem]
