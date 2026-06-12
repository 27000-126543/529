from typing import Optional
from datetime import datetime, date, timedelta
from decimal import Decimal
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import and_, func
from app.models import (
    EngineeringProject, ApprovalRecord, ApprovalStage, ApprovalStatus,
    MaintenanceTeam, ResidentReport, WorkOrderType,
    User, UserRole, NotificationType
)
from app.utils.security import generate_order_no
from app.config import settings
from app.services import work_order_service, notification_service


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
    stages_order = [
        ApprovalStage.SAFETY,
        ApprovalStage.DESIGN,
        ApprovalStage.ENGINEERING,
        ApprovalStage.FINAL
    ]

    safety_inspector_result = await db.execute(
        select(User.id).where(
            and_(
                User.role == UserRole.SAFETY_INSPECTOR,
                User.is_active == True
            )
        ).limit(1)
    )
    safety_inspector_id = safety_inspector_result.scalar_one_or_none()

    for stage in stages_order:
        approver_id = 0
        if stage == ApprovalStage.SAFETY and safety_inspector_id:
            approver_id = safety_inspector_id

        record = ApprovalRecord(
            project_id=project.id,
            stage=stage,
            approver_id=approver_id,
            status=ApprovalStatus.PENDING,
            submitted_at=datetime.utcnow()
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
    stages_order = [
        ApprovalStage.SAFETY,
        ApprovalStage.DESIGN,
        ApprovalStage.ENGINEERING,
        ApprovalStage.FINAL
    ]

    project_result = await db.execute(
        select(EngineeringProject).where(EngineeringProject.id == project_id)
    )
    project = project_result.scalar_one_or_none()
    if not project:
        raise ValueError("工程项目不存在")

    current_idx = stages_order.index(stage)
    if current_idx > 0:
        prev_stage = stages_order[current_idx - 1]
        prev_result = await db.execute(
            select(ApprovalRecord).where(
                and_(
                    ApprovalRecord.project_id == project_id,
                    ApprovalRecord.stage == prev_stage
                )
            )
        )
        prev_record = prev_result.scalar_one_or_none()
        if not prev_record or prev_record.status != ApprovalStatus.APPROVED:
            raise ValueError(f"上一阶段 {prev_stage.value} 未通过，不能审批当前阶段")

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
        raise ValueError(f"审批记录不存在: {stage}")

    if record.status != ApprovalStatus.PENDING:
        raise ValueError(f"当前阶段 {stage.value} 已被处理，不能重复审批")

    record.approver_id = approver_id
    record.status = status
    record.comment = comment
    record.approved_at = datetime.utcnow()
    await db.flush()

    stage_role_map = {
        ApprovalStage.SAFETY: UserRole.SAFETY_INSPECTOR,
        ApprovalStage.DESIGN: UserRole.DESIGNER,
        ApprovalStage.ENGINEERING: UserRole.ENGINEER,
        ApprovalStage.FINAL: [UserRole.DISPATCHER, UserRole.ADMIN]
    }

    if status == ApprovalStatus.APPROVED:
        current_idx = stages_order.index(stage)
        if current_idx < len(stages_order) - 1:
            next_stage = stages_order[current_idx + 1]
            project.current_stage = next_stage
            project.approval_status = ApprovalStatus.PENDING

            required_role = stage_role_map.get(next_stage)
            next_approver_id = 0

            if required_role:
                if isinstance(required_role, list):
                    role_result = await db.execute(
                        select(User.id).where(
                            and_(
                                User.role.in_(required_role),
                                User.is_active == True
                            )
                        ).limit(1)
                    )
                else:
                    role_result = await db.execute(
                        select(User.id).where(
                            and_(
                                User.role == required_role,
                                User.is_active == True
                            )
                        ).limit(1)
                    )
                found_id = role_result.scalar_one_or_none()
                if found_id:
                    next_approver_id = found_id

            next_record_result = await db.execute(
                select(ApprovalRecord).where(
                    and_(
                        ApprovalRecord.project_id == project_id,
                        ApprovalRecord.stage == next_stage
                    )
                )
            )
            next_record = next_record_result.scalar_one_or_none()
            if next_record:
                next_record.approver_id = next_approver_id
        else:
            project.approval_status = ApprovalStatus.APPROVED
            project.status = "approved"
            await assign_construction_team(db, project)

    elif status == ApprovalStatus.REJECTED:
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
        if not project.planned_start_date:
            project.actual_start_date = date.today() + timedelta(days=3)
        else:
            project.actual_start_date = project.planned_start_date
        project.status = "scheduled"
        team.current_load += 1
        await db.flush()


async def check_and_remind_approvals(db: AsyncSession) -> int:
    threshold = datetime.utcnow() - timedelta(hours=settings.APPROVAL_TIMEOUT_HOURS)
    now = datetime.utcnow()

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
        should_remind = False
        if rec.last_reminded_at is None:
            should_remind = True
        else:
            elapsed = (now - rec.last_reminded_at).total_seconds()
            if elapsed >= 3600:
                should_remind = True

        if should_remind:
            rec.reminder_count += 1
            rec.last_reminded_at = now
            reminded += 1

            if rec.approver_id and rec.approver_id > 0:
                project_result = await db.execute(
                    select(EngineeringProject).where(
                        EngineeringProject.id == rec.project_id
                    )
                )
                project = project_result.scalar_one_or_none()
                project_name = project.name if project else "未知项目"
                stage_label = {
                    ApprovalStage.SAFETY: "安监",
                    ApprovalStage.DESIGN: "设计",
                    ApprovalStage.ENGINEERING: "工程",
                    ApprovalStage.FINAL: "终审"
                }.get(rec.stage, rec.stage.value)

                await notification_service.create_notification(
                    db,
                    rec.approver_id,
                    NotificationType.APPROVAL,
                    f"【审批提醒】{stage_label}审批待处理",
                    f"项目《{project_name}》的{stage_label}审批已超时，请尽快处理。项目编号：{project.project_no if project else 'N/A'}",
                    rec.project_id,
                    "engineering_project"
                )

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
