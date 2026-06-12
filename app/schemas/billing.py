from typing import Optional, List
from datetime import datetime, date
from decimal import Decimal
from pydantic import Field
from app.schemas.common import BaseSchema, PaginationResponse
from app.models import (
    BillStatus, GasStatus, ApprovalStatus, ApprovalStage,
    PurchaseStatus, NotificationType
)


class ResidentAccountBase(BaseSchema):
    account_no: str
    user_id: Optional[int] = None
    area_id: int
    resident_name: str
    phone: Optional[str] = None
    address: Optional[str] = None
    longitude: Optional[Decimal] = None
    latitude: Optional[Decimal] = None
    meter_no: Optional[str] = None


class ResidentAccountCreate(ResidentAccountBase):
    pass


class ResidentAccountUpdate(BaseSchema):
    resident_name: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    longitude: Optional[Decimal] = None
    latitude: Optional[Decimal] = None
    meter_no: Optional[str] = None
    gas_status: Optional[GasStatus] = None
    tier_level: Optional[int] = None


class ResidentAccountInfo(ResidentAccountBase):
    id: int
    meter_reading: Decimal = Decimal("0")
    last_reading_date: Optional[date] = None
    gas_status: GasStatus = GasStatus.NORMAL
    gas_restricted_at: Optional[datetime] = None
    tier_level: int = 1
    created_at: Optional[datetime] = None


class ResidentAccountListResponse(PaginationResponse):
    items: List[ResidentAccountInfo]


class ResidentReportBase(BaseSchema):
    account_id: int
    area_id: Optional[int] = None
    report_type: str
    description: Optional[str] = None
    longitude: Optional[Decimal] = None
    latitude: Optional[Decimal] = None


class ResidentReportCreate(ResidentReportBase):
    pass


class ResidentReportInfo(ResidentReportBase):
    id: int
    report_no: str
    historical_match_count: int = 0
    auto_diagnosis: Optional[str] = None
    confidence_score: Optional[Decimal] = None
    status: str = "submitted"
    reported_at: Optional[datetime] = None
    resolved_at: Optional[datetime] = None


class ResidentReportListResponse(PaginationResponse):
    items: List[ResidentReportInfo]


class GasPriceTierBase(BaseSchema):
    tier: int
    name: str
    min_volume: Decimal
    max_volume: Optional[Decimal] = None
    unit_price: Decimal
    effective_date: date
    is_active: bool = True


class GasPriceTierCreate(GasPriceTierBase):
    pass


class GasPriceTierInfo(GasPriceTierBase):
    id: int


class GasPriceTierListResponse(PaginationResponse):
    items: List[GasPriceTierInfo]


class BillBase(BaseSchema):
    account_id: int
    billing_month: str
    billing_start_date: date
    billing_end_date: date
    previous_reading: Decimal
    current_reading: Decimal
    total_volume: Decimal
    due_date: date


class BillCreate(BillBase):
    pass


class BillInfo(BillBase):
    id: int
    bill_no: str
    tier1_volume: Decimal = Decimal("0")
    tier2_volume: Decimal = Decimal("0")
    tier3_volume: Decimal = Decimal("0")
    tier1_amount: Decimal = Decimal("0")
    tier2_amount: Decimal = Decimal("0")
    tier3_amount: Decimal = Decimal("0")
    surcharge: Decimal = Decimal("0")
    discount: Decimal = Decimal("0")
    total_amount: Decimal
    paid_amount: Decimal = Decimal("0")
    status: BillStatus = BillStatus.UNPAID
    paid_at: Optional[datetime] = None
    restriction_issued: bool = False
    restricted_at: Optional[datetime] = None
    collector_id: Optional[int] = None
    created_at: Optional[datetime] = None


class BillListResponse(PaginationResponse):
    items: List[BillInfo]


class PaymentCreate(BaseSchema):
    bill_id: int
    amount: Decimal
    payment_method: Optional[str] = None
    transaction_no: Optional[str] = None


class PaymentInfo(BaseSchema):
    id: int
    payment_no: str
    bill_id: int
    account_id: Optional[int] = None
    amount: Decimal
    payment_method: Optional[str] = None
    transaction_no: Optional[str] = None
    paid_at: Optional[datetime] = None


class MeterReadingCreate(BaseSchema):
    account_id: int
    reading_value: Decimal
    reading_date: date
    is_estimated: bool = False
    reader_id: Optional[int] = None


class MeterReadingInfo(BaseSchema):
    id: int
    account_id: int
    reading_value: Decimal
    reading_date: date
    is_estimated: bool
    reader_id: Optional[int] = None
    created_at: Optional[datetime] = None


class EngineeringProjectBase(BaseSchema):
    name: str
    area_id: Optional[int] = None
    project_type: Optional[str] = None
    scope: Optional[str] = None
    budget: Optional[Decimal] = None
    planned_start_date: Optional[date] = None
    planned_end_date: Optional[date] = None


class EngineeringProjectCreate(EngineeringProjectBase):
    pass


class EngineeringProjectInfo(EngineeringProjectBase):
    id: int
    project_no: str
    applicant_id: Optional[int] = None
    current_stage: ApprovalStage = ApprovalStage.SAFETY
    approval_status: ApprovalStatus = ApprovalStatus.PENDING
    construction_team_id: Optional[int] = None
    actual_start_date: Optional[date] = None
    actual_end_date: Optional[date] = None
    status: str = "draft"
    created_at: Optional[datetime] = None


class EngineeringProjectListResponse(PaginationResponse):
    items: List[EngineeringProjectInfo]


class ApprovalRecordCreate(BaseSchema):
    project_id: int
    stage: ApprovalStage
    approver_id: int


class ApprovalRecordUpdate(BaseSchema):
    status: ApprovalStatus
    comment: Optional[str] = None


class ApprovalRecordInfo(BaseSchema):
    id: int
    project_id: int
    stage: ApprovalStage
    approver_id: int
    status: ApprovalStatus = ApprovalStatus.PENDING
    comment: Optional[str] = None
    submitted_at: Optional[datetime] = None
    approved_at: Optional[datetime] = None
    reminder_count: int = 0
    last_reminded_at: Optional[datetime] = None


class GasSupplierBase(BaseSchema):
    name: str
    code: str
    contact_person: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    rating: int = 3
    is_active: bool = True


class GasSupplierCreate(GasSupplierBase):
    pass


class GasSupplierInfo(GasSupplierBase):
    id: int
    created_at: Optional[datetime] = None


class GasInventoryBase(BaseSchema):
    storage_point: str
    current_volume: Decimal
    min_threshold: Decimal
    max_capacity: Decimal


class GasInventoryCreate(GasInventoryBase):
    pass


class GasInventoryInfo(GasInventoryBase):
    id: int
    last_updated: Optional[datetime] = None


class GasPurchasePlanBase(BaseSchema):
    plan_month: Optional[str] = None
    predicted_demand: Decimal
    current_inventory: Decimal
    safety_stock: Decimal
    planned_volume: Decimal
    supplier_id: Optional[int] = None
    unit_price: Optional[Decimal] = None
    total_amount: Optional[Decimal] = None


class GasPurchasePlanCreate(GasPurchasePlanBase):
    pass


class GasPurchasePlanInfo(GasPurchasePlanBase):
    id: int
    plan_no: str
    status: PurchaseStatus = PurchaseStatus.DRAFT
    approver_id: Optional[int] = None
    approved_at: Optional[datetime] = None
    ordered_at: Optional[datetime] = None
    shipped_at: Optional[datetime] = None
    delivered_volume: Decimal = Decimal("0")
    delivered_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    created_at: Optional[datetime] = None


class GasPurchasePlanListResponse(PaginationResponse):
    items: List[GasPurchasePlanInfo]


class DailyReportInfo(BaseSchema):
    id: int
    report_date: date
    area_id: Optional[int] = None
    area_name: Optional[str] = None
    total_gas_volume: Decimal = Decimal("0")
    peak_hour_volume: Decimal = Decimal("0")
    leak_count: int = 0
    leak_resolved: int = 0
    leak_detection_rate: Decimal = Decimal("0")
    work_order_count: int = 0
    work_order_completed: int = 0
    avg_response_minutes: Decimal = Decimal("0")
    avg_resolution_minutes: Decimal = Decimal("0")
    complaint_count: int = 0
    complaint_rate: Decimal = Decimal("0")
    new_connection_count: int = 0
    overdue_bill_count: int = 0
    revenue: Decimal = Decimal("0")
    created_at: Optional[datetime] = None


class DailyReportListResponse(PaginationResponse):
    items: List[DailyReportInfo]


class NotificationInfo(BaseSchema):
    id: int
    user_id: int
    type: NotificationType
    title: str
    content: Optional[str] = None
    related_id: Optional[int] = None
    related_type: Optional[str] = None
    is_read: bool = False
    read_at: Optional[datetime] = None
    created_at: Optional[datetime] = None


class NotificationListResponse(PaginationResponse):
    items: List[NotificationInfo]
