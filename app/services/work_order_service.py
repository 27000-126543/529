from typing import Optional, List
from datetime import datetime, timedelta
from decimal import Decimal
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import and_, or_, func
from app.models import (
    WorkOrder, WorkOrderStatus, WorkOrderAssignment,
    MaintenanceTeam, User, UserRole, Area, WarningLevel
)
from app.utils.security import generate_order_no, calculate_distance
from app.config import settings


async def find_nearest_team(
    db: AsyncSession,
    area_id: int,
    longitude: Optional[Decimal],
    latitude: Optional[Decimal],
    exclude_team_ids: Optional[List[int]] = None
) -> Optional[MaintenanceTeam]:
    query = select(MaintenanceTeam).where(
        and_(
            MaintenanceTeam.status == "active",
            MaintenanceTeam.current_load < MaintenanceTeam.max_capacity
        )
    )
    if exclude_team_ids:
        query = query.where(MaintenanceTeam.id.notin_(exclude_team_ids))

    if area_id:
        area_result = await db.execute(
            select(
                MaintenanceTeam.area_id.in_([area_id])
            )
        )
        pass

    teams = (await db.execute(query)).scalars().all()
    if not teams:
        return None

    scored = []
    for team in teams:
        score = team.current_load / max(team.max_capacity, 1)
        if longitude and latitude and team.longitude and team.latitude:
            dist = calculate_distance(
                float(latitude), float(longitude),
                float(team.latitude), float(team.longitude)
            )
            score += dist * 0.1
        scored.append((score, team))

    scored.sort(key=lambda x: x[0])
    return scored[0][1] if scored else None


async def find_available_user_in_team(
    db: AsyncSession,
    team_id: int
) -> Optional[User]:
    result = await db.execute(
        select(User).where(
            and_(
                User.team_id == team_id,
                User.is_active == True,
                User.role.in_([UserRole.MAINTENANCE, UserRole.ENGINEER])
            )
        )
    )
    users = result.scalars().all()
    if not users:
        return None

    user_loads = []
    for u in users:
        wo_count = await db.execute(
            select(func.count(WorkOrder.id)).where(
                and_(
                    WorkOrder.assignee_id == u.id,
                    WorkOrder.status.in_([
                        WorkOrderStatus.ASSIGNED,
                        WorkOrderStatus.ACCEPTED,
                        WorkOrderStatus.IN_PROGRESS
                    ])
                )
            )
        )
        count = wo_count.scalar_one()
        user_loads.append((count, u))

    user_loads.sort(key=lambda x: x[0])
    return user_loads[0][1]


async def create_work_order(
    db: AsyncSession,
    data,
    owner_id: Optional[int] = None,
    auto_assign: bool = True
) -> WorkOrder:
    wo = WorkOrder(
        order_no=generate_order_no("WO"),
        type=data.type,
        title=data.title,
        description=data.description,
        warning_id=data.warning_id,
        resident_report_id=data.resident_report_id,
        area_id=data.area_id,
        team_id=data.team_id,
        priority=data.priority,
        owner_id=owner_id,
        status=WorkOrderStatus.PENDING,
        longitude=data.longitude,
        latitude=data.latitude,
        level=data.level
    )
    db.add(wo)
    await db.flush()

    if auto_assign:
        await assign_work_order(db, wo)

    return wo


async def assign_work_order(
    db: AsyncSession,
    work_order: WorkOrder,
    team_id: Optional[int] = None,
    assignee_id: Optional[int] = None
) -> WorkOrder:
    if not team_id and not work_order.team_id:
        team_id = work_order.team_id

    if not team_id:
        team = await find_nearest_team(
            db,
            work_order.area_id,
            work_order.longitude,
            work_order.latitude
        )
        if team:
            team_id = team.id
            work_order.team_id = team_id
            team.current_load += 1

    if not assignee_id and team_id:
        user = await find_available_user_in_team(db, team_id)
        if user:
            assignee_id = user.id

    if assignee_id:
        work_order.assignee_id = assignee_id
        work_order.status = WorkOrderStatus.ASSIGNED
        work_order.assigned_at = datetime.utcnow()

        assignment = WorkOrderAssignment(
            work_order_id=work_order.id,
            assignee_id=assignee_id,
            assigned_by=None,
            status="pending",
            is_active=True
        )
        db.add(assignment)

    await db.flush()
    return work_order


async def accept_work_order(db: AsyncSession, work_order: WorkOrder, assignee_id: int) -> WorkOrder:
    work_order.status = WorkOrderStatus.ACCEPTED
    work_order.accepted_at = datetime.utcnow()

    if work_order.assigned_at:
        response_time = work_order.accepted_at - work_order.assigned_at
        work_order.response_minutes = int(response_time.total_seconds() // 60)

    result = await db.execute(
        select(WorkOrderAssignment).where(
            and_(
                WorkOrderAssignment.work_order_id == work_order.id,
                WorkOrderAssignment.assignee_id == assignee_id,
                WorkOrderAssignment.is_active == True
            )
        )
    )
    assignment = result.scalar_one_or_none()
    if assignment:
        assignment.status = "accepted"
        assignment.accepted_at = datetime.utcnow()

    await db.flush()
    return work_order


async def complete_work_order(db: AsyncSession, work_order: WorkOrder, result: str, images: Optional[List] = None) -> WorkOrder:
    work_order.status = WorkOrderStatus.COMPLETED
    work_order.completed_at = datetime.utcnow()
    work_order.result = result
    work_order.images = images

    if work_order.started_at:
        resolution_time = work_order.completed_at - work_order.started_at
        work_order.resolution_minutes = int(resolution_time.total_seconds() // 60)
    elif work_order.accepted_at:
        resolution_time = work_order.completed_at - work_order.accepted_at
        work_order.resolution_minutes = int(resolution_time.total_seconds() // 60)

    if work_order.team_id:
        team_result = await db.execute(
            select(MaintenanceTeam).where(MaintenanceTeam.id == work_order.team_id)
        )
        team = team_result.scalar_one_or_none()
        if team and team.current_load > 0:
            team.current_load -= 1

    await db.flush()
    return work_order


async def escalate_work_order(db: AsyncSession, work_order: WorkOrder, reason: Optional[str] = None) -> WorkOrder:
    work_order.status = WorkOrderStatus.ESCALATED
    work_order.escalated_at = datetime.utcnow()
    work_order.escalation_count += 1

    if work_order.level == WarningLevel.LEVEL_1:
        work_order.level = WarningLevel.LEVEL_2
    elif work_order.level == WarningLevel.LEVEL_2:
        work_order.level = WarningLevel.LEVEL_3
    elif work_order.level == WarningLevel.LEVEL_3:
        work_order.level = WarningLevel.LEVEL_4

    await db.flush()

    if work_order.team_id:
        team_result = await db.execute(
            select(MaintenanceTeam).where(MaintenanceTeam.id == work_order.team_id)
        )
        team = team_result.scalar_one_or_none()
        if team and team.current_load > 0:
            team.current_load -= 1

    work_order.team_id = None
    work_order.assignee_id = None
    await assign_work_order(db, work_order)

    return work_order


async def check_and_escalate_overdue_orders(db: AsyncSession):
    threshold = datetime.utcnow() - timedelta(minutes=settings.WORK_ORDER_AUTO_UPGRADE_MINUTES)
    query = select(WorkOrder).where(
        and_(
            WorkOrder.status.in_([WorkOrderStatus.ASSIGNED, WorkOrderStatus.PENDING]),
            WorkOrder.created_at < threshold,
            or_(
                WorkOrder.escalation_count == 0,
                WorkOrder.escalated_at < (datetime.utcnow() - timedelta(minutes=settings.WORK_ORDER_AUTO_UPGRADE_MINUTES))
            )
        )
    )
    orders = (await db.execute(query)).scalars().all()

    for wo in orders:
        try:
            await escalate_work_order(db, wo, reason="超时未处理自动升级")
        except Exception:
            continue
    return len(orders)
