from celery import shared_task
from datetime import datetime, date, timedelta
from decimal import Decimal
from app.database import SyncSessionLocal
from app.models import (
    DailyReport, Area, User, UserRole, NotificationType,
    GasPurchasePlan, GasInventory, LoadPrediction, GasSupplier,
    WorkOrder, WorkOrderStatus, SensorReading, Sensor,
    LeakWarning, Bill, ResidentAccount, GasStatus
)
from app.services import sensor_service, notification_service
from app.config import settings
from app.utils.logger import get_logger
from app.utils.security import generate_order_no
import calendar

logger = get_logger(__name__)


@shared_task(name="app.tasks.report_tasks.generate_daily_reports")
def generate_daily_reports():
    db = SyncSessionLocal()
    try:
        report_date = date.today() - timedelta(days=1)

        areas = db.execute(Area.__table__.select()).mappings().all()
        area_ids = [None] + [a.id for a in areas]

        generated = 0
        for area_id in area_ids:
            try:
                report = sensor_service.generate_daily_report.__wrapped__(db, report_date, area_id)
                generated += 1
            except Exception as e:
                logger.error(f"生成日报失败: area_id={area_id}, error={e}")
                continue

        db.commit()

        dispatcher_ids = db.execute(
            User.__table__.select().where(
                User.role == UserRole.DISPATCHER,
                User.is_active == True
            )
        ).mappings().all()
        for u in dispatcher_ids:
            notification_service.create_notification.__wrapped__(
                db, u.id, NotificationType.SYSTEM,
                f"每日运行报表已生成",
                f"报表日期: {report_date}，共生成 {generated} 份区域报表",
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


@shared_task(name="app.tasks.report_tasks.predict_daily_demand")
def predict_daily_demand():
    db = SyncSessionLocal()
    try:
        for_date = date.today() + timedelta(days=1)

        areas = db.execute(Area.__table__.select()).mappings().all()
        area_ids = [None] + [a.id for a in areas]

        total_prediction = {}
        for area_id in area_ids:
            try:
                pred = sensor_service.predict_demand.__wrapped__(db, area_id, for_date)
                total_prediction[str(area_id)] = pred.get("total_predicted_volume", 0)
            except Exception as e:
                logger.error(f"预测失败: area_id={area_id}, error={e}")
                continue

        db.commit()
        logger.info(f"predict_daily_demand: 完成 {len(area_ids)} 个预测")
        return {"date": str(for_date), "predictions": len(area_ids)}
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
            GasPurchasePlan.__table__.select().where(
                GasPurchasePlan.plan_month == plan_month
            )
        ).mappings().first()
        if existing:
            return {"status": "exists", "plan_month": plan_month}

        days_in_month = calendar.monthrange(next_month.year, next_month.month)[1]
        from sqlalchemy import select, func

        hist_start = date(today.year - 1, next_month.month, 1) if today.month != 1 else date(today.year, 1, 1)
        hist_end = today
        total_historical = 1000000.0

        days_covered = max(1, (hist_end - hist_start).days)
        avg_daily = total_historical / days_covered
        predicted_demand = Decimal(str(avg_daily * days_in_month * 1.1))

        inv_result = db.execute(GasInventory.__table__.select()).mappings().first()
        current_inv = Decimal(str(inv_result.current_volume if inv_result else 0))

        safety_stock = predicted_demand * Decimal("0.15")

        if current_inv >= (predicted_demand + safety_stock):
            planned_volume = Decimal("0")
        else:
            planned_volume = predicted_demand + safety_stock - current_inv

        suppliers = db.execute(GasSupplier.__table__.select().where(GasSupplier.is_active == True)).mappings().all()
        supplier_id = suppliers[0].id if suppliers else None
        unit_price = Decimal("3.5")

        plan = GasPurchasePlan(
            plan_no=generate_order_no("PP"),
            plan_month=plan_month,
            predicted_demand=predicted_demand,
            current_inventory=current_inv,
            safety_stock=safety_stock,
            planned_volume=planned_volume,
            supplier_id=supplier_id,
            unit_price=unit_price,
            total_amount=planned_volume * unit_price
        )
        db.add(plan)
        db.commit()
        db.refresh(plan)

        dispatcher_ids = db.execute(
            User.__table__.select().where(
                User.role == UserRole.DISPATCHER,
                User.is_active == True
            )
        ).mappings().all()
        for u in dispatcher_ids:
            notification_service.create_notification.__wrapped__(
                db, u.id, NotificationType.SYSTEM,
                f"月度采购计划已生成",
                f"月份: {plan_month}，计划采购: {planned_volume} m³，金额: {plan.total_amount}元",
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
