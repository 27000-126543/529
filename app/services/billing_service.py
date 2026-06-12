from typing import Optional, List
from datetime import datetime, date, timedelta
from decimal import Decimal
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import and_, or_, func
from app.models import (
    ResidentAccount, Bill, BillStatus, GasStatus,
    GasPriceTier, Payment, MeterReading
)
from app.utils.security import generate_order_no
from app.config import settings


async def get_active_price_tiers(db: AsyncSession, effective_date: Optional[date] = None) -> List[GasPriceTier]:
    if not effective_date:
        effective_date = date.today()
    result = await db.execute(
        select(GasPriceTier).where(
            and_(
                GasPriceTier.is_active == True,
                GasPriceTier.effective_date <= effective_date
            )
        ).order_by(GasPriceTier.tier)
    )
    tiers = result.scalars().all()

    latest_tiers = {}
    for t in tiers:
        if t.tier not in latest_tiers or t.effective_date > latest_tiers[t.tier].effective_date:
            latest_tiers[t.tier] = t
    return sorted(latest_tiers.values(), key=lambda x: x.tier)


async def calculate_bill(
    db: AsyncSession,
    account: ResidentAccount,
    total_volume: Decimal,
    billing_start: date,
    billing_end: date
) -> dict:
    tiers = await get_active_price_tiers(db, billing_start)

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


async def generate_monthly_bill(
    db: AsyncSession,
    account: ResidentAccount,
    billing_month: str,
    current_reading: Optional[Decimal] = None
) -> Optional[Bill]:
    existing = await db.execute(
        select(Bill).where(
            and_(
                Bill.account_id == account.id,
                Bill.billing_month == billing_month
            )
        )
    )
    if existing.scalar_one_or_none():
        return None

    year, month = map(int, billing_month.split("-"))
    billing_start = date(year, month, 1)
    if month == 12:
        billing_end = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        billing_end = date(year, month + 1, 1) - timedelta(days=1)

    previous_reading = account.meter_reading

    if current_reading is None:
        latest_reading = await db.execute(
            select(MeterReading).where(
                MeterReading.account_id == account.id
            ).order_by(MeterReading.reading_date.desc()).limit(1)
        )
        reading = latest_reading.scalar_one_or_none()
        if reading and reading.reading_date >= billing_start:
            current_reading = reading.reading_value
        else:
            avg_daily = Decimal("0.5")
            days_in_month = (billing_end - billing_start).days + 1
            current_reading = previous_reading + (avg_daily * Decimal(days_in_month))

    total_volume = current_reading - previous_reading
    if total_volume < 0:
        total_volume = Decimal("0")

    calc = await calculate_bill(db, account, total_volume, billing_start, billing_end)

    due_date = billing_end + timedelta(days=15)

    bill = Bill(
        bill_no=generate_order_no("BL"),
        account_id=account.id,
        billing_month=billing_month,
        billing_start_date=billing_start,
        billing_end_date=billing_end,
        previous_reading=previous_reading,
        current_reading=current_reading,
        due_date=due_date,
        status=BillStatus.UNPAID,
        **calc
    )
    db.add(bill)
    await db.flush()

    account.last_reading_date = billing_end
    await db.flush()

    return bill


async def generate_all_monthly_bills(db: AsyncSession, billing_month: str) -> int:
    result = await db.execute(
        select(ResidentAccount).where(ResidentAccount.gas_status != GasStatus.SUSPENDED)
    )
    accounts = result.scalars().all()
    count = 0
    for acc in accounts:
        try:
            bill = await generate_monthly_bill(db, acc, billing_month)
            if bill:
                count += 1
        except Exception:
            continue
    return count


async def process_payment(
    db: AsyncSession,
    bill_id: int,
    amount: Decimal,
    payment_method: Optional[str] = None,
    transaction_no: Optional[str] = None
) -> Payment:
    result = await db.execute(
        select(Bill).where(Bill.id == bill_id)
    )
    bill = result.scalar_one_or_none()
    if not bill:
        raise ValueError("Bill not found")

    payment = Payment(
        payment_no=generate_order_no("PY"),
        bill_id=bill_id,
        account_id=bill.account_id,
        amount=amount,
        payment_method=payment_method,
        transaction_no=transaction_no
    )
    db.add(payment)
    await db.flush()

    bill.paid_amount += amount
    if bill.paid_amount >= bill.total_amount:
        bill.status = BillStatus.PAID
        bill.paid_at = datetime.utcnow()
    elif bill.paid_amount > 0:
        bill.status = BillStatus.PARTIAL
    else:
        bill.status = BillStatus.UNPAID

    if bill.restriction_issued and bill.status == BillStatus.PAID:
        account_result = await db.execute(
            select(ResidentAccount).where(ResidentAccount.id == bill.account_id)
        )
        account = account_result.scalar_one_or_none()
        if account and account.gas_status == GasStatus.RESTRICTED:
            account.gas_status = GasStatus.NORMAL
            account.gas_restricted_at = None

    await db.flush()
    return payment


async def check_overdue_bills_and_restrict(db: AsyncSession) -> int:
    cutoff_date = date.today() - timedelta(days=settings.BILL_OVERDUE_DAYS)
    result = await db.execute(
        select(Bill).where(
            and_(
                Bill.due_date < cutoff_date,
                Bill.status.in_([BillStatus.UNPAID, BillStatus.PARTIAL, BillStatus.OVERDUE]),
                Bill.restriction_issued == False
            )
        )
    )
    bills = result.scalars().all()

    restricted_count = 0
    for bill in bills:
        bill.status = BillStatus.OVERDUE
        bill.restriction_issued = True
        bill.restricted_at = datetime.utcnow()

        account_result = await db.execute(
            select(ResidentAccount).where(ResidentAccount.id == bill.account_id)
        )
        account = account_result.scalar_one_or_none()
        if account and account.gas_status == GasStatus.NORMAL:
            account.gas_status = GasStatus.RESTRICTED
            account.gas_restricted_at = datetime.utcnow()
            restricted_count += 1

    await db.flush()
    return restricted_count
