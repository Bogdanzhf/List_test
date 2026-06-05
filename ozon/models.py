from __future__ import annotations

from datetime import datetime
from typing import Literal, Union

from pydantic import BaseModel


class PositionResult(BaseModel):
    query: str
    sku: str
    position: Union[int, Literal["not_found"]]
    page: Union[int, None]
    total_checked: int
    timestamp: datetime

    def to_json(self) -> str:
        return self.model_dump_json(indent=2)
