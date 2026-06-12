from celery import shared_task
from datetime import datetime, date, timedelta
from decimal import Decimal
from sqlalchemy import select, and_, or_, desc, func
from app.database import SyncSessionLocal
from app.models import (
    WorkOrder, WorkOrderStatus, WorkOrderAssignment,
    MaintenanceTeam, User, UserRole, Area, WarningLevel,
    ApprovalRecord, ApprovalStatus, NotificationType,
    Notification, PressureStation, Sensor, SensorReading,
    SensorType, ControlLog
)
from app.config import settings
from app.utils.logger import get_logger

logger = get_logger(__name__)


def _create_notification_sync(db, user_id, type, title, content=None, related_id=None, related_type=None):
    notification = Notification(
        user_id=user_id,
        type=type,
        title=title,
        content=content,
        related_id=related_id,
        related_type=related_type,
        is_read=False
    )
    db.add(notification)
    db.flush()
    return notification


def _get_dispatchers(db, limit=3):
    result = db.execute(
        select(User.id).where(
            and_(User.role == UserRole.DISPATCHER, User.is_active == True)
        )
    )
    return list(result.scalars().all())[:limit]


def _escalate_level(current_level):
    if current_level == WarningLevel.LEVEL_1:
        return WarningLevel.LEVEL_2
    elif current_level == WarningLevel.LEVEL_2:
        return WarningLevel.LEVEL_3
    else:
        return WarningLevel.LEVEL_4


def _find_team_for_area(db, area_id):
    result = db.execute(
        select(MaintenanceTeam).where(
            and_(
                MaintenanceTeam.area_id == area_id,
                MaintenanceTeam.status == "active",
                MaintenanceTeam.current_load < MaintenanceTeam.max_capacity
            )
        )
    )
    teams = list(result.scalars().all())
    if not teams:
        return None
    scored = []
    for team in teams:
        load_ratio = team.current_load / max(team.max_capacity, 1)
        scored.append((load_ratio, team))
    scored.sort(key=lambda x: x[0])
    return scored[0][1]


def _find_available_user_in_team(db, team_id):
    result = db.execute(
        select(User).where(
            and_(
                User.team_id == team_id,
                User.is_active == True,
                User.role.in_([UserRole.MAINTENANCE, UserRole.ENGINEER])
            )
        )
    )
    users = list(result.scalars().all())
    if not users:
        return None
    user_loads = []
    for u in users:
        wo_count = db.execute(
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
        ).scalar_one()
        user_loads.append((wo_count, u))
    user_loads.sort(key=lambda x: x[0])
    return user_loads[0][1]


@shared_task(name="app.tasks.notification_tasks.check_overdue_work_orders")
def check_overdue_work_orders():
    db = SyncSessionLocal()
    try:
        now = datetime.utcnow()
        threshold = now - timedelta(minutes=settings.WORK_ORDER_AUTO_UPGRADE_MINUTES)
        escalate_threshold = now - timedelta(minutes=settings.WORK_ORDER_AUTO_UPGRADE_MINUTES)

        result = db.execute(
            select(WorkOrder).where(
                and_(
                    WorkOrder.status.in_(["pending", "assigned", "in_progress"]),
                    WorkOrder.created_at < threshold,
                    or_(
                        WorkOrder.escalation_count == 0,
                        WorkOrder.escalated_at == None,
                        WorkOrder.escalated_at < escalate_threshold
                    )
                )
            )
        )
        orders = list(result.scalars().all())

        actual_count = 0
        failed_count = 0
        for wo in orders:
            try:
                wo.status = "escalated"
                wo.escalated_at = now
                wo.escalation_count = (wo.escalation_count or 0) + 1
                wo.level = _escalate_level(wo.level or WarningLevel.LEVEL_1)

                old_team_id = wo.team_id
                if old_team_id:
                    old_team = db.execute(
                        select(MaintenanceTeam).where(MaintenanceTeam.id == old_team_id)
                    ).scalar_one_or_none()
                    if old_team and old_team.current_load > 0:
                        old_team.current_load -= 1

                wo.team_id = None
                wo.assignee_id = None

                if wo.area_id:
                    new_team = _find_team_for_area(db, wo.area_id)
                    if not new_team:
                        areas_result = db.execute(
                            select(Area).where(Area.parent_id == (
                                select(Area.parent_id).where(Area.id == wo.area_id).scalar_subquery()
                            ))
                        )
                        sibling_areas = list(areas_result.scalars().all())
                        for sibling_area in sibling_areas:
                            if sibling_area.id != wo.area_id:
                                new_team = _find_team_for_area(db, sibling_area.id)
                                if new_team:
                                    wo.is_cross_area = True
                                    wo.cross_area_from_area_id = wo.area_id
                                    break
                    if new_team:
                        wo.team_id = new_team.id
                        new_team.current_load += 1

                        new_user = _find_available_user_in_team(db, new_team.id)
                        if new_user:
                            wo.assignee_id = new_user.id
                            wo.status = "assigned"
                            wo.assigned_at = now

                            assignment = WorkOrderAssignment(
                                work_order_id=wo.id,
                                assignee_id=new_user.id,
                                assigned_by=None,
                                assigned_at=now,
                                status="pending",
                                is_active=True
                            )
                            db.add(assignment)

                area = db.execute(
                    select(Area).where(Area.id == wo.area_id)
                ).scalar_one_or_none()
                if area and area.manager_id:
                    try:
                        _create_notification_sync(
                            db, area.manager_id, NotificationType.WARNING,
                            f"工单自动升级: {wo.title}",
                            f"工单编号: {wo.order_no}，已升级至 {wo.level.value} 处理",
                            wo.id, "work_order_escalation"
                        )
                    except Exception as e:
                        logger.error(f"发送区域经理通知失败: wo_id={wo.id}, manager_id={area.manager_id}, error={e}")

                dispatchers = _get_dispatchers(db, 3)
                for did in dispatchers:
                    try:
                        _create_notification_sync(
                            db, did, NotificationType.WARNING,
                            f"工单自动升级: {wo.title}",
                            f"工单编号: {wo.order_no}，已升级至 {wo.level.value}",
                            wo.id, "work_order_escalation"
                        )
                    except Exception as e:
                        logger.error(f"发送调度员通知失败: wo_id={wo.id}, dispatcher_id={did}, error={e}")

                if wo.assignee_id:
                    try:
                        _create_notification_sync(
                            db, wo.assignee_id, NotificationType.WORK_ORDER,
                            f"升级工单已分配: {wo.title}",
                            f"工单编号: {wo.order_no}，等级: {wo.level.value}",
                            wo.id, "work_order"
                        )
                    except Exception as e:
                        logger.error(f"发送处理人通知失败: wo_id={wo.id}, assignee_id={wo.assignee_id}, error={e}")

                db.commit()
                actual_count += 1
            except Exception as e:
                logger.error(f"升级工单失败: wo_id={wo.id}, error={e}")
                db.rollback()
                failed_count += 1
                continue

        dispatchers = _get_dispatchers(db, 3)
        for did in dispatchers:
            try:
                _create_notification_sync(
                    db, did, NotificationType.SYSTEM,
                    f"超时工单自动升级完成",
                    f"共升级 {actual_count} 个超时工单（成功{actual_count}条，失败{failed_count}条）",
                    None, "work_order_escalation"
                )
                db.commit()
            except Exception as e:
                logger.error(f"发送完成通知失败: dispatcher_id={did}, error={e}")
                db.rollback()
                continue

        logger.info(f"check_overdue_work_orders: 升级了 {actual_count} 个工单（成功{actual_count}条，失败{failed_count}条）")
        return {"escalated": actual_count, "failed": failed_count}
    except Exception as e:
        logger.error(f"check_overdue_work_orders error: {e}")
        db.rollback()
        raise
    finally:
        db.close()


@shared_task(name="app.tasks.notification_tasks.check_approval_reminders")
def check_approval_reminders():
    db = SyncSessionLocal()
    try:
        now = datetime.utcnow()
        threshold = now - timedelta(hours=settings.APPROVAL_TIMEOUT_HOURS)

        result = db.execute(
            select(ApprovalRecord).where(
                and_(
                    ApprovalRecord.status == "pending",
                    ApprovalRecord.submitted_at < threshold
                )
            )
        )
        records = list(result.scalars().all())

        actual_count = 0
        failed_count = 0
        for rec in records:
            try:
                last_remind = rec.last_reminded_at
                should_remind = False
                if last_remind is None:
                    should_remind = True
                elif (now - last_remind).total_seconds() >= 3600:
                    should_remind = True

                if should_remind:
                    rec.reminder_count = (rec.reminder_count or 0) + 1
                    rec.last_reminded_at = now

                    if rec.approver_id and rec.approver_id > 0:
                        _create_notification_sync(
                            db, rec.approver_id, NotificationType.APPROVAL,
                            f"审批催办：阶段 {rec.stage.value}",
                            f"项目ID: {rec.project_id}，已超时第{rec.reminder_count}次提醒，请尽快处理",
                            rec.project_id, "approval_reminder"
                        )
                    db.commit()
                    actual_count += 1
            except Exception as e:
                logger.error(f"催办失败: approval_id={rec.id}, error={e}")
                db.rollback()
                failed_count += 1
                continue

        dispatchers = _get_dispatchers(db, 3)
        for did in dispatchers:
            try:
                _create_notification_sync(
                    db, did, NotificationType.SYSTEM,
                    f"审批催办完成",
                    f"共催办 {actual_count} 条超时审批（成功{actual_count}条，失败{failed_count}条）",
                    None, "approval_reminder"
                )
                db.commit()
            except Exception as e:
                logger.error(f"发送完成通知失败: dispatcher_id={did}, error={e}")
                db.rollback()
                continue

        logger.info(f"check_approval_reminders: 催办了 {actual_count} 条审批（成功{actual_count}条，失败{failed_count}条）")
        return {"reminded": actual_count, "failed": failed_count}
    except Exception as e:
        logger.error(f"check_approval_reminders error: {e}")
        db.rollback()
        raise
    finally:
        db.close()


@shared_task(name="app.tasks.notification_tasks.auto_adjust_pressure_stations")
def auto_adjust_pressure_stations():
    db = SyncSessionLocal()
    try:
        now = datetime.utcnow()
        current_hour = now.hour

        stations_result = db.execute(
            select(PressureStation).where(PressureStation.status == "normal")
        )
        stations = list(stations_result.scalars().all())

        actual_count = 0
        failed_count = 0
        for station in stations:
            try:
                sensors_result = db.execute(
                    select(Sensor).where(
                        and_(
                            Sensor.pressure_station_id == station.id,
                            Sensor.type == "pressure"
                        )
                    )
                )
                sensors = list(sensors_result.scalars().all())
                if not sensors:
                    continue

                total_pressure = Decimal("0")
                count_readings = 0
                for s in sensors:
                    latest = db.execute(
                        select(SensorReading).where(
                            SensorReading.sensor_id == s.id
                        ).order_by(desc(SensorReading.reading_time)).limit(1)
                    ).scalar_one_or_none()
                    if latest:
                        total_pressure += latest.value
                        count_readings += 1

                if count_readings == 0:
                    continue

                avg_pressure = total_pressure / Decimal(count_readings)

                is_peak = current_hour in settings.PEAK_HOURS
                if is_peak:
                    target = station.outlet_pressure_max * Decimal("0.95") if station.outlet_pressure_max else avg_pressure
                    reason = "高峰时段自动调节"
                else:
                    p_min = station.outlet_pressure_min if station.outlet_pressure_min else avg_pressure
                    p_set = station.outlet_pressure_set if station.outlet_pressure_set else avg_pressure
                    target = p_min + (p_set - p_min) * Decimal("0.5")
                    reason = "常规时段自动调节"

                deviation = abs(avg_pressure - target)
                threshold_dev = target * Decimal("0.05")

                if deviation > threshold_dev:
                    trigger_condition = {
                        "peak_hours": is_peak,
                        "current_hour": current_hour,
                        "avg_pressure": float(avg_pressure),
                        "target_pressure": float(target),
                        "deviation": float(deviation),
                        "threshold": float(threshold_dev)
                    }

                    log = ControlLog(
                        pressure_station_id=station.id,
                        dispatcher_id=None,
                        old_outlet_pressure=avg_pressure,
                        new_outlet_pressure=target,
                        reason=reason,
                        is_auto=True,
                        trigger_condition=trigger_condition,
                        created_at=now
                    )
                    db.add(log)
                    db.flush()

                    dispatchers = _get_dispatchers(db, 3)
                    for did in dispatchers:
                        try:
                            _create_notification_sync(
                                db, did, NotificationType.SYSTEM,
                                f"调压站自动调节: {station.name}",
                                f"{avg_pressure} -> {target} kPa，原因: {reason}",
                                station.id, "pressure_adjust"
                            )
                        except Exception as e:
                            logger.error(f"发送调压站调节通知失败: station={station.name}, dispatcher_id={did}, error={e}")

                    db.commit()
                    actual_count += 1
            except Exception as e:
                logger.error(f"调压站 {station.name} 调节失败: {e}")
                db.rollback()
                failed_count += 1
                continue

        dispatchers = _get_dispatchers(db, 3)
        for did in dispatchers:
            try:
                _create_notification_sync(
                    db, did, NotificationType.SYSTEM,
                    f"调压站自动调节完成",
                    f"共调节 {actual_count} 个调压站（成功{actual_count}条，失败{failed_count}条）",
                    None, "pressure_adjust"
                )
                db.commit()
            except Exception as e:
                logger.error(f"发送完成通知失败: dispatcher_id={did}, error={e}")
                db.rollback()
                continue

        logger.info(f"auto_adjust_pressure_stations: 调节了 {actual_count} 个调压站（成功{actual_count}条，失败{failed_count}条）")
        return {"adjusted": actual_count, "failed": failed_count}
    except Exception as e:
        logger.error(f"auto_adjust_pressure_stations error: {e}")
        db.rollback()
        raise
    finally:
        db.close()
