from sqlalchemy import (
    Column, Integer, String, Float, Boolean, DateTime, Text,
    ForeignKey, Index, UniqueConstraint, CheckConstraint,
    BigInteger, Numeric, Date, Time, JSON, Enum as SAEnum
)
from sqlalchemy.orm import relationship
from sqlalchemy.dialects.postgresql import UUID, TIMESTAMP, ARRAY
from datetime import datetime, date
from decimal import Decimal
import enum
from app.database import Base


class UserRole(str, enum.Enum):
    ADMIN = "admin"
    DISPATCHER = "dispatcher"
    MAINTENANCE = "maintenance"
    SAFETY_INSPECTOR = "safety_inspector"
    DESIGNER = "designer"
    ENGINEER = "engineer"
    COLLECTOR = "collector"
    SUPPLIER = "supplier"
    RESIDENT = "resident"
    AREA_MANAGER = "area_manager"


class WorkOrderStatus(str, enum.Enum):
    PENDING = "pending"
    ASSIGNED = "assigned"
    ACCEPTED = "accepted"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    ESCALATED = "escalated"


class WorkOrderType(str, enum.Enum):
    LEAK_REPAIR = "leak_repair"
    MAINTENANCE = "maintenance"
    INSPECTION = "inspection"
    RESIDENT_REPORT = "resident_report"
    CONSTRUCTION = "construction"
    GAS_RESTORATION = "gas_restoration"


class WarningLevel(str, enum.Enum):
    LEVEL_1 = "level_1"
    LEVEL_2 = "level_2"
    LEVEL_3 = "level_3"
    LEVEL_4 = "level_4"


class ApprovalStatus(str, enum.Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    ESCALATED = "escalated"


class ApprovalStage(str, enum.Enum):
    SAFETY = "safety"
    DESIGN = "design"
    ENGINEERING = "engineering"
    FINAL = "final"


class SensorType(str, enum.Enum):
    PRESSURE = "pressure"
    FLOW = "flow"
    LEAK = "leak"
    TEMPERATURE = "temperature"


class BillStatus(str, enum.Enum):
    UNPAID = "unpaid"
    PARTIAL = "partial"
    PAID = "paid"
    OVERDUE = "overdue"
    SUSPENDED = "suspended"


class GasStatus(str, enum.Enum):
    NORMAL = "normal"
    RESTRICTED = "restricted"
    SUSPENDED = "suspended"


class PurchaseStatus(str, enum.Enum):
    DRAFT = "draft"
    APPROVED = "approved"
    ORDERED = "ordered"
    SHIPPED = "shipped"
    DELIVERED = "delivered"
    COMPLETED = "completed"


class NotificationType(str, enum.Enum):
    WARNING = "warning"
    WORK_ORDER = "work_order"
    APPROVAL = "approval"
    BILL = "bill"
    MAINTENANCE = "maintenance"
    SYSTEM = "system"


class User(Base):
    __tablename__ = "users"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    username = Column(String(50), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    real_name = Column(String(50))
    phone = Column(String(20), index=True)
    email = Column(String(100))
    role = Column(SAEnum(UserRole), nullable=False, index=True)
    area_id = Column(BigInteger, ForeignKey("areas.id"), index=True)
    team_id = Column(BigInteger, ForeignKey("maintenance_teams.id"), index=True)
    is_active = Column(Boolean, default=True)
    last_login = Column(TIMESTAMP(timezone=True))
    created_at = Column(TIMESTAMP(timezone=True), default=datetime.utcnow)
    updated_at = Column(TIMESTAMP(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)

    area = relationship("Area", foreign_keys=[area_id], back_populates="users")
    team = relationship("MaintenanceTeam", foreign_keys=[team_id], back_populates="members")
    owned_work_orders = relationship("WorkOrder", foreign_keys="WorkOrder.owner_id", back_populates="owner")
    assigned_work_orders = relationship("WorkOrder", foreign_keys="WorkOrder.assignee_id", back_populates="assignee")
    notifications = relationship("Notification", back_populates="user")
    resident_account = relationship("ResidentAccount", uselist=False, back_populates="user")
    approvals = relationship("ApprovalRecord", back_populates="approver")


class Area(Base):
    __tablename__ = "areas"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False)
    code = Column(String(20), unique=True, nullable=False, index=True)
    parent_id = Column(BigInteger, ForeignKey("areas.id"), index=True)
    level = Column(Integer, default=1)
    manager_id = Column(BigInteger, ForeignKey("users.id"))
    longitude = Column(Numeric(10, 7))
    latitude = Column(Numeric(10, 7))
    description = Column(Text)
    created_at = Column(TIMESTAMP(timezone=True), default=datetime.utcnow)

    parent = relationship("Area", remote_side=[id], backref="children")
    users = relationship("User", foreign_keys="User.area_id", back_populates="area")
    pressure_stations = relationship("PressureStation", back_populates="area")
    resident_accounts = relationship("ResidentAccount", back_populates="area")
    maintenance_teams = relationship("MaintenanceTeam", back_populates="area")


class MaintenanceTeam(Base):
    __tablename__ = "maintenance_teams"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False)
    area_id = Column(BigInteger, ForeignKey("areas.id"), nullable=False, index=True)
    leader_id = Column(BigInteger, ForeignKey("users.id"))
    current_load = Column(Integer, default=0)
    max_capacity = Column(Integer, default=10)
    status = Column(String(20), default="active")
    longitude = Column(Numeric(10, 7))
    latitude = Column(Numeric(10, 7))
    contact_phone = Column(String(20))
    created_at = Column(TIMESTAMP(timezone=True), default=datetime.utcnow)

    area = relationship("Area", back_populates="maintenance_teams")
    members = relationship("User", foreign_keys="User.team_id", back_populates="team")


class PressureStation(Base):
    __tablename__ = "pressure_stations"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False)
    code = Column(String(50), unique=True, nullable=False, index=True)
    area_id = Column(BigInteger, ForeignKey("areas.id"), nullable=False, index=True)
    inlet_pressure_min = Column(Numeric(10, 4))
    inlet_pressure_max = Column(Numeric(10, 4))
    outlet_pressure_set = Column(Numeric(10, 4), nullable=False)
    outlet_pressure_min = Column(Numeric(10, 4))
    outlet_pressure_max = Column(Numeric(10, 4))
    capacity = Column(Numeric(15, 4))
    longitude = Column(Numeric(10, 7))
    latitude = Column(Numeric(10, 7))
    status = Column(String(20), default="normal")
    created_at = Column(TIMESTAMP(timezone=True), default=datetime.utcnow)

    area = relationship("Area", back_populates="pressure_stations")
    sensors = relationship("Sensor", back_populates="pressure_station")
    control_logs = relationship("ControlLog", back_populates="pressure_station")


class Sensor(Base):
    __tablename__ = "sensors"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    code = Column(String(50), unique=True, nullable=False, index=True)
    name = Column(String(100), nullable=False)
    type = Column(SAEnum(SensorType), nullable=False, index=True)
    pressure_station_id = Column(BigInteger, ForeignKey("pressure_stations.id"), index=True)
    area_id = Column(BigInteger, ForeignKey("areas.id"), index=True)
    longitude = Column(Numeric(10, 7))
    latitude = Column(Numeric(10, 7))
    threshold_min = Column(Numeric(15, 4))
    threshold_max = Column(Numeric(15, 4))
    leak_threshold = Column(Numeric(10, 4))
    status = Column(String(20), default="online")
    last_reading = Column(TIMESTAMP(timezone=True))
    created_at = Column(TIMESTAMP(timezone=True), default=datetime.utcnow)

    pressure_station = relationship("PressureStation", back_populates="sensors")
    area = relationship("Area")
    readings = relationship("SensorReading", back_populates="sensor")
    warnings = relationship("LeakWarning", back_populates="sensor")


class SensorReading(Base):
    __tablename__ = "sensor_readings"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    sensor_id = Column(BigInteger, ForeignKey("sensors.id"), nullable=False, index=True)
    value = Column(Numeric(15, 4), nullable=False)
    reading_time = Column(TIMESTAMP(timezone=True), nullable=False, index=True)
    is_anomaly = Column(Boolean, default=False)
    created_at = Column(TIMESTAMP(timezone=True), default=datetime.utcnow)

    sensor = relationship("Sensor", back_populates="readings")
    __table_args__ = (
        Index("ix_sensor_readings_sensor_time", "sensor_id", "reading_time", postgresql_using="brin"),
    )


class ControlLog(Base):
    __tablename__ = "control_logs"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    pressure_station_id = Column(BigInteger, ForeignKey("pressure_stations.id"), nullable=False, index=True)
    dispatcher_id = Column(BigInteger, ForeignKey("users.id"), index=True)
    old_outlet_pressure = Column(Numeric(10, 4), nullable=False)
    new_outlet_pressure = Column(Numeric(10, 4), nullable=False)
    reason = Column(Text)
    is_auto = Column(Boolean, default=True)
    trigger_condition = Column(JSON)
    created_at = Column(TIMESTAMP(timezone=True), default=datetime.utcnow, index=True)

    pressure_station = relationship("PressureStation", back_populates="control_logs")
    dispatcher = relationship("User", foreign_keys=[dispatcher_id])


class LeakWarning(Base):
    __tablename__ = "leak_warnings"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    sensor_id = Column(BigInteger, ForeignKey("sensors.id"), nullable=False, index=True)
    area_id = Column(BigInteger, ForeignKey("areas.id"), index=True)
    level = Column(SAEnum(WarningLevel), nullable=False, index=True)
    concentration = Column(Numeric(10, 4))
    longitude = Column(Numeric(10, 7))
    latitude = Column(Numeric(10, 7))
    description = Column(Text)
    first_detected = Column(TIMESTAMP(timezone=True), nullable=False)
    resolved_at = Column(TIMESTAMP(timezone=True))
    status = Column(String(20), default="active", index=True)
    created_at = Column(TIMESTAMP(timezone=True), default=datetime.utcnow)

    sensor = relationship("Sensor", back_populates="warnings")
    area = relationship("Area")
    work_orders = relationship("WorkOrder", back_populates="warning")


class WorkOrder(Base):
    __tablename__ = "work_orders"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    order_no = Column(String(32), unique=True, nullable=False, index=True)
    type = Column(SAEnum(WorkOrderType), nullable=False, index=True)
    status = Column(SAEnum(WorkOrderStatus), nullable=False, default=WorkOrderStatus.PENDING, index=True)
    warning_id = Column(BigInteger, ForeignKey("leak_warnings.id"), index=True)
    resident_report_id = Column(BigInteger, ForeignKey("resident_reports.id"), index=True)
    area_id = Column(BigInteger, ForeignKey("areas.id"), index=True)
    owner_id = Column(BigInteger, ForeignKey("users.id"), index=True)
    assignee_id = Column(BigInteger, ForeignKey("users.id"), index=True)
    team_id = Column(BigInteger, ForeignKey("maintenance_teams.id"), index=True)
    priority = Column(Integer, default=3)
    title = Column(String(200), nullable=False)
    description = Column(Text)
    longitude = Column(Numeric(10, 7))
    latitude = Column(Numeric(10, 7))
    level = Column(SAEnum(WarningLevel))
    assigned_at = Column(TIMESTAMP(timezone=True))
    accepted_at = Column(TIMESTAMP(timezone=True))
    started_at = Column(TIMESTAMP(timezone=True))
    completed_at = Column(TIMESTAMP(timezone=True))
    response_minutes = Column(Integer)
    resolution_minutes = Column(Integer)
    escalated_at = Column(TIMESTAMP(timezone=True))
    escalation_count = Column(Integer, default=0)
    is_cross_area = Column(Boolean, default=False)
    cross_area_from_area_id = Column(BigInteger, nullable=True)
    result = Column(Text)
    images = Column(JSON)
    created_at = Column(TIMESTAMP(timezone=True), default=datetime.utcnow, index=True)
    updated_at = Column(TIMESTAMP(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)

    warning = relationship("LeakWarning", back_populates="work_orders")
    resident_report = relationship("ResidentReport", back_populates="work_orders")
    area = relationship("Area")
    owner = relationship("User", foreign_keys=[owner_id], back_populates="owned_work_orders")
    assignee = relationship("User", foreign_keys=[assignee_id], back_populates="assigned_work_orders")
    team = relationship("MaintenanceTeam")
    assignments = relationship("WorkOrderAssignment", back_populates="work_order", cascade="all, delete-orphan")


class WorkOrderAssignment(Base):
    __tablename__ = "work_order_assignments"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    work_order_id = Column(BigInteger, ForeignKey("work_orders.id"), nullable=False, index=True)
    assignee_id = Column(BigInteger, ForeignKey("users.id"), nullable=False)
    assigned_by = Column(BigInteger, ForeignKey("users.id"))
    assigned_at = Column(TIMESTAMP(timezone=True), default=datetime.utcnow)
    accepted_at = Column(TIMESTAMP(timezone=True))
    rejected_at = Column(TIMESTAMP(timezone=True))
    rejection_reason = Column(Text)
    status = Column(String(20), default="pending")
    is_active = Column(Boolean, default=True)

    work_order = relationship("WorkOrder", back_populates="assignments")
    assignee = relationship("User", foreign_keys=[assignee_id])


class ResidentAccount(Base):
    __tablename__ = "resident_accounts"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    account_no = Column(String(32), unique=True, nullable=False, index=True)
    user_id = Column(BigInteger, ForeignKey("users.id"), unique=True, index=True)
    area_id = Column(BigInteger, ForeignKey("areas.id"), nullable=False, index=True)
    resident_name = Column(String(50), nullable=False)
    phone = Column(String(20))
    address = Column(String(300))
    longitude = Column(Numeric(10, 7))
    latitude = Column(Numeric(10, 7))
    meter_no = Column(String(50), unique=True, index=True)
    meter_reading = Column(Numeric(12, 4), default=0)
    last_reading_date = Column(Date)
    gas_status = Column(SAEnum(GasStatus), default=GasStatus.NORMAL, index=True)
    gas_restricted_at = Column(TIMESTAMP(timezone=True))
    tier_level = Column(Integer, default=1)
    created_at = Column(TIMESTAMP(timezone=True), default=datetime.utcnow)

    user = relationship("User", back_populates="resident_account")
    area = relationship("Area", back_populates="resident_accounts")
    bills = relationship("Bill", back_populates="account")
    meter_readings = relationship("MeterReading", back_populates="account")
    reports = relationship("ResidentReport", back_populates="account")


class MeterReading(Base):
    __tablename__ = "meter_readings"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    account_id = Column(BigInteger, ForeignKey("resident_accounts.id"), nullable=False, index=True)
    reading_value = Column(Numeric(12, 4), nullable=False)
    reading_date = Column(Date, nullable=False, index=True)
    is_estimated = Column(Boolean, default=False)
    reader_id = Column(BigInteger, ForeignKey("users.id"))
    created_at = Column(TIMESTAMP(timezone=True), default=datetime.utcnow)

    account = relationship("ResidentAccount", back_populates="meter_readings")


class ResidentReport(Base):
    __tablename__ = "resident_reports"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    report_no = Column(String(32), unique=True, nullable=False, index=True)
    account_id = Column(BigInteger, ForeignKey("resident_accounts.id"), nullable=False, index=True)
    area_id = Column(BigInteger, ForeignKey("areas.id"), index=True)
    report_type = Column(String(50), nullable=False, index=True)
    description = Column(Text)
    longitude = Column(Numeric(10, 7))
    latitude = Column(Numeric(10, 7))
    historical_match_count = Column(Integer, default=0)
    auto_diagnosis = Column(String(200))
    confidence_score = Column(Numeric(5, 2))
    status = Column(String(20), default="submitted", index=True)
    reported_at = Column(TIMESTAMP(timezone=True), default=datetime.utcnow)
    resolved_at = Column(TIMESTAMP(timezone=True))

    account = relationship("ResidentAccount", back_populates="reports")
    area = relationship("Area")
    work_orders = relationship("WorkOrder", back_populates="resident_report")


class GasPriceTier(Base):
    __tablename__ = "gas_price_tiers"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    tier = Column(Integer, nullable=False)
    name = Column(String(50), nullable=False)
    min_volume = Column(Numeric(12, 4), nullable=False)
    max_volume = Column(Numeric(12, 4))
    unit_price = Column(Numeric(10, 4), nullable=False)
    effective_date = Column(Date, nullable=False)
    is_active = Column(Boolean, default=True)
    __table_args__ = (
        UniqueConstraint("tier", "effective_date", name="uq_tier_date"),
    )


class Bill(Base):
    __tablename__ = "bills"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    bill_no = Column(String(32), unique=True, nullable=False, index=True)
    account_id = Column(BigInteger, ForeignKey("resident_accounts.id"), nullable=False, index=True)
    billing_month = Column(String(7), nullable=False, index=True)
    billing_start_date = Column(Date, nullable=False)
    billing_end_date = Column(Date, nullable=False)
    previous_reading = Column(Numeric(12, 4), nullable=False)
    current_reading = Column(Numeric(12, 4), nullable=False)
    total_volume = Column(Numeric(12, 4), nullable=False)
    tier1_volume = Column(Numeric(12, 4), default=0)
    tier2_volume = Column(Numeric(12, 4), default=0)
    tier3_volume = Column(Numeric(12, 4), default=0)
    tier1_amount = Column(Numeric(12, 4), default=0)
    tier2_amount = Column(Numeric(12, 4), default=0)
    tier3_amount = Column(Numeric(12, 4), default=0)
    surcharge = Column(Numeric(12, 4), default=0)
    discount = Column(Numeric(12, 4), default=0)
    total_amount = Column(Numeric(12, 4), nullable=False)
    paid_amount = Column(Numeric(12, 4), default=0)
    status = Column(SAEnum(BillStatus), default=BillStatus.UNPAID, index=True)
    due_date = Column(Date, nullable=False)
    paid_at = Column(TIMESTAMP(timezone=True))
    restriction_issued = Column(Boolean, default=False)
    restricted_at = Column(TIMESTAMP(timezone=True))
    collector_id = Column(BigInteger, ForeignKey("users.id"), index=True)
    created_at = Column(TIMESTAMP(timezone=True), default=datetime.utcnow)

    account = relationship("ResidentAccount", back_populates="bills")
    collector = relationship("User", foreign_keys=[collector_id])
    payments = relationship("Payment", back_populates="bill")


class Payment(Base):
    __tablename__ = "payments"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    payment_no = Column(String(32), unique=True, nullable=False)
    bill_id = Column(BigInteger, ForeignKey("bills.id"), nullable=False, index=True)
    account_id = Column(BigInteger, ForeignKey("resident_accounts.id"), index=True)
    amount = Column(Numeric(12, 4), nullable=False)
    payment_method = Column(String(20))
    transaction_no = Column(String(100))
    paid_at = Column(TIMESTAMP(timezone=True), default=datetime.utcnow, index=True)

    bill = relationship("Bill", back_populates="payments")
    account = relationship("ResidentAccount")


class EngineeringProject(Base):
    __tablename__ = "engineering_projects"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    project_no = Column(String(32), unique=True, nullable=False, index=True)
    name = Column(String(200), nullable=False)
    area_id = Column(BigInteger, ForeignKey("areas.id"), index=True)
    applicant_id = Column(BigInteger, ForeignKey("users.id"))
    project_type = Column(String(50))
    scope = Column(Text)
    budget = Column(Numeric(15, 4))
    planned_start_date = Column(Date)
    planned_end_date = Column(Date)
    current_stage = Column(SAEnum(ApprovalStage), default=ApprovalStage.SAFETY, index=True)
    approval_status = Column(SAEnum(ApprovalStatus), default=ApprovalStatus.PENDING, index=True)
    construction_team_id = Column(BigInteger, ForeignKey("maintenance_teams.id"))
    actual_start_date = Column(Date)
    actual_end_date = Column(Date)
    status = Column(String(20), default="draft")
    created_at = Column(TIMESTAMP(timezone=True), default=datetime.utcnow)

    area = relationship("Area")
    applicant = relationship("User", foreign_keys=[applicant_id])
    construction_team = relationship("MaintenanceTeam", foreign_keys=[construction_team_id])
    approval_records = relationship("ApprovalRecord", back_populates="project")


class ApprovalRecord(Base):
    __tablename__ = "approval_records"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    project_id = Column(BigInteger, ForeignKey("engineering_projects.id"), nullable=False, index=True)
    stage = Column(SAEnum(ApprovalStage), nullable=False, index=True)
    approver_id = Column(BigInteger, ForeignKey("users.id"), nullable=False)
    status = Column(SAEnum(ApprovalStatus), default=ApprovalStatus.PENDING, index=True)
    comment = Column(Text)
    submitted_at = Column(TIMESTAMP(timezone=True), default=datetime.utcnow)
    approved_at = Column(TIMESTAMP(timezone=True))
    reminder_count = Column(Integer, default=0)
    last_reminded_at = Column(TIMESTAMP(timezone=True))

    project = relationship("EngineeringProject", back_populates="approval_records")
    approver = relationship("User", back_populates="approvals")


class GasSupplier(Base):
    __tablename__ = "gas_suppliers"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    name = Column(String(200), nullable=False)
    code = Column(String(50), unique=True, nullable=False, index=True)
    contact_person = Column(String(50))
    phone = Column(String(20))
    address = Column(String(300))
    rating = Column(Integer, default=3)
    is_active = Column(Boolean, default=True)
    created_at = Column(TIMESTAMP(timezone=True), default=datetime.utcnow)


class GasInventory(Base):
    __tablename__ = "gas_inventory"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    storage_point = Column(String(100), nullable=False)
    current_volume = Column(Numeric(15, 4), nullable=False)
    min_threshold = Column(Numeric(15, 4), nullable=False)
    max_capacity = Column(Numeric(15, 4), nullable=False)
    last_updated = Column(TIMESTAMP(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)


class GasPurchasePlan(Base):
    __tablename__ = "gas_purchase_plans"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    plan_no = Column(String(32), unique=True, nullable=False, index=True)
    plan_month = Column(String(7), index=True)
    predicted_demand = Column(Numeric(15, 4), nullable=False)
    current_inventory = Column(Numeric(15, 4), nullable=False)
    safety_stock = Column(Numeric(15, 4), nullable=False)
    planned_volume = Column(Numeric(15, 4), nullable=False)
    supplier_id = Column(BigInteger, ForeignKey("gas_suppliers.id"), index=True)
    unit_price = Column(Numeric(12, 4))
    total_amount = Column(Numeric(15, 4))
    status = Column(SAEnum(PurchaseStatus), default=PurchaseStatus.DRAFT, index=True)
    approver_id = Column(BigInteger, ForeignKey("users.id"))
    approved_at = Column(TIMESTAMP(timezone=True))
    ordered_at = Column(TIMESTAMP(timezone=True))
    shipped_at = Column(TIMESTAMP(timezone=True))
    delivered_volume = Column(Numeric(15, 4), default=0)
    delivered_at = Column(TIMESTAMP(timezone=True))
    completed_at = Column(TIMESTAMP(timezone=True))
    created_at = Column(TIMESTAMP(timezone=True), default=datetime.utcnow)

    supplier = relationship("GasSupplier")
    approver = relationship("User", foreign_keys=[approver_id])


class DailyReport(Base):
    __tablename__ = "daily_reports"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    report_date = Column(Date, nullable=False, index=True)
    area_id = Column(BigInteger, ForeignKey("areas.id"), index=True)
    total_gas_volume = Column(Numeric(15, 4), default=0)
    peak_hour_volume = Column(Numeric(15, 4), default=0)
    leak_count = Column(Integer, default=0)
    leak_resolved = Column(Integer, default=0)
    leak_detection_rate = Column(Numeric(5, 2), default=0)
    work_order_count = Column(Integer, default=0)
    work_order_completed = Column(Integer, default=0)
    avg_response_minutes = Column(Numeric(10, 2), default=0)
    avg_resolution_minutes = Column(Numeric(10, 2), default=0)
    complaint_count = Column(Integer, default=0)
    complaint_rate = Column(Numeric(5, 2), default=0)
    new_connection_count = Column(Integer, default=0)
    overdue_bill_count = Column(Integer, default=0)
    revenue = Column(Numeric(15, 4), default=0)
    created_at = Column(TIMESTAMP(timezone=True), default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("report_date", "area_id", name="uq_report_date_area"),
    )


class Notification(Base):
    __tablename__ = "notifications"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, ForeignKey("users.id"), nullable=False, index=True)
    type = Column(SAEnum(NotificationType), nullable=False, index=True)
    title = Column(String(200), nullable=False)
    content = Column(Text)
    related_id = Column(BigInteger)
    related_type = Column(String(50))
    is_read = Column(Boolean, default=False, index=True)
    read_at = Column(TIMESTAMP(timezone=True))
    created_at = Column(TIMESTAMP(timezone=True), default=datetime.utcnow, index=True)

    user = relationship("User", back_populates="notifications")


class LoadPrediction(Base):
    __tablename__ = "load_predictions"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    prediction_date = Column(Date, nullable=False, index=True)
    prediction_hour = Column(Integer, nullable=False)
    area_id = Column(BigInteger, ForeignKey("areas.id"), index=True)
    predicted_volume = Column(Numeric(15, 4), nullable=False)
    actual_volume = Column(Numeric(15, 4))
    model_version = Column(String(20))
    created_at = Column(TIMESTAMP(timezone=True), default=datetime.utcnow)

    __table_args__ = (
        Index("ix_prediction_date_hour_area", "prediction_date", "prediction_hour", "area_id"),
    )
