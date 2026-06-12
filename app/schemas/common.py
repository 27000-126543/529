from pydantic import BaseModel, ConfigDict
from typing import Optional
from datetime import datetime, date
from decimal import Decimal
from app.models import ApprovalStage, ApprovalStatus


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


class ProjectCreateResponse(BaseSchema):
    success: bool = True
    project_id: int
    project_no: str
    message: str = "申报成功"


class ProjectDetailResponse(BaseSchema):
    id: int
    project_no: str
    name: str
    area_id: Optional[int] = None
    applicant_id: Optional[int] = None
    project_type: Optional[str] = None
    scope: Optional[str] = None
    budget: Optional[Decimal] = None
    planned_start_date: Optional[date] = None
    planned_end_date: Optional[date] = None
    current_stage: ApprovalStage
    approval_status: ApprovalStatus
    construction_team_id: Optional[int] = None
    actual_start_date: Optional[date] = None
    actual_end_date: Optional[date] = None
    status: str
    created_at: Optional[datetime] = None
    approval_flow_status: str


class FinalApprovalResponse(BaseSchema):
    success: bool = True
    message: str
    construction_team_id: Optional[int] = None
    actual_start_date: Optional[date] = None
    status: str


class DailyReportCreateResponse(BaseSchema):
    id: int
    report_date: date
    area_id: Optional[int] = None
