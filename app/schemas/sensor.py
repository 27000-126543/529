from typing import Optional, List
from datetime import datetime, date
from decimal import Decimal
from pydantic import Field
from app.schemas.common import BaseSchema, PaginationResponse
from app.models import SensorType, WorkOrderType, WorkOrderStatus, WarningLevel


class SensorBase(BaseSchema):
    code: str
    name: str
    type: SensorType
    pressure_station_id: Optional[int] = None
    area_id: Optional[int] = None
    longitude: Optional[Decimal] = None
    latitude: Optional[Decimal] = None
    threshold_min: Optional[Decimal] = None
    threshold_max: Optional[Decimal] = None
    leak_threshold: Optional[Decimal] = None


class SensorCreate(SensorBase):
    pass


class SensorUpdate(BaseSchema):
    name: Optional[str] = None
    type: Optional[SensorType] = None
    pressure_station_id: Optional[int] = None
    area_id: Optional[int] = None
    longitude: Optional[Decimal] = None
    latitude: Optional[Decimal] = None
    threshold_min: Optional[Decimal] = None
    threshold_max: Optional[Decimal] = None
    leak_threshold: Optional[Decimal] = None
    status: Optional[str] = None


class SensorInfo(SensorBase):
    id: int
    status: str = "online"
    last_reading: Optional[datetime] = None
    created_at: Optional[datetime] = None


class SensorListResponse(PaginationResponse):
    items: List[SensorInfo]


class SensorReadingCreate(BaseSchema):
    sensor_id: int
    value: Decimal
    reading_time: datetime


class SensorReadingBatch(BaseSchema):
    readings: List[SensorReadingCreate]


class SensorReadingInfo(BaseSchema):
    id: int
    sensor_id: int
    value: Decimal
    reading_time: datetime
    is_anomaly: bool
    created_at: Optional[datetime] = None


class SensorReadingListResponse(PaginationResponse):
    items: List[SensorReadingInfo]


class PressureStationBase(BaseSchema):
    name: str
    code: str
    area_id: int
    inlet_pressure_min: Optional[Decimal] = None
    inlet_pressure_max: Optional[Decimal] = None
    outlet_pressure_set: Decimal
    outlet_pressure_min: Optional[Decimal] = None
    outlet_pressure_max: Optional[Decimal] = None
    capacity: Optional[Decimal] = None
    longitude: Optional[Decimal] = None
    latitude: Optional[Decimal] = None


class PressureStationCreate(PressureStationBase):
    pass


class PressureStationUpdate(BaseSchema):
    name: Optional[str] = None
    area_id: Optional[int] = None
    inlet_pressure_min: Optional[Decimal] = None
    inlet_pressure_max: Optional[Decimal] = None
    outlet_pressure_set: Optional[Decimal] = None
    outlet_pressure_min: Optional[Decimal] = None
    outlet_pressure_max: Optional[Decimal] = None
    capacity: Optional[Decimal] = None
    longitude: Optional[Decimal] = None
    latitude: Optional[Decimal] = None
    status: Optional[str] = None


class PressureStationInfo(PressureStationBase):
    id: int
    status: str = "normal"
    created_at: Optional[datetime] = None


class PressureStationListResponse(PaginationResponse):
    items: List[PressureStationInfo]


class ControlLogCreate(BaseSchema):
    pressure_station_id: int
    dispatcher_id: Optional[int] = None
    old_outlet_pressure: Decimal
    new_outlet_pressure: Decimal
    reason: Optional[str] = None
    is_auto: bool = True
    trigger_condition: Optional[dict] = None


class ControlLogInfo(BaseSchema):
    id: int
    pressure_station_id: int
    dispatcher_id: Optional[int] = None
    old_outlet_pressure: Decimal
    new_outlet_pressure: Decimal
    reason: Optional[str] = None
    is_auto: bool
    trigger_condition: Optional[dict] = None
    created_at: Optional[datetime] = None


class ControlLogListResponse(PaginationResponse):
    items: List[ControlLogInfo]


class LeakWarningBase(BaseSchema):
    sensor_id: int
    area_id: Optional[int] = None
    level: WarningLevel
    concentration: Optional[Decimal] = None
    longitude: Optional[Decimal] = None
    latitude: Optional[Decimal] = None
    description: Optional[str] = None


class LeakWarningCreate(LeakWarningBase):
    first_detected: datetime


class LeakWarningUpdate(BaseSchema):
    status: Optional[str] = None
    resolved_at: Optional[datetime] = None
    description: Optional[str] = None


class LeakWarningInfo(LeakWarningBase):
    id: int
    first_detected: datetime
    resolved_at: Optional[datetime] = None
    status: str = "active"
    created_at: Optional[datetime] = None


class LeakWarningListResponse(PaginationResponse):
    items: List[LeakWarningInfo]


class WorkOrderBase(BaseSchema):
    type: WorkOrderType
    title: str
    description: Optional[str] = None
    warning_id: Optional[int] = None
    resident_report_id: Optional[int] = None
    area_id: Optional[int] = None
    team_id: Optional[int] = None
    priority: int = 3
    longitude: Optional[Decimal] = None
    latitude: Optional[Decimal] = None
    level: Optional[WarningLevel] = None


class WorkOrderCreate(WorkOrderBase):
    pass


class WorkOrderUpdate(BaseSchema):
    status: Optional[WorkOrderStatus] = None
    assignee_id: Optional[int] = None
    result: Optional[str] = None
    images: Optional[List] = None


class WorkOrderInfo(WorkOrderBase):
    id: int
    order_no: str
    status: WorkOrderStatus
    owner_id: Optional[int] = None
    assignee_id: Optional[int] = None
    assigned_at: Optional[datetime] = None
    accepted_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    response_minutes: Optional[int] = None
    resolution_minutes: Optional[int] = None
    escalated_at: Optional[datetime] = None
    escalation_count: int = 0
    result: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class WorkOrderListResponse(PaginationResponse):
    items: List[WorkOrderInfo]


class WorkOrderAssign(BaseSchema):
    assignee_id: Optional[int] = None
    team_id: Optional[int] = None


class WorkOrderAccept(BaseSchema):
    pass


class WorkOrderComplete(BaseSchema):
    result: str
    images: Optional[List] = None
