from pydantic import BaseModel, ConfigDict
from typing import Optional
from datetime import datetime, date
from decimal import Decimal


class BaseSchema(BaseModel):
    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class IdResponse(BaseSchema):
    id: int


class PaginationParams(BaseSchema):
    page: int = 1
    page_size: int = 20

    @property
    def offset(self) -> int:
        return (self.page - 1) * self.page_size


class PaginationResponse(BaseSchema):
    total: int
    page: int
    page_size: int
    total_pages: int


class SuccessResponse(BaseSchema):
    success: bool = True
    message: str = "Operation successful"
