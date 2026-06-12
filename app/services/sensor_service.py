from typing import Optional, List, Dict
from datetime import datetime, date, timedelta
from decimal import Decimal
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import and_, func, cast, Date as SADate
from app.models import (
    SensorReading, Sensor, LeakWarning, WarningLevel,
    PressureStation, ControlLog, Area, WorkOrder,
    WorkOrderStatus, ResidentReport, Bill, DailyReport,
    ResidentAccount, GasStatus
)
from app.config import settings
import json


async def process_sensor_reading(
    db: AsyncSession,
    sensor: Sensor,
    value: Decimal,
    reading_time: datetime
) -> SensorReading:
    is_anomaly = False
    if sensor.threshold_min is not None and value < sensor.threshold_min:
        is_anomaly = True
    if sensor.threshold_max is not None and value > sensor.threshold_max:
        is_anomaly = True
    if sensor.type.value == "leak" and sensor.leak_threshold is not None and value > sensor.leak_threshold:
        is_anomaly = True
        await handle_leak_detection(db, sensor, value, reading_time)

    reading = SensorReading(
        sensor_id=sensor.id,
        value=value,
        reading_time=reading_time,
        is_anomaly=is_anomaly
    )
    db.add(reading)

    sensor.last_reading = reading_time
    await db.flush()
    return reading


async def handle_leak_detection(
    db: AsyncSession,
    sensor: Sensor,
    concentration: Decimal,
    detected_at: datetime
) -> LeakWarning:
    level = WarningLevel.LEVEL_1
    if sensor.leak_threshold:
        ratio = float(concentration) / float(sensor.leak_threshold)
        if ratio >= 10:
            level = WarningLevel.LEVEL_4
        elif ratio >= 5:
            level = WarningLevel.LEVEL_3
        elif ratio >= 2:
            level = WarningLevel.LEVEL_2

    result = await db.execute(
        select(LeakWarning).where(
            and_(
                LeakWarning.sensor_id == sensor.id,
                LeakWarning.status == "active"
            )
        ).order_by(LeakWarning.created_at.desc()).limit(1)
    )
    existing = result.scalar_one_or_none()

    if existing:
        existing.level = level
        existing.concentration = concentration
        await db.flush()
        return existing

    warning = LeakWarning(
        sensor_id=sensor.id,
        area_id=sensor.area_id,
        level=level,
        concentration=concentration,
        longitude=sensor.longitude,
        latitude=sensor.latitude,
        description=f"传感器检测到燃气泄漏，浓度: {concentration}",
        first_detected=detected_at,
        status="active"
    )
    db.add(warning)
    await db.flush()

    return warning


async def check_auto_adjust_pressure(
    db: AsyncSession,
    station: PressureStation,
    current_outlet: Decimal,
    current_time: datetime
) -> Optional[ControlLog]:
    current_hour = current_time.hour
    is_peak = current_hour in settings.PEAK_HOURS

    target_pressure = station.outlet_pressure_set
    if is_peak and station.outlet_pressure_max:
        target_pressure = station.outlet_pressure_max * Decimal("0.95")
    elif not is_peak and station.outlet_pressure_min:
        target_pressure = station.outlet_pressure_min + (station.outlet_pressure_set - station.outlet_pressure_min) * Decimal("0.5")

    deviation = abs(current_outlet - target_pressure)
    if deviation > target_pressure * Decimal("0.05"):
        log = ControlLog(
            pressure_station_id=station.id,
            old_outlet_pressure=current_outlet,
            new_outlet_pressure=target_pressure,
            reason="用气高峰自动调节" if is_peak else "常规压力调节",
            is_auto=True,
            trigger_condition={
                "peak_hour": is_peak,
                "current_hour": current_hour,
                "current_outlet": float(current_outlet),
                "target_pressure": float(target_pressure),
                "deviation": float(deviation)
            }
        )
        db.add(log)
        await db.flush()
        return log
    return None


async def predict_demand(
    db: AsyncSession,
    area_id: Optional[int],
    for_date: date
) -> Dict:
    from app.models import LoadPrediction

    start_hist = for_date - timedelta(days=30)

    query = select(
        func.date_trunc('hour', SensorReading.reading_time).label("hour"),
        func.sum(SensorReading.value)
    ).select_from(SensorReading).join(
        Sensor, Sensor.id == SensorReading.sensor_id
    ).where(
        and_(
            Sensor.type == "flow",
            SensorReading.reading_time >= start_hist,
            SensorReading.reading_time < for_date
        )
    )
    if area_id:
        query = query.where(Sensor.area_id == area_id)
    query = query.group_by("hour")

    result = await db.execute(query)
    historical = result.all()

    hourly_avg = {}
    hourly_data = {}
    for hour_str, vol in historical:
        if hour_str:
            h = hour_str.hour
            hourly_data.setdefault(h, []).append(float(vol or 0))

    for h in range(24):
        data = hourly_data.get(h, [0])
        hourly_avg[h] = sum(data) / len(data) if data else 0

    total_daily = sum(hourly_avg.values())

    for h, pred_vol in hourly_avg.items():
        existing = await db.execute(
            select(LoadPrediction).where(
                and_(
                    LoadPrediction.prediction_date == for_date,
                    LoadPrediction.prediction_hour == h,
                    LoadPrediction.area_id == area_id
                )
            )
        )
        if not existing.scalar_one_or_none():
            pred = LoadPrediction(
                prediction_date=for_date,
                prediction_hour=h,
                area_id=area_id,
                predicted_volume=Decimal(str(pred_vol)),
                model_version="v1.0-avg30d"
            )
            db.add(pred)

    await db.flush()

    return {
        "date": str(for_date),
        "area_id": area_id,
        "total_predicted_volume": total_daily,
        "peak_hour_prediction": max(hourly_avg.items(), key=lambda x: x[1]) if hourly_avg else (7, 0),
        "hourly_predictions": hourly_avg
    }


async def generate_daily_report(db: AsyncSession, report_date: date, area_id: Optional[int] = None) -> DailyReport:
    existing = await db.execute(
        select(DailyReport).where(
            and_(
                DailyReport.report_date == report_date,
                DailyReport.area_id == area_id
            )
        )
    )
    report = existing.scalar_one_or_none()
    if report:
        return report

    next_day = report_date + timedelta(days=1)

    total_volume = Decimal("0")
    flow_result = await db.execute(
        select(func.sum(SensorReading.value)).select_from(
            SensorReading
        ).join(Sensor, Sensor.id == SensorReading.sensor_id).where(
            and_(
                Sensor.type == "flow",
                SensorReading.reading_time >= datetime.combine(report_date, datetime.min.time()),
                SensorReading.reading_time < datetime.combine(next_day, datetime.min.time()),
                (Sensor.area_id == area_id) if area_id else True
            )
        )
    )
    if flow_result.scalar_one_or_none():
        total_volume = Decimal(str(flow_result.scalar_one() or 0))

    peak_vol = Decimal("0")
    for h in settings.PEAK_HOURS:
        pass

    leak_query = select(LeakWarning).where(
        and_(
            func.date(LeakWarning.created_at) == report_date,
            (LeakWarning.area_id == area_id) if area_id else True
        )
    )
    leak_result = await db.execute(leak_query)
    all_leaks = leak_result.scalars().all()
    leak_count = len(all_leaks)
    leak_resolved = sum(1 for w in all_leaks if w.status != "active")

    leak_detection_rate = Decimal(str(leak_resolved / leak_count * 100)) if leak_count > 0 else Decimal("100")

    wo_query = select(WorkOrder).where(
        and_(
            func.date(WorkOrder.created_at) == report_date,
            (WorkOrder.area_id == area_id) if area_id else True
        )
    )
    wo_result = await db.execute(wo_query)
    all_wos = wo_result.scalars().all()
    wo_count = len(all_wos)
    wo_completed = sum(1 for w in all_wos if w.status == WorkOrderStatus.COMPLETED)

    resp_times = [w.response_minutes for w in all_wos if w.response_minutes]
    res_times = [w.resolution_minutes for w in all_wos if w.resolution_minutes]
    avg_resp = Decimal(str(sum(resp_times) / len(resp_times))) if resp_times else Decimal("0")
    avg_res = Decimal(str(sum(res_times) / len(res_times))) if res_times else Decimal("0")

    bill_result = await db.execute(
        select(func.count(Bill.id)).select_from(
            Bill
        ).join(ResidentAccount, ResidentAccount.id == Bill.account_id).where(
            and_(
                Bill.due_date < report_date,
                Bill.status.in_(["unpaid", "partial", "overdue"]),
                (ResidentAccount.area_id == area_id) if area_id else True
            )
        )
    )
    overdue_count = bill_result.scalar_one() or 0

    rev_result = await db.execute(
        select(func.sum(Bill.paid_amount)).select_from(
            Bill
        ).join(ResidentAccount, ResidentAccount.id == Bill.account_id).where(
            and_(
                func.date(Bill.paid_at) == report_date if Bill.paid_at else False,
                (ResidentAccount.area_id == area_id) if area_id else True
            )
        )
    )
    revenue = Decimal(str(rev_result.scalar_one() or 0))

    report = DailyReport(
        report_date=report_date,
        area_id=area_id,
        total_gas_volume=total_volume,
        peak_hour_volume=peak_vol,
        leak_count=leak_count,
        leak_resolved=leak_resolved,
        leak_detection_rate=leak_detection_rate,
        work_order_count=wo_count,
        work_order_completed=wo_completed,
        avg_response_minutes=avg_resp,
        avg_resolution_minutes=avg_res,
        complaint_count=0,
        complaint_rate=Decimal("0"),
        new_connection_count=0,
        overdue_bill_count=overdue_count,
        revenue=revenue
    )
    db.add(report)
    await db.flush()
    return report
