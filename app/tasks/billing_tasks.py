from celery import shared_task
from datetime import datetime, date, timedelta
from decimal import Decimal
from app.database import SyncSessionLocal
from app.models import (
    ResidentAccount, Bill, BillStatus, GasStatus,
    GasPriceTier, User, UserRole, NotificationType, WorkOrder,
    WorkOrderType
)
from app.services import billing_service, notification_service, work_order_service
from app.schemas.sensor import WorkOrderCreate
from app.config import settings
from app.utils.logger import get_logger

logger = get_logger(__name__)


@shared_task(name="app.tasks.billing_tasks.generate_monthly_bills")
def generate_monthly_bills():
    db = SyncSessionLocal()
    try:
        today = date.today()
        billing_month = (today.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")

        accounts = db.execute(
            ResidentAccount.__table__.select().where(ResidentAccount.gas_status != GasStatus.SUSPENDED)
        ).mappings().all()

        count = 0
        for acc_data in accounts:
            try:
                acc = db.get(ResidentAccount, acc_data.id)
                if not acc:
                    continue
                bill = billing_service.generate_monthly_bill.__wrapped__(db, acc, billing_month)
                if bill:
                    count += 1
                    notification_service.create_notification.__wrapped__(
                        db, acc.user_id, NotificationType.BILL,
                        f"月度账单已生成: {billing_month}",
                        f"总金额: {bill.total_amount} 元，到期日: {bill.due_date}",
                        bill.id, "bill"
                    )
            except Exception as e:
                logger.error(f"生成账单失败: account_id={acc_data.id}, error={e}")
                continue

        db.commit()
        dispatcher_ids = db.execute(
            User.__table__.select().where(
                User.role == UserRole.DISPATCHER, User.is_active == True
            )
        ).mappings().all()
        for u in dispatcher_ids:
            notification_service.create_notification.__wrapped__(
                db, u.id, NotificationType.SYSTEM,
                f"月度账单生成完成",
                f"账单月份: {billing_month}，共生成 {count} 份账单",
                None, "billing_report"
            )
        db.commit()

        logger.info(f"generate_monthly_bills: 生成了 {count} 份账单")
        return {"month": billing_month, "generated": count}
    except Exception as e:
        logger.error(f"generate_monthly_bills error: {e}")
        db.rollback()
        raise
    finally:
        db.close()


@shared_task(name="app.tasks.billing_tasks.check_overdue_bills")
def check_overdue_bills():
    db = SyncSessionLocal()
    try:
        cutoff_date = date.today() - timedelta(days=settings.BILL_OVERDUE_DAYS)
        from sqlalchemy import and_, select

        bills = db.execute(
            select(Bill).where(
                and_(
                    Bill.due_date < cutoff_date,
                    Bill.status.in_([
                        BillStatus.UNPAID,
                        BillStatus.PARTIAL,
                    ]),
                    Bill.restriction_issued == False
                )
            )
        ).scalars().all()

        restricted = 0
        bills_updated = 0
        for bill in bills:
            try:
                bill.status = BillStatus.OVERDUE
                bill.restriction_issued = True
                bill.restricted_at = datetime.utcnow()
                bills_updated += 1

                acc = db.get(ResidentAccount, bill.account_id)
                if acc and acc.gas_status == GasStatus.NORMAL:
                    acc.gas_status = GasStatus.RESTRICTED
                    acc.gas_restricted_at = datetime.utcnow()
                    restricted += 1

                    notification_service.create_notification.__wrapped__(
                        db, acc.user_id, NotificationType.BILL,
                        f"燃气已因欠费限气",
                        f"账单金额: {bill.total_amount}元，请尽快缴费恢复供气",
                        bill.id, "gas_restricted"
                    )

                    collector_ids = db.execute(
                        select(User.id).where(
                            and_(User.role == UserRole.COLLECTOR, User.is_active == True)
                        )
                    ).scalars().all()
                    for cid in list(collector_ids)[:10]:
                        notification_service.create_notification.__wrapped__(
                            db, cid, NotificationType.BILL,
                            f"欠费催缴任务: {acc.resident_name}",
                            f"账户: {acc.account_no}，欠费: {bill.total_amount}元",
                            bill.id, "collection_task"
                        )
                        bill.collector_id = cid
                        break
            except Exception as e:
                logger.error(f"处理欠费账单失败: {bill.id}, error={e}")
                continue

        db.commit()
        logger.info(f"check_overdue_bills: 更新了 {bills_updated} 账单，限气 {restricted} 账户")
        return {"bills_overdue": bills_updated, "accounts_restricted": restricted}
    except Exception as e:
        logger.error(f"check_overdue_bills error: {e}")
        db.rollback()
        raise
    finally:
        db.close()
