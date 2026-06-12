from celery import shared_task
from datetime import datetime, date, timedelta
from decimal import Decimal
from app.database import SyncSessionLocal
from app.models import (
    WorkOrder, WorkOrderStatus, User, UserRole, Area,
    ApprovalRecord, ApprovalStatus, NotificationType,
    PressureStation, Sensor, SensorReading
)
from app.services import work_order_service, project_service, notification_service, sensor_service
from app.config import settings
from app.utils.logger import get_logger

logger = get_logger(__name__)


@shared_task(name="app.tasks.notification_tasks.check_overdue_work_orders")
def check_overdue_work_orders():
    db = SyncSessionLocal()
    try:
        from app.services.work_order_service import check_and_escalate_overdue_orders

        threshold = datetime.utcnow() - timedelta(minutes=settings.WORK_ORDER_AUTO_UPGRADE_MINUTES)
        from sqlalchemy import and_, or_, select
        orders = db.execute(
            select(WorkOrder).where(
                and_(
                    WorkOrder.status.in_([
                        WorkOrderStatus.PENDING,
                        WorkOrderStatus.ASSIGNED,
                        WorkOrderStatus.ACCEPTED
                    ]),
                    WorkOrder.created_at < threshold,
                    or_(
                        WorkOrder.escalation_count == 0,
                        WorkOrder.escalated_at < (datetime.utcnow() - timedelta(minutes=settings.WORK_ORDER_AUTO_UPGRADE_MINUTES))
                    )
                )
            )
        ).scalars().all()

        count = 0
        for wo in orders:
            try:
                work_order_service.escalate_work_order.__wrapped__(db, wo, reason="定时任务：超时未处理自动升级")
                area = db.execute(select(Area).where(Area.id == wo.area_id)).scalar_one_or_none()
                if area and area.manager_id:
                    notification_service.create_notification.__wrapped__(
                        db, area.manager_id, NotificationType.WARNING,
                        f"工单自动升级: {wo.title}",
                        f"工单编号: {wo.order_no}，已升级处理",
                        wo.id, "work_order_escalation"
                    )
                if wo.assignee_id:
                    notification_service.create_notification.__wrapped__(
                        db, wo.assignee_id, NotificationType.WORK_ORDER,
                        f"升级工单已分配: {wo.title}",
                        f"工单编号: {wo.order_no}",
                        wo.id, "work_order"
                    )
                count += 1
            except Exception as e:
                logger.error(f"升级工单失败: {wo.id}, error={e}")
                continue

        db.commit()
        logger.info(f"check_overdue_work_orders: 升级了 {count} 个工单")
        return {"escalated": count}
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
        threshold = datetime.utcnow() - timedelta(hours=settings.APPROVAL_TIMEOUT_HOURS)
        from sqlalchemy import and_, select

        records = db.execute(
            select(ApprovalRecord).where(
                and_(
                    ApprovalRecord.status == ApprovalStatus.PENDING,
                    ApprovalRecord.submitted_at < threshold
                )
            )
        ).scalars().all()

        reminded = 0
        for rec in records:
            try:
                last_remind = rec.last_reminded_at or rec.submitted_at
                if last_remind and (datetime.utcnow() - last_remind).total_seconds() >= 3600:
                    rec.reminder_count += 1
                    rec.last_reminded_at = datetime.utcnow()

                    if rec.approver_id and rec.approver_id > 0:
                        notification_service.create_notification.__wrapped__(
                            db, rec.approver_id, NotificationType.APPROVAL,
                            f"审批催办：阶段 {rec.stage.value}",
                            f"项目ID: {rec.project_id}，已超时{rec.reminder_count}次提醒",
                            rec.project_id, "approval_reminder"
                        )
                    reminded += 1
            except Exception as e:
                logger.error(f"催办失败: {rec.id}, error={e}")
                continue

        db.commit()
        logger.info(f"check_approval_reminders: 催办了 {reminded} 条审批")
        return {"reminded": reminded}
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
        from sqlalchemy import select, and_, desc, func
        stations = db.execute(select(PressureStation).where(PressureStation.status == "normal")).scalars().all()

        adjusted = 0
        for station in stations:
            try:
                sensors = db.execute(
                    select(Sensor).where(
                        and_(
                            Sensor.pressure_station_id == station.id,
                            Sensor.type == "pressure"
                        )
                    )
                ).scalars().all()
                if not sensors:
                    continue

                total_pressure = Decimal("0")
                count = 0
                for s in sensors:
                    latest = db.execute(
                        select(SensorReading).where(
                            SensorReading.sensor_id == s.id
                        ).order_by(SensorReading.reading_time.desc()).limit(1)
                    ).scalar_one_or_none()
                    if latest:
                        total_pressure += latest.value
                        count += 1

                if count == 0:
                    continue

                avg_pressure = total_pressure / Decimal(count)
                log = sensor_service.check_auto_adjust_pressure.__wrapped__(
                    db, station, avg_pressure, datetime.utcnow()
                )
                if log:
                    adjusted += 1
                    dispatcher_ids = db.execute(
                        select(User.id).where(
                            and_(User.role == UserRole.DISPATCHER, User.is_active == True)
                        )
                    ).scalars().all()
                    for uid in dispatcher_ids[:5]:
                        notification_service.create_notification.__wrapped__(
                            db, uid, NotificationType.SYSTEM,
                            f"调压站自动调节: {station.name}",
                            f"{log.old_outlet_pressure} -> {log.new_outlet_pressure} kPa，原因: {log.reason}",
                            station.id, "pressure_adjust"
                        )
            except Exception as e:
                logger.error(f"调压站 {station.name} 调节失败: {e}")
                continue

        db.commit()
        logger.info(f"auto_adjust_pressure_stations: 调节了 {adjusted} 个调压站")
        return {"adjusted": adjusted}
    except Exception as e:
        logger.error(f"auto_adjust_pressure_stations error: {e}")
        db.rollback()
        raise
    finally:
        db.close()
