from typing import Optional, List, Dict, Tuple
from datetime import datetime, date, timedelta, time
from decimal import Decimal
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import and_, func, extract
from app.models import (
    SensorReading, Sensor, LeakWarning, WarningLevel,
    PressureStation, ControlLog, Area, WorkOrder,
    WorkOrderStatus, ResidentReport, Bill, DailyReport,
    ResidentAccount, GasStatus, BillStatus, WorkOrderType,
    NotificationType, UserRole, User
)
from app.services.work_order_service import assign_work_order
from app.services import notification_service
from app.utils.security import generate_order_no
from app.config import settings
import json
from app.utils.logger import get_logger

logger = get_logger(__name__)


async def process_sensor_reading(
    db: AsyncSession,
    sensor: Sensor,
    value: Decimal,
    reading_time: datetime
) -> Tuple[SensorReading, Optional[int], Optional[int]]:
    is_anomaly = False
    warning_id: Optional[int] = None
    work_order_id: Optional[int] = None

    if sensor.threshold_min is not None and value < sensor.threshold_min:
        is_anomaly = True
    if sensor.threshold_max is not None and value > sensor.threshold_max:
        is_anomaly = True
    if sensor.type.value == "leak" and sensor.leak_threshold is not None and value > sensor.leak_threshold:
        is_anomaly = True
        try:
            warning_id, work_order_id = await handle_leak_detection(db, sensor, value, reading_time)
        except Exception as e:
            logger.error(f"泄漏检测处理失败: sensor_id={sensor.id}, error={e}", exc_info=True)

    reading = SensorReading(
        sensor_id=sensor.id,
        value=value,
        reading_time=reading_time,
        is_anomaly=is_anomaly
    )
    db.add(reading)

    sensor.last_reading = reading_time
    await db.flush()
    return reading, warning_id, work_order_id


async def handle_leak_detection(
    db: AsyncSession,
    sensor: Sensor,
    concentration: Decimal,
    detected_at: datetime
) -> Tuple[Optional[int], Optional[int]]:
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
        return existing.id, None

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

    work_order_id: Optional[int] = None
    try:
        level_label = {
            WarningLevel.LEVEL_1: "一级",
            WarningLevel.LEVEL_2: "二级",
            WarningLevel.LEVEL_3: "三级",
            WarningLevel.LEVEL_4: "四级(紧急)"
        }.get(level, "一般")

        priority = 1
        if level in (WarningLevel.LEVEL_3, WarningLevel.LEVEL_4):
            priority = 1
        elif level == WarningLevel.LEVEL_2:
            priority = 2
        else:
            priority = 3

        wo_order_no = generate_order_no("WO")
        work_order = WorkOrder(
            order_no=wo_order_no,
            type=WorkOrderType.LEAK_REPAIR,
            title=f"[{level_label}泄漏抢修] 传感器{sensor.code}",
            description=f"传感器:{sensor.name}(编码:{sensor.code})检测到燃气泄漏\n浓度值:{concentration}\n预警级别:{level_label}\n现场地址: 经度{sensor.longitude}, 纬度{sensor.latitude}",
            warning_id=warning.id,
            area_id=sensor.area_id,
            priority=priority,
            longitude=sensor.longitude,
            latitude=sensor.latitude,
            level=level,
            status="pending",
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow()
        )
        db.add(work_order)
        await db.flush()
        work_order_id = work_order.id

        try:
            await assign_work_order(db, work_order, prefer_area_id=warning.area_id)
            await db.flush()
        except Exception as e:
            logger.error(f"工单分配失败: work_order_id={work_order.id}, error={e}", exc_info=True)

        try:
            if work_order.assignee_id:
                await notification_service.create_notification(
                    db,
                    work_order.assignee_id,
                    NotificationType.WARNING,
                    f"【紧急】{level_label}泄漏抢修工单已分配",
                    f"工单:{work_order.order_no} 位置:传感器{sensor.code} 浓度:{concentration}",
                    work_order.id,
                    "work_order"
                )

            if sensor.area_id:
                area_q = await db.execute(select(Area).where(Area.id == sensor.area_id))
                area = area_q.scalar_one_or_none()
                if area and area.manager_id:
                    await notification_service.create_notification(
                        db,
                        area.manager_id,
                        NotificationType.WARNING,
                        f"【区域预警】{level_label}泄漏事件 - {area.name}",
                        f"传感器:{sensor.code} 浓度:{concentration} 已生成工单:{work_order.order_no}",
                        warning.id,
                        "leak_warning"
                    )

            dispatcher_ids_q = await db.execute(
                select(User.id).where(User.role == UserRole.DISPATCHER, User.is_active == True)
            )
            dispatcher_ids = dispatcher_ids_q.scalars().all()
            for did in list(dispatcher_ids)[:5]:
                await notification_service.create_notification(
                    db,
                    did,
                    NotificationType.WARNING,
                    f"【调度】{level_label}泄漏事件",
                    f"传感器:{sensor.code} 工单:{work_order.order_no} 已派给: ID{work_order.assignee_id}",
                    warning.id,
                    "leak_warning"
                )
        except Exception as e:
            logger.error(f"发送通知失败: warning_id={warning.id}, error={e}", exc_info=True)

    except Exception as e:
        logger.error(f"创建泄漏工单失败: sensor_id={sensor.id}, warning_id={warning.id}, error={e}", exc_info=True)

    return warning.id, work_order_id


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
    conditions = [DailyReport.report_date == report_date]
    if area_id is None:
        conditions.append(DailyReport.area_id.is_(None))
    else:
        conditions.append(DailyReport.area_id == area_id)
    existing = await db.execute(select(DailyReport).where(and_(*conditions)))
    report = existing.scalar_one_or_none()

    start_dt = datetime.combine(report_date, time.min)
    end_dt = start_dt + timedelta(days=1)

    flow_conditions = [
        Sensor.type == "flow",
        SensorReading.reading_time >= start_dt,
        SensorReading.reading_time < end_dt
    ]
    if area_id:
        flow_conditions.append(Sensor.area_id == area_id)

    flow_result = await db.execute(
        select(func.sum(SensorReading.value)).select_from(
            SensorReading
        ).join(Sensor, Sensor.id == SensorReading.sensor_id).where(
            and_(*flow_conditions)
        )
    )
    total_gas_volume = Decimal(str(flow_result.scalar_one_or_none() or 0))

    peak_conditions = [
        Sensor.type == "flow",
        SensorReading.reading_time >= start_dt,
        SensorReading.reading_time < end_dt,
        extract("hour", SensorReading.reading_time).in_(settings.PEAK_HOURS)
    ]
    if area_id:
        peak_conditions.append(Sensor.area_id == area_id)

    peak_result = await db.execute(
        select(func.sum(SensorReading.value)).select_from(
            SensorReading
        ).join(Sensor, Sensor.id == SensorReading.sensor_id).where(
            and_(*peak_conditions)
        )
    )
    peak_hour_volume = Decimal(str(peak_result.scalar_one_or_none() or 0))

    leak_conditions = [func.date(LeakWarning.created_at) == report_date]
    if area_id:
        leak_conditions.append(LeakWarning.area_id == area_id)

    leak_query = select(LeakWarning).where(and_(*leak_conditions))
    leak_result = await db.execute(leak_query)
    all_leaks = leak_result.scalars().all()
    leak_count = len(all_leaks)
    leak_resolved = sum(1 for w in all_leaks if w.status != "active")
    leak_detection_rate = Decimal(str(100.0 * leak_resolved / leak_count)) if leak_count > 0 else Decimal("0")

    wo_conditions = [func.date(WorkOrder.created_at) == report_date]
    if area_id:
        wo_conditions.append(WorkOrder.area_id == area_id)

    wo_query = select(WorkOrder).where(and_(*wo_conditions))
    wo_result = await db.execute(wo_query)
    all_wos = wo_result.scalars().all()
    work_order_count = len(all_wos)
    work_order_completed = sum(1 for w in all_wos if w.status == "completed")

    resp_times = [w.response_minutes for w in all_wos if w.response_minutes is not None]
    res_times = [w.resolution_minutes for w in all_wos if w.resolution_minutes is not None]
    avg_response = Decimal(str(sum(resp_times) / len(resp_times))) if resp_times else Decimal("0")
    avg_resolution = Decimal(str(sum(res_times) / len(res_times))) if res_times else Decimal("0")

    overdue_conditions = [
        Bill.due_date < report_date,
        Bill.status != "paid"
    ]
    if area_id:
        overdue_conditions.append(ResidentAccount.area_id == area_id)

    bill_result = await db.execute(
        select(func.count(Bill.id)).select_from(
            Bill
        ).join(ResidentAccount, ResidentAccount.id == Bill.account_id).where(
            and_(*overdue_conditions)
        )
    )
    overdue_bill_count = int(bill_result.scalar_one() or 0)

    revenue_conditions = [func.date(Bill.paid_at) == report_date]
    if area_id:
        revenue_conditions.append(ResidentAccount.area_id == area_id)

    rev_result = await db.execute(
        select(func.sum(Bill.paid_amount)).select_from(
            Bill
        ).join(ResidentAccount, ResidentAccount.id == Bill.account_id).where(
            and_(*revenue_conditions)
        )
    )
    revenue = Decimal(str(rev_result.scalar_one_or_none() or 0))

    if report:
        report.total_gas_volume = total_gas_volume
        report.peak_hour_volume = peak_hour_volume
        report.leak_count = leak_count
        report.leak_resolved = leak_resolved
        report.leak_detection_rate = leak_detection_rate
        report.work_order_count = work_order_count
        report.work_order_completed = work_order_completed
        report.avg_response_minutes = avg_response
        report.avg_resolution_minutes = avg_resolution
        report.overdue_bill_count = overdue_bill_count
        report.revenue = revenue
        report.complaint_count = 0
        report.complaint_rate = Decimal("0")
        report.new_connection_count = 0
        await db.flush()
    else:
        report = DailyReport(
            report_date=report_date,
            area_id=area_id,
            total_gas_volume=total_gas_volume,
            peak_hour_volume=peak_hour_volume,
            leak_count=leak_count,
            leak_resolved=leak_resolved,
            leak_detection_rate=leak_detection_rate,
            work_order_count=work_order_count,
            work_order_completed=work_order_completed,
            avg_response_minutes=avg_response,
            avg_resolution_minutes=avg_resolution,
            complaint_count=0,
            complaint_rate=Decimal("0"),
            new_connection_count=0,
            overdue_bill_count=overdue_bill_count,
            revenue=revenue
        )
        db.add(report)
        await db.flush()

    await db.commit()
    await db.refresh(report)

    verify_conditions = [DailyReport.report_date == report_date]
    if area_id is None:
        verify_conditions.append(DailyReport.area_id.is_(None))
    else:
        verify_conditions.append(DailyReport.area_id == area_id)
    verify_result = await db.execute(select(DailyReport).where(and_(*verify_conditions)))
    verified = verify_result.scalar_one_or_none()
    if not verified or verified.id != report.id:
        logger.error(f"日报生成后校验失败: report_date={report_date}, area_id={area_id}, report_id={report.id}")
        raise RuntimeError(f"日报生成校验失败，(report_date={report_date}, area_id={area_id}) 组合不存在或不匹配")

    return report
