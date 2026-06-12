from celery import shared_task
from datetime import datetime, date, timedelta
from decimal import Decimal
import random
import calendar
from sqlalchemy import select, and_, func
from app.database import SyncSessionLocal
from app.models import (
    ResidentAccount, Bill, BillStatus, GasStatus,
    GasPriceTier, User, UserRole, NotificationType,
    Notification, MeterReading
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
    from sqlalchemy import select, and_
    result = db.execute(
        select(User.id).where(
            and_(User.role == UserRole.DISPATCHER, User.is_active == True)
        )
    )
    return list(result.scalars().all())[:limit]


def _get_active_price_tiers(db, effective_date=None):
    if not effective_date:
        effective_date = date.today()
    result = db.execute(
        select(GasPriceTier).where(
            and_(
                GasPriceTier.is_active == True,
                GasPriceTier.effective_date <= effective_date
            )
        ).order_by(GasPriceTier.tier)
    )
    tiers = list(result.scalars().all())
    latest_tiers = {}
    for t in tiers:
        if t.tier not in latest_tiers or t.effective_date > latest_tiers[t.tier].effective_date:
            latest_tiers[t.tier] = t
    return sorted(latest_tiers.values(), key=lambda x: x.tier)


def _calculate_bill_by_tiers(db, account, total_volume, billing_start):
    tiers = _get_active_price_tiers(db, billing_start)
    tier1_vol = tier2_vol = tier3_vol = Decimal("0")
    tier1_amt = tier2_amt = tier3_amt = Decimal("0")
    remaining = total_volume
    for tier in tiers:
        tier_range = (tier.max_volume - tier.min_volume) if tier.max_volume else None
        if tier.tier == 1:
            if tier_range and remaining > tier_range:
                tier1_vol = tier_range
                remaining -= tier_range
            else:
                tier1_vol = remaining
                remaining = Decimal("0")
            tier1_amt = tier1_vol * tier.unit_price
        elif tier.tier == 2:
            if tier_range and remaining > tier_range:
                tier2_vol = tier_range
                remaining -= tier_range
            else:
                tier2_vol = remaining
                remaining = Decimal("0")
            tier2_amt = tier2_vol * tier.unit_price
        elif tier.tier == 3:
            tier3_vol = remaining
            remaining = Decimal("0")
            tier3_amt = tier3_vol * tier.unit_price
    total_amount = tier1_amt + tier2_amt + tier3_amt
    return {
        "total_volume": total_volume,
        "tier1_volume": tier1_vol,
        "tier2_volume": tier2_vol,
        "tier3_volume": tier3_vol,
        "tier1_amount": tier1_amt,
        "tier2_amount": tier2_amt,
        "tier3_amount": tier3_amt,
        "total_amount": total_amount,
        "surcharge": Decimal("0"),
        "discount": Decimal("0")
    }


def _estimate_current_reading(db, account, previous_reading):
    thirty_days_ago = date.today() - timedelta(days=30)
    result = db.execute(
        select(MeterReading).where(
            and_(
                MeterReading.account_id == account.id,
                MeterReading.reading_date >= thirty_days_ago
            )
        ).order_by(MeterReading.reading_date.desc())
    )
    readings = list(result.scalars().all())
    if len(readings) >= 2:
        oldest = readings[-1]
        newest = readings[0]
        days_diff = max((newest.reading_date - oldest.reading_date).days, 1)
        avg_daily = (newest.reading_value - oldest.reading_value) / Decimal(days_diff)
        estimated_monthly = avg_daily * Decimal(30)
        if estimated_monthly < 0:
            estimated_monthly = Decimal("0")
        return previous_reading + estimated_monthly
    else:
        random_usage = Decimal(str(random.uniform(10.0, 30.0)))
        return previous_reading + random_usage


@shared_task(name="app.tasks.billing_tasks.generate_monthly_bills")
def generate_monthly_bills():
    db = SyncSessionLocal()
    try:
        today = date.today()
        first_day_this_month = today.replace(day=1)
        last_day_prev_month = first_day_this_month - timedelta(days=1)
        billing_month = last_day_prev_month.strftime("%Y-%m")
        year, month = map(int, billing_month.split("-"))
        billing_start = date(year, month, 1)
        if month == 12:
            billing_end = date(year + 1, 1, 1) - timedelta(days=1)
        else:
            billing_end = date(year, month + 1, 1) - timedelta(days=1)

        accounts_result = db.execute(
            select(ResidentAccount).where(ResidentAccount.gas_status != "suspended")
        )
        accounts = list(accounts_result.scalars().all())

        actual_count = 0
        failed_count = 0
        for acc in accounts:
            try:
                existing = db.execute(
                    select(Bill).where(
                        and_(
                            Bill.account_id == acc.id,
                            Bill.billing_month == billing_month
                        )
                    )
                ).scalar_one_or_none()
                if existing:
                    continue

                previous_reading = acc.meter_reading or Decimal("0")
                current_reading = _estimate_current_reading(db, acc, previous_reading)

                total_volume = current_reading - previous_reading
                if total_volume < 0:
                    total_volume = Decimal("0")
                    current_reading = previous_reading

                calc = _calculate_bill_by_tiers(db, acc, total_volume, billing_start)
                due_date = billing_end + timedelta(days=15)

                bill = Bill(
                    bill_no=generate_order_no("BL"),
                    account_id=acc.id,
                    billing_month=billing_month,
                    billing_start_date=billing_start,
                    billing_end_date=billing_end,
                    previous_reading=previous_reading,
                    current_reading=current_reading,
                    due_date=due_date,
                    status="unpaid",
                    **calc
                )
                db.add(bill)
                db.flush()

                acc.meter_reading = current_reading
                acc.last_reading_date = billing_end

                if acc.user_id:
                    _create_notification_sync(
                        db, acc.user_id, NotificationType.BILL,
                        f"月度账单已生成: {billing_month}",
                        f"总金额: {bill.total_amount} 元，到期日: {bill.due_date}",
                        bill.id, "bill"
                    )

                db.commit()
                actual_count += 1
            except Exception as e:
                logger.error(f"生成账单失败: account_id={acc.id}, error={e}")
                db.rollback()
                failed_count += 1
                continue

        dispatchers = _get_dispatchers(db, 3)
        for did in dispatchers:
            _create_notification_sync(
                db, did, NotificationType.SYSTEM,
                f"月度账单生成完成",
                f"账期: {billing_month}，共生成 {actual_count} 份账单（成功{actual_count}条，失败{failed_count}条）",
                None, "monthly_billing"
            )
        db.commit()

        logger.info(f"generate_monthly_bills: 生成了 {actual_count} 份账单（成功{actual_count}条，失败{failed_count}条）")
        return {"month": billing_month, "generated": actual_count, "failed": failed_count}
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
        now = datetime.utcnow()
        today = date.today()

        bills_result = db.execute(
            select(Bill).where(
                and_(
                    Bill.due_date < today,
                    Bill.status == "unpaid",
                    Bill.restriction_issued == False
                )
            )
        )
        bills = list(bills_result.scalars().all())

        actual_bills_updated = 0
        actual_restricted = 0
        failed_count = 0
        for bill in bills:
            try:
                bill.status = "overdue"
                bill.restriction_issued = True
                bill.restricted_at = now

                restricted_this_one = 0
                acc = db.execute(
                    select(ResidentAccount).where(ResidentAccount.id == bill.account_id)
                ).scalar_one_or_none()
                if acc:
                    acc.gas_status = "restricted"
                    acc.gas_restricted_at = now
                    restricted_this_one = 1

                    if acc.user_id:
                        _create_notification_sync(
                            db, acc.user_id, NotificationType.BILL,
                            f"燃气已因欠费限气",
                            f"账单金额: {bill.total_amount}元，请尽快缴费恢复供气",
                            bill.id, "gas_restricted"
                        )

                collector_result = db.execute(
                    select(User).where(
                        and_(User.role == UserRole.COLLECTOR, User.is_active == True)
                    ).order_by(User.id.asc())
                )
                collectors = list(collector_result.scalars().all())
                if collectors:
                    collector = collectors[0]
                    bill.collector_id = collector.id
                    _create_notification_sync(
                        db, collector.id, NotificationType.BILL,
                        f"欠费催缴任务分配",
                        f"账户: {acc.account_no if acc else '未知'}，欠费: {bill.total_amount}元",
                        bill.id, "collection_task"
                    )

                db.commit()
                actual_bills_updated += 1
                actual_restricted += restricted_this_one
            except Exception as e:
                logger.error(f"处理欠费账单失败: bill_id={bill.id}, error={e}")
                db.rollback()
                failed_count += 1
                continue

        dispatchers = _get_dispatchers(db, 3)
        for did in dispatchers:
            _create_notification_sync(
                db, did, NotificationType.SYSTEM,
                f"欠费账单检查完成",
                f"已更新 {actual_bills_updated} 份逾期账单，对 {actual_restricted} 户执行限气（成功{actual_bills_updated}条，失败{failed_count}条）",
                None, "overdue_bills"
            )
        db.commit()

        logger.info(f"check_overdue_bills: 更新了 {actual_bills_updated} 账单，限气 {actual_restricted} 账户（成功{actual_bills_updated}条，失败{failed_count}条）")
        return {"bills_overdue": actual_bills_updated, "accounts_restricted": actual_restricted, "failed": failed_count}
    except Exception as e:
        logger.error(f"check_overdue_bills error: {e}")
        db.rollback()
        raise
    finally:
        db.close()
