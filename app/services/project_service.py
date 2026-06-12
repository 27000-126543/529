from typing import Optional, List
from datetime import datetime, date, timedelta
from decimal import Decimal
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import and_, func
from app.models import (
    EngineeringProject, ApprovalRecord, ApprovalStage, ApprovalStatus,
    MaintenanceTeam, ResidentReport, WorkOrderType
)
from app.utils.security import generate_order_no
from app.config import settings
from app.services import work_order_service


async def create_project(
    db: AsyncSession,
    data,
    applicant_id: Optional[int] = None
) -> EngineeringProject:
    project = EngineeringProject(
        project_no=generate_order_no("EP"),
        name=data.name,
        area_id=data.area_id,
        applicant_id=applicant_id,
        project_type=data.project_type,
        scope=data.scope,
        budget=data.budget,
        planned_start_date=data.planned_start_date,
        planned_end_date=data.planned_end_date,
        current_stage=ApprovalStage.SAFETY,
        approval_status=ApprovalStatus.PENDING,
        status="pending_approval"
    )
    db.add(project)
    await db.flush()

    await create_approval_records(db, project)
    return project


async def create_approval_records(db: AsyncSession, project: EngineeringProject):
    stages = [
        ApprovalStage.SAFETY,
        ApprovalStage.DESIGN,
        ApprovalStage.ENGINEERING,
        ApprovalStage.FINAL
    ]
    for stage in stages:
        record = ApprovalRecord(
            project_id=project.id,
            stage=stage,
            approver_id=0,
            status=ApprovalStatus.PENDING if stage == ApprovalStage.SAFETY else ApprovalStatus.PENDING
        )
        db.add(record)
    await db.flush()


async def approve_stage(
    db: AsyncSession,
    project_id: int,
    stage: ApprovalStage,
    approver_id: int,
    status: ApprovalStatus,
    comment: Optional[str] = None
) -> ApprovalRecord:
    result = await db.execute(
        select(ApprovalRecord).where(
            and_(
                ApprovalRecord.project_id == project_id,
                ApprovalRecord.stage == stage
            )
        )
    )
    record = result.scalar_one_or_none()
    if not record:
        raise ValueError(f"Approval record not found for stage: {stage}")

    record.approver_id = approver_id
    record.status = status
    record.comment = comment
    record.approved_at = datetime.utcnow()
    await db.flush()

    if status == ApprovalStatus.APPROVED:
        project_result = await db.execute(
            select(EngineeringProject).where(EngineeringProject.id == project_id)
        )
        project = project_result.scalar_one_or_none()
        if project:
            stages_order = [
                ApprovalStage.SAFETY,
                ApprovalStage.DESIGN,
                ApprovalStage.ENGINEERING,
                ApprovalStage.FINAL
            ]
            current_idx = stages_order.index(stage)
            if current_idx < len(stages_order) - 1:
                project.current_stage = stages_order[current_idx + 1]
                project.approval_status = ApprovalStatus.PENDING
            else:
                project.approval_status = ApprovalStatus.APPROVED
                project.status = "approved"
                await assign_construction_team(db, project)

    elif status == ApprovalStatus.REJECTED:
        project_result = await db.execute(
            select(EngineeringProject).where(EngineeringProject.id == project_id)
        )
        project = project_result.scalar_one_or_none()
        if project:
            project.approval_status = ApprovalStatus.REJECTED
            project.status = "rejected"

    await db.flush()
    return record


async def assign_construction_team(
    db: AsyncSession,
    project: EngineeringProject
):
    team_result = await db.execute(
        select(MaintenanceTeam).where(
            and_(
                MaintenanceTeam.status == "active",
                MaintenanceTeam.area_id == project.area_id if project.area_id else True
            )
        ).order_by(MaintenanceTeam.current_load.asc()).limit(1)
    )
    team = team_result.scalar_one_or_none()
    if team:
        project.construction_team_id = team.id
        team.current_load += 1
        project.status = "scheduled"
        await db.flush()


async def check_and_remind_approvals(db: AsyncSession) -> int:
    threshold = datetime.utcnow() - timedelta(hours=settings.APPROVAL_TIMEOUT_HOURS)
    result = await db.execute(
        select(ApprovalRecord).where(
            and_(
                ApprovalRecord.status == ApprovalStatus.PENDING,
                ApprovalRecord.submitted_at < threshold
            )
        )
    )
    records = result.scalars().all()

    reminded = 0
    for rec in records:
        last_remind = rec.last_reminded_at or rec.submitted_at
        if last_remind and (datetime.utcnow() - last_remind).total_seconds() >= 3600:
            rec.reminder_count += 1
            rec.last_reminded_at = datetime.utcnow()
            reminded += 1

    await db.flush()
    return reminded


async def auto_diagnose_report(
    db: AsyncSession,
    report: ResidentReport
) -> tuple:
    type_keywords = {
        "无气": "检查阀门状态和近期维修记录",
        "没气": "检查阀门状态和近期维修记录",
        "异味": "疑似燃气泄漏，优先安排检漏",
        "臭味": "疑似燃气泄漏，优先安排检漏",
        "火小": "检查压力和管道堵塞情况",
        "漏气": "疑似燃气泄漏，立即上门检修",
    }

    keywords = list(type_keywords.keys())
    matched_keyword = None
    for kw in keywords:
        if kw in (report.description or "") or kw in (report.report_type or ""):
            matched_keyword = kw
            break

    same_area_count = 0
    if report.area_id:
        start_time = datetime.utcnow() - timedelta(days=7)
        count_result = await db.execute(
            select(func.count(ResidentReport.id)).where(
                and_(
                    ResidentReport.area_id == report.area_id,
                    ResidentReport.report_type == report.report_type,
                    ResidentReport.reported_at >= start_time
                )
            )
        )
        same_area_count = count_result.scalar_one() or 0

    diagnosis = type_keywords.get(matched_keyword, "常规上门检查")
    confidence = min(95, 50 + same_area_count * 10)

    if same_area_count >= 3:
        diagnosis = f"区域集中问题：{diagnosis}，建议区域巡检"
        confidence = min(95, confidence + 10)

    return diagnosis, Decimal(str(confidence)), same_area_count


async def create_work_order_from_report(
    db: AsyncSession,
    report: ResidentReport,
    diagnosis: str
):
    from app.schemas.sensor import WorkOrderCreate

    wo_type = WorkOrderType.MAINTENANCE
    if "泄漏" in diagnosis or "检漏" in diagnosis:
        wo_type = WorkOrderType.LEAK_REPAIR
    elif "区域巡检" in diagnosis:
        wo_type = WorkOrderType.INSPECTION

    wo_data = WorkOrderCreate(
        type=wo_type,
        title=f"居民报修-{report.report_type}",
        description=f"用户描述：{report.description}\n自动诊断：{diagnosis}",
        resident_report_id=report.id,
        area_id=report.area_id,
        longitude=report.longitude,
        latitude=report.latitude,
        priority=2 if wo_type == WorkOrderType.LEAK_REPAIR else 3
    )

    wo = await work_order_service.create_work_order(db, wo_data)
    report.status = "processing"
    await db.flush()
    return wo
