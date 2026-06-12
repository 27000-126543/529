from celery import shared_task
from datetime import datetime, date, timedelta
from decimal import Decimal
import random
import calendar
from sqlalchemy import select, and_, func, cast, Date, extract, Integer
from app.database import SyncSessionLocal
from app.models import (
    DailyReport, Area, User, UserRole, NotificationType,
    GasPurchasePlan, GasInventory, LoadPrediction, GasSupplier,
    WorkOrder, WorkOrderStatus, SensorReading, Sensor,
    LeakWarning, Bill, ResidentAccount, GasStatus,
    Notification, SensorType
)
from app.config import settings
from app.utils.logger import get_logger
from app.utils.security import generate_order_no

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


def _get_total_gas_volume(db, report_date, area_id=None):
    start_dt = datetime.combine(report_date, datetime.min.time())
    end_dt = start_dt + timedelta(days=1)
    query = select(func.coalesce(func.sum(SensorReading.value), Decimal("0"))).select_from(
        SensorReading
    ).join(Sensor, Sensor.id == SensorReading.sensor_id).where(
        and_(
            Sensor.type == SensorType.FLOW,
            SensorReading.reading_time >= start_dt,
            SensorReading.reading_time < end_dt
        )
    )
    if area_id:
        query = query.where(Sensor.area_id == area_id)
    result = db.execute(query).scalar_one()
    return result


def _get_leak_stats(db, report_date, area_id=None):
    start_dt = datetime.combine(report_date, datetime.min.time())
    end_dt = start_dt + timedelta(days=1)
    query = select(LeakWarning).where(
        and_(
            LeakWarning.created_at >= start_dt,
            LeakWarning.created_at < end_dt
        )
    )
    if area_id:
        query = query.where(LeakWarning.area_id == area_id)
    warnings = list(db.execute(query).scalars().all())
    count = len(warnings)
    resolved = sum(1 for w in warnings if w.status != "active")
    rate = Decimal("100") * Decimal(resolved) / Decimal(count) if count > 0 else Decimal("0")
    return count, resolved, rate


def _get_work_order_stats(db, report_date, area_id=None):
    start_dt = datetime.combine(report_date, datetime.min.time())
    end_dt = start_dt + timedelta(days=1)
    query = select(WorkOrder).where(
        WorkOrder.created_at >= start_dt,
        WorkOrder.created_at < end_dt
    )
    if area_id:
        query = query.where(WorkOrder.area_id == area_id)
    orders = list(db.execute(query).scalars().all())
    count = len(orders)
    completed = sum(1 for o in orders if o.status == "completed")
    response_times = [o.response_minutes for o in orders if o.response_minutes is not None]
    avg_response = Decimal(str(sum(response_times) / len(response_times))) if response_times else Decimal("0")
    resolution_times = [o.resolution_minutes for o in orders if o.resolution_minutes is not None]
    avg_resolution = Decimal(str(sum(resolution_times) / len(resolution_times))) if resolution_times else Decimal("0")
    return count, completed, avg_response, avg_resolution


def _get_peak_hour_volume(db, report_date, area_id=None):
    from app.config import settings
    start_dt = datetime.combine(report_date, datetime.min.time())
    end_dt = start_dt + timedelta(days=1)
    query = select(func.coalesce(func.sum(SensorReading.value), Decimal("0"))).select_from(
        SensorReading
    ).join(Sensor, Sensor.id == SensorReading.sensor_id).where(
        and_(
            Sensor.type == SensorType.FLOW,
            SensorReading.reading_time >= start_dt,
            SensorReading.reading_time < end_dt,
            extract("hour", SensorReading.reading_time).in_(settings.PEAK_HOURS)
        )
    )
    if area_id:
        query = query.where(Sensor.area_id == area_id)
    result = db.execute(query).scalar_one()
    return result


def _get_overdue_bill_count(db, report_date, area_id=None):
    query = select(func.count(Bill.id)).select_from(Bill).join(
        ResidentAccount, ResidentAccount.id == Bill.account_id
    ).where(
        and_(
            Bill.due_date < report_date,
            Bill.status != "paid"
        )
    )
    if area_id:
        query = query.where(ResidentAccount.area_id == area_id)
    result = db.execute(query).scalar_one()
    return result


def _get_revenue(db, report_date, area_id=None):
    start_dt = datetime.combine(report_date, datetime.min.time())
    end_dt = start_dt + timedelta(days=1)
    query = select(func.coalesce(func.sum(Bill.paid_amount), Decimal("0"))).select_from(Bill).join(
        ResidentAccount, ResidentAccount.id == Bill.account_id
    ).where(
        and_(
            Bill.paid_at >= start_dt,
            Bill.paid_at < end_dt
        )
    )
    if area_id:
        query = query.where(ResidentAccount.area_id == area_id)
    result = db.execute(query).scalar_one()
    return result


def _upsert_daily_report(db, report_date, area_id, stats):
    existing = db.execute(
        select(DailyReport).where(
            and_(
                DailyReport.report_date == report_date,
                DailyReport.area_id.is_(None) if area_id is None else DailyReport.area_id == area_id
            )
        )
    ).scalar_one_or_none()
    if existing:
        existing.total_gas_volume = stats["total_gas_volume"]
        existing.peak_hour_volume = stats["peak_hour_volume"]
        existing.leak_count = stats["leak_count"]
        existing.leak_resolved = stats["leak_resolved"]
        existing.leak_detection_rate = stats["leak_detection_rate"]
        existing.work_order_count = stats["work_order_count"]
        existing.work_order_completed = stats["work_order_completed"]
        existing.avg_response_minutes = stats["avg_response_minutes"]
        existing.avg_resolution_minutes = stats["avg_resolution_minutes"]
        existing.overdue_bill_count = stats["overdue_bill_count"]
        existing.revenue = stats["revenue"]
        existing.complaint_count = 0
        existing.complaint_rate = Decimal("0")
        existing.new_connection_count = 0
        return existing
    else:
        report = DailyReport(
            report_date=report_date,
            area_id=area_id,
            total_gas_volume=stats["total_gas_volume"],
            peak_hour_volume=stats["peak_hour_volume"],
            leak_count=stats["leak_count"],
            leak_resolved=stats["leak_resolved"],
            leak_detection_rate=stats["leak_detection_rate"],
            work_order_count=stats["work_order_count"],
            work_order_completed=stats["work_order_completed"],
            avg_response_minutes=stats["avg_response_minutes"],
            avg_resolution_minutes=stats["avg_resolution_minutes"],
            complaint_count=0,
            complaint_rate=Decimal("0"),
            new_connection_count=0,
            overdue_bill_count=stats["overdue_bill_count"],
            revenue=stats["revenue"]
        )
        db.add(report)
        db.flush()
        return report


@shared_task(name="app.tasks.report_tasks.generate_daily_reports")
def generate_daily_reports():
    db = SyncSessionLocal()
    try:
        report_date = date.today() - timedelta(days=1)

        areas = list(db.execute(select(Area)).scalars().all())
        area_ids = [None] + [a.id for a in areas]

        generated = 0
        for area_id in area_ids:
            try:
                total_gas = _get_total_gas_volume(db, report_date, area_id)
                peak_gas = _get_peak_hour_volume(db, report_date, area_id)
                leak_count, leak_resolved, leak_rate = _get_leak_stats(db, report_date, area_id)
                wo_count, wo_completed, avg_resp, avg_res = _get_work_order_stats(db, report_date, area_id)
                overdue_bills = _get_overdue_bill_count(db, report_date, area_id)
                revenue = _get_revenue(db, report_date, area_id)

                stats = {
                    "total_gas_volume": total_gas,
                    "peak_hour_volume": peak_gas,
                    "leak_count": leak_count,
                    "leak_resolved": leak_resolved,
                    "leak_detection_rate": leak_rate,
                    "work_order_count": wo_count,
                    "work_order_completed": wo_completed,
                    "avg_response_minutes": avg_resp,
                    "avg_resolution_minutes": avg_res,
                    "overdue_bill_count": overdue_bills,
                    "revenue": revenue
                }
                _upsert_daily_report(db, report_date, area_id, stats)
                generated += 1
            except Exception as e:
                logger.error(f"生成日报失败: area_id={area_id}, error={e}")
                db.rollback()
                continue

        db.commit()

        dispatchers = _get_dispatchers(db, 3)
        for did in dispatchers:
            _create_notification_sync(
                db, did, NotificationType.SYSTEM,
                f"每日运行报表已生成",
                f"报表日期: {report_date}，共生成 {generated} 份区域报表，包括全局和 {len(areas)} 个区域",
                None, "daily_report"
            )
        db.commit()

        logger.info(f"generate_daily_reports: 生成了 {generated} 份日报")
        return {"date": str(report_date), "generated": generated}
    except Exception as e:
        logger.error(f"generate_daily_reports error: {e}")
        db.rollback()
        raise
    finally:
        db.close()


def _build_hourly_sum(db, start_date, end_date, area_id=None):
    start_dt = datetime.combine(start_date, datetime.min.time())
    end_dt = datetime.combine(end_date, datetime.min.time())
    hourly_sum = {h: Decimal("0") for h in range(24)}
    query = select(
        cast(extract("hour", SensorReading.reading_time), Integer).label("hr"),
        func.sum(SensorReading.value).label("total")
    ).select_from(SensorReading).join(
        Sensor, Sensor.id == SensorReading.sensor_id
    ).where(
        and_(
            Sensor.type == SensorType.FLOW,
            SensorReading.reading_time >= start_dt,
            SensorReading.reading_time < end_dt
        )
    ).group_by("hr")
    if area_id:
        query = query.where(Sensor.area_id == area_id)
    rows = db.execute(query).all()
    for row in rows:
        hourly_sum[int(row.hr)] = row.total or Decimal("0")
    return hourly_sum


@shared_task(name="app.tasks.report_tasks.predict_daily_demand")
def predict_daily_demand():
    db = SyncSessionLocal()
    try:
        for_date = date.today() + timedelta(days=1)
        start_date = date.today() - timedelta(days=30)
        end_date = date.today()
        days_covered = 30

        areas = list(db.execute(select(Area)).scalars().all())
        area_ids = [None] + [a.id for a in areas]

        total_written = 0
        for area_id in area_ids:
            try:
                hourly_sum = _build_hourly_sum(db, start_date, end_date, area_id)
                total_sum = sum(hourly_sum.values())
                avg_daily = total_sum / Decimal(days_covered) if days_covered > 0 else Decimal("0")

                for hour in range(24):
                    hour_avg = hourly_sum[hour] / Decimal(days_covered) if days_covered > 0 else Decimal("0")
                    prediction = LoadPrediction(
                        prediction_date=for_date,
                        prediction_hour=hour,
                        area_id=area_id,
                        predicted_volume=hour_avg,
                        model_version="v1.0"
                    )
                    db.add(prediction)
                    total_written += 1
            except Exception as e:
                logger.error(f"预测失败: area_id={area_id}, error={e}")
                db.rollback()
                continue

        db.commit()

        dispatchers = _get_dispatchers(db, 3)
        for did in dispatchers:
            _create_notification_sync(
                db, did, NotificationType.SYSTEM,
                f"每日负荷预测完成",
                f"预测日期: {for_date}，共生成 {total_written} 条预测记录",
                None, "load_prediction"
            )
        db.commit()

        logger.info(f"predict_daily_demand: 完成 {len(area_ids)} 个区域预测，共 {total_written} 条记录")
        return {"date": str(for_date), "predictions": total_written}
    except Exception as e:
        logger.error(f"predict_daily_demand error: {e}")
        db.rollback()
        raise
    finally:
        db.close()


@shared_task(name="app.tasks.report_tasks.generate_monthly_purchase_plan")
def generate_monthly_purchase_plan():
    db = SyncSessionLocal()
    try:
        today = date.today()
        if today.month == 12:
            next_month = date(today.year + 1, 1, 1)
        else:
            next_month = date(today.year, today.month + 1, 1)
        plan_month = next_month.strftime("%Y-%m")

        existing = db.execute(
            select(GasPurchasePlan).where(GasPurchasePlan.plan_month == plan_month)
        ).scalar_one_or_none()
        if existing:
            logger.info(f"generate_monthly_purchase_plan: {plan_month} 已存在，跳过")
            return {"status": "exists", "plan_month": plan_month}

        predicted_demand = Decimal(str(random.uniform(500000.0, 1000000.0)))

        inv_result = db.execute(
            select(func.coalesce(func.sum(GasInventory.current_volume), Decimal("0")))
        ).scalar_one()
        current_inventory = inv_result or Decimal("0")

        safety_stock = predicted_demand * Decimal("0.15")
        planned_volume = max(Decimal("0"), predicted_demand + safety_stock - current_inventory)

        supplier = db.execute(
            select(GasSupplier).where(GasSupplier.is_active == True)
        ).scalars().first()
        supplier_id = supplier.id if supplier else None
        unit_price = Decimal("3.5")
        total_amount = planned_volume * unit_price

        plan = GasPurchasePlan(
            plan_no=generate_order_no("PP"),
            plan_month=plan_month,
            predicted_demand=predicted_demand,
            current_inventory=current_inventory,
            safety_stock=safety_stock,
            planned_volume=planned_volume,
            supplier_id=supplier_id,
            unit_price=unit_price,
            total_amount=total_amount
        )
        db.add(plan)
        db.flush()

        dispatchers = _get_dispatchers(db, 3)
        for did in dispatchers:
            _create_notification_sync(
                db, did, NotificationType.SYSTEM,
                f"月度采购计划已生成",
                f"月份: {plan_month}，计划采购: {planned_volume} m³，金额: {total_amount}元",
                plan.id, "purchase_plan"
            )
        db.commit()

        logger.info(f"generate_monthly_purchase_plan: 已生成采购计划 {plan.plan_no}")
        return {"plan_month": plan_month, "plan_id": plan.id, "planned_volume": float(planned_volume)}
    except Exception as e:
        logger.error(f"generate_monthly_purchase_plan error: {e}")
        db.rollback()
        raise
    finally:
        db.close()
