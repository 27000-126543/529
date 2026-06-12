from typing import Optional, List
from datetime import datetime, date, timedelta
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import and_, func, or_
from app.database import get_db
from app.models import (
    Sensor, SensorType, PressureStation, ControlLog, LeakWarning, WarningLevel,
    User, UserRole, WorkOrder, WorkOrderStatus, WorkOrderType, Area
)
from app.schemas.sensor import (
    SensorCreate, SensorUpdate, SensorInfo, SensorListResponse,
    SensorReadingCreate, SensorReadingBatch, SensorReadingInfo, SensorReadingListResponse,
    PressureStationCreate, PressureStationUpdate, PressureStationInfo, PressureStationListResponse,
    ControlLogCreate, ControlLogInfo, ControlLogListResponse,
    LeakWarningCreate, LeakWarningUpdate, LeakWarningInfo, LeakWarningListResponse,
    WorkOrderCreate, WorkOrderUpdate, WorkOrderInfo, WorkOrderListResponse,
    WorkOrderAssign, WorkOrderAccept, WorkOrderComplete
)
from app.schemas.common import IdResponse, SuccessResponse
from app.utils.security import get_current_user, require_roles
from app.services import sensor_service, work_order_service, notification_service
from app.models import NotificationType
from app.utils.logger import get_logger
import json
import asyncio

logger = get_logger(__name__)
router = APIRouter(prefix="/api/v1/sensors", tags=["传感器与数据采集"])


@router.post("", response_model=IdResponse, dependencies=[Depends(require_roles(UserRole.ADMIN, UserRole.DISPATCHER))])
async def create_sensor(sensor_data: SensorCreate, db: AsyncSession = Depends(get_db)):
    existing = await db.execute(select(Sensor).where(Sensor.code == sensor_data.code))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="传感器编码已存在")
    sensor = Sensor(**sensor_data.model_dump())
    db.add(sensor)
    await db.commit()
    await db.refresh(sensor)
    return IdResponse(id=sensor.id)


@router.get("", response_model=SensorListResponse)
async def list_sensors(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    type: Optional[SensorType] = None,
    pressure_station_id: Optional[int] = None,
    area_id: Optional[int] = None,
    status: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    query = select(Sensor)
    if type:
        query = query.where(Sensor.type == type)
    if pressure_station_id:
        query = query.where(Sensor.pressure_station_id == pressure_station_id)
    if area_id:
        query = query.where(Sensor.area_id == area_id)
    elif current_user.area_id and current_user.role in [UserRole.AREA_MANAGER, UserRole.MAINTENANCE]:
        query = query.where(Sensor.area_id == current_user.area_id)
    if status:
        query = query.where(Sensor.status == status)

    total = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar_one()
    query = query.order_by(Sensor.id.desc()).offset((page - 1) * page_size).limit(page_size)
    items = (await db.execute(query)).scalars().all()

    return SensorListResponse(
        total=total,
        page=page,
        page_size=page_size,
        total_pages=(total + page_size - 1) // page_size,
        items=[SensorInfo.model_validate(s) for s in items]
    )


@router.get("/{sensor_id}", response_model=SensorInfo)
async def get_sensor(sensor_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Sensor).where(Sensor.id == sensor_id))
    sensor = result.scalar_one_or_none()
    if not sensor:
        raise HTTPException(status_code=404, detail="传感器不存在")
    return SensorInfo.model_validate(sensor)


@router.put("/{sensor_id}", response_model=SuccessResponse, dependencies=[Depends(require_roles(UserRole.ADMIN, UserRole.DISPATCHER))])
async def update_sensor(sensor_id: int, data: SensorUpdate, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Sensor).where(Sensor.id == sensor_id))
    sensor = result.scalar_one_or_none()
    if not sensor:
        raise HTTPException(status_code=404, detail="传感器不存在")
    for k, v in data.model_dump(exclude_unset=True).items():
        if v is not None:
            setattr(sensor, k, v)
    await db.commit()
    return SuccessResponse(message="传感器更新成功")


@router.post("/readings", status_code=201)
async def submit_reading(
    data: SensorReadingCreate,
    db: AsyncSession = Depends(get_db)
):
    try:
        result = await db.execute(select(Sensor).where(Sensor.id == data.sensor_id))
        sensor = result.scalar_one_or_none()
        if not sensor:
            raise HTTPException(status_code=404, detail="传感器不存在")

        reading, warning_id, work_order_id = await sensor_service.process_sensor_reading(
            db, sensor, data.value, data.reading_time
        )
        await db.commit()

        response_data = {
            "success": True,
            "reading_id": reading.id,
            "is_anomaly": reading.is_anomaly
        }
        if warning_id is not None:
            response_data["warning_id"] = warning_id
        if work_order_id is not None:
            response_data["work_order_id"] = work_order_id
        return response_data
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"提交传感器读数失败: sensor_id={data.sensor_id}, error={e}", exc_info=True)
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"服务器内部错误: {str(e)}")


@router.post("/readings/batch", status_code=201)
async def submit_batch_readings(
    batch: SensorReadingBatch,
    db: AsyncSession = Depends(get_db)
):
    try:
        sensor_ids = {r.sensor_id for r in batch.readings}
        result = await db.execute(select(Sensor).where(Sensor.id.in_(list(sensor_ids))))
        sensors = {s.id: s for s in result.scalars().all()}

        anomaly_count = 0
        success_count = 0
        failed_count = 0
        reading_results = []

        for r in batch.readings:
            sensor = sensors.get(r.sensor_id)
            if not sensor:
                failed_count += 1
                reading_results.append({
                    "sensor_id": r.sensor_id,
                    "success": False,
                    "error": "传感器不存在"
                })
                continue

            try:
                reading, warning_id, work_order_id = await sensor_service.process_sensor_reading(
                    db, sensor, r.value, r.reading_time
                )
                if reading.is_anomaly:
                    anomaly_count += 1
                success_count += 1

                result_item = {
                    "sensor_id": r.sensor_id,
                    "success": True,
                    "reading_id": reading.id,
                    "is_anomaly": reading.is_anomaly
                }
                if warning_id is not None:
                    result_item["warning_id"] = warning_id
                if work_order_id is not None:
                    result_item["work_order_id"] = work_order_id
                reading_results.append(result_item)
            except Exception as e:
                logger.error(f"处理传感器读数失败: sensor_id={r.sensor_id}, error={e}", exc_info=True)
                failed_count += 1
                reading_results.append({
                    "sensor_id": r.sensor_id,
                    "success": False,
                    "error": str(e)
                })
                continue

        await db.commit()
        return {
            "success": True,
            "total": len(batch.readings),
            "success_count": success_count,
            "failed_count": failed_count,
            "anomaly_count": anomaly_count,
            "results": reading_results
        }
    except Exception as e:
        logger.error(f"批量提交传感器读数失败: error={e}", exc_info=True)
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"服务器内部错误: {str(e)}")


@router.get("/readings/{sensor_id}", response_model=SensorReadingListResponse)
async def get_sensor_readings(
    sensor_id: int,
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=1000),
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
    anomaly_only: bool = False,
    db: AsyncSession = Depends(get_db)
):
    from app.models import SensorReading as SR
    query = select(SR).where(SR.sensor_id == sensor_id)
    if start_time:
        query = query.where(SR.reading_time >= start_time)
    if end_time:
        query = query.where(SR.reading_time <= end_time)
    if anomaly_only:
        query = query.where(SR.is_anomaly == True)

    total = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar_one()
    query = query.order_by(SR.reading_time.desc()).offset((page - 1) * page_size).limit(page_size)
    items = (await db.execute(query)).scalars().all()

    return SensorReadingListResponse(
        total=total,
        page=page,
        page_size=page_size,
        total_pages=(total + page_size - 1) // page_size,
        items=[SensorReadingInfo.model_validate(r) for r in items]
    )


station_router = APIRouter(prefix="/api/v1/pressure-stations", tags=["调压站与调度"])


@station_router.post("", response_model=IdResponse, dependencies=[Depends(require_roles(UserRole.ADMIN, UserRole.DISPATCHER))])
async def create_pressure_station(data: PressureStationCreate, db: AsyncSession = Depends(get_db)):
    existing = await db.execute(select(PressureStation).where(PressureStation.code == data.code))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="调压站编码已存在")
    ps = PressureStation(**data.model_dump())
    db.add(ps)
    await db.commit()
    await db.refresh(ps)
    return IdResponse(id=ps.id)


@station_router.get("", response_model=PressureStationListResponse)
async def list_pressure_stations(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    area_id: Optional[int] = None,
    status: Optional[str] = None,
    db: AsyncSession = Depends(get_db)
):
    query = select(PressureStation)
    if area_id:
        query = query.where(PressureStation.area_id == area_id)
    if status:
        query = query.where(PressureStation.status == status)

    total = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar_one()
    query = query.order_by(PressureStation.id.desc()).offset((page - 1) * page_size).limit(page_size)
    items = (await db.execute(query)).scalars().all()

    return PressureStationListResponse(
        total=total,
        page=page,
        page_size=page_size,
        total_pages=(total + page_size - 1) // page_size,
        items=[PressureStationInfo.model_validate(s) for s in items]
    )


@station_router.get("/{station_id}", response_model=PressureStationInfo)
async def get_pressure_station(station_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(PressureStation).where(PressureStation.id == station_id))
    ps = result.scalar_one_or_none()
    if not ps:
        raise HTTPException(status_code=404, detail="调压站不存在")
    return PressureStationInfo.model_validate(ps)


@station_router.put("/{station_id}", response_model=SuccessResponse, dependencies=[Depends(require_roles(UserRole.ADMIN, UserRole.DISPATCHER))])
async def update_pressure_station(station_id: int, data: PressureStationUpdate, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(PressureStation).where(PressureStation.id == station_id))
    ps = result.scalar_one_or_none()
    if not ps:
        raise HTTPException(status_code=404, detail="调压站不存在")
    for k, v in data.model_dump(exclude_unset=True).items():
        if v is not None:
            setattr(ps, k, v)
    await db.commit()
    return SuccessResponse(message="调压站更新成功")


@station_router.post("/control-log", response_model=IdResponse, dependencies=[Depends(require_roles(UserRole.DISPATCHER, UserRole.ADMIN))])
async def create_control_log(
    data: ControlLogCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    if data.dispatcher_id is None:
        data.dispatcher_id = current_user.id
    log = ControlLog(**data.model_dump())
    db.add(log)
    await db.commit()
    await db.refresh(log)
    return IdResponse(id=log.id)


@station_router.get("/control-logs", response_model=ControlLogListResponse)
async def list_control_logs(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    pressure_station_id: Optional[int] = None,
    dispatcher_id: Optional[int] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    is_auto: Optional[bool] = None,
    db: AsyncSession = Depends(get_db)
):
    query = select(ControlLog)
    if pressure_station_id:
        query = query.where(ControlLog.pressure_station_id == pressure_station_id)
    if dispatcher_id:
        query = query.where(ControlLog.dispatcher_id == dispatcher_id)
    if start_date:
        query = query.where(func.date(ControlLog.created_at) >= start_date)
    if end_date:
        query = query.where(func.date(ControlLog.created_at) <= end_date)
    if is_auto is not None:
        query = query.where(ControlLog.is_auto == is_auto)

    total = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar_one()
    query = query.order_by(ControlLog.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    items = (await db.execute(query)).scalars().all()

    return ControlLogListResponse(
        total=total,
        page=page,
        page_size=page_size,
        total_pages=(total + page_size - 1) // page_size,
        items=[ControlLogInfo.model_validate(l) for l in items]
    )


warning_router = APIRouter(prefix="/api/v1/warnings", tags=["泄漏预警"])


@warning_router.get("", response_model=LeakWarningListResponse)
async def list_leak_warnings(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    warning_id: Optional[int] = None,
    area_id: Optional[int] = None,
    level: Optional[WarningLevel] = None,
    status: Optional[str] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    query = select(LeakWarning)
    if warning_id:
        query = query.where(LeakWarning.id == warning_id)
    if area_id:
        query = query.where(LeakWarning.area_id == area_id)
    elif current_user.area_id and current_user.role in [UserRole.AREA_MANAGER, UserRole.MAINTENANCE]:
        query = query.where(LeakWarning.area_id == current_user.area_id)
    if level:
        query = query.where(LeakWarning.level == level)
    if status:
        query = query.where(LeakWarning.status == status)
    if start_date:
        query = query.where(func.date(LeakWarning.created_at) >= start_date)
    if end_date:
        query = query.where(func.date(LeakWarning.created_at) <= end_date)

    total = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar_one()
    query = query.order_by(LeakWarning.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    items = (await db.execute(query)).scalars().all()

    return LeakWarningListResponse(
        total=total,
        page=page,
        page_size=page_size,
        total_pages=(total + page_size - 1) // page_size,
        items=[LeakWarningInfo.model_validate(w) for w in items]
    )


@warning_router.get("/{warning_id}", response_model=LeakWarningInfo)
async def get_leak_warning(warning_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(LeakWarning).where(LeakWarning.id == warning_id))
    w = result.scalar_one_or_none()
    if not w:
        raise HTTPException(status_code=404, detail="预警不存在")
    return LeakWarningInfo.model_validate(w)


@warning_router.put("/{warning_id}", response_model=SuccessResponse)
async def update_leak_warning(
    warning_id: int,
    data: LeakWarningUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    result = await db.execute(select(LeakWarning).where(LeakWarning.id == warning_id))
    w = result.scalar_one_or_none()
    if not w:
        raise HTTPException(status_code=404, detail="预警不存在")
    for k, v in data.model_dump(exclude_unset=True).items():
        if v is not None:
            setattr(w, k, v)
    await db.commit()
    return SuccessResponse(message="预警更新成功")


work_order_router = APIRouter(prefix="/api/v1/work-orders", tags=["工单管理"])


@work_order_router.post("", response_model=IdResponse)
async def create_work_order(
    data: WorkOrderCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    wo = await work_order_service.create_work_order(db, data, owner_id=current_user.id)
    await db.commit()
    await db.refresh(wo)

    if wo.assignee_id:
        await notification_service.create_notification(
            db, wo.assignee_id, NotificationType.WORK_ORDER,
            f"您有新的工单: {wo.title}",
            f"工单编号: {wo.order_no}, 优先级: {wo.priority}",
            wo.id, "work_order"
        )
    await db.commit()
    return IdResponse(id=wo.id)


@work_order_router.get("", response_model=WorkOrderListResponse)
async def list_work_orders(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    warning_id: Optional[int] = None,
    resident_report_id: Optional[int] = None,
    type: Optional[WorkOrderType] = None,
    status: Optional[WorkOrderStatus] = None,
    area_id: Optional[int] = None,
    team_id: Optional[int] = None,
    assignee_id: Optional[int] = None,
    mine: bool = False,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    query = select(WorkOrder)
    if warning_id:
        query = query.where(WorkOrder.warning_id == warning_id)
    if resident_report_id:
        query = query.where(WorkOrder.resident_report_id == resident_report_id)
    if type:
        query = query.where(WorkOrder.type == type)
    if status:
        query = query.where(WorkOrder.status == status)
    if area_id:
        query = query.where(WorkOrder.area_id == area_id)
    if team_id:
        query = query.where(WorkOrder.team_id == team_id)
    if assignee_id:
        query = query.where(WorkOrder.assignee_id == assignee_id)
    elif mine:
        query = query.where(or_(
            WorkOrder.assignee_id == current_user.id,
            WorkOrder.owner_id == current_user.id
        ))
    elif current_user.role == UserRole.MAINTENANCE:
        query = query.where(WorkOrder.assignee_id == current_user.id)
    elif current_user.area_id and current_user.role in [UserRole.AREA_MANAGER]:
        query = query.where(WorkOrder.area_id == current_user.area_id)

    if start_date:
        query = query.where(func.date(WorkOrder.created_at) >= start_date)
    if end_date:
        query = query.where(func.date(WorkOrder.created_at) <= end_date)

    total = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar_one()
    query = query.order_by(WorkOrder.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    items = (await db.execute(query)).scalars().all()

    return WorkOrderListResponse(
        total=total,
        page=page,
        page_size=page_size,
        total_pages=(total + page_size - 1) // page_size,
        items=[WorkOrderInfo.model_validate(w) for w in items]
    )


@work_order_router.get("/{order_id}", response_model=WorkOrderInfo)
async def get_work_order(order_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(WorkOrder).where(WorkOrder.id == order_id))
    wo = result.scalar_one_or_none()
    if not wo:
        raise HTTPException(status_code=404, detail="工单不存在")
    return WorkOrderInfo.model_validate(wo)


@work_order_router.post("/{order_id}/assign", response_model=SuccessResponse, dependencies=[Depends(require_roles(UserRole.DISPATCHER, UserRole.ADMIN, UserRole.AREA_MANAGER))])
async def assign_work_order(
    order_id: int,
    data: WorkOrderAssign,
    db: AsyncSession = Depends(get_db)
):
    result = await db.execute(select(WorkOrder).where(WorkOrder.id == order_id))
    wo = result.scalar_one_or_none()
    if not wo:
        raise HTTPException(status_code=404, detail="工单不存在")

    if data.team_id:
        wo.team_id = data.team_id
    wo = await work_order_service.assign_work_order(db, wo, data.team_id, data.assignee_id)

    if wo.assignee_id:
        await notification_service.create_notification(
            db, wo.assignee_id, NotificationType.WORK_ORDER,
            f"工单已分配给您: {wo.title}",
            f"工单编号: {wo.order_no}",
            wo.id, "work_order"
        )
    await db.commit()
    return SuccessResponse(message="工单分配成功")


@work_order_router.post("/{order_id}/accept", response_model=SuccessResponse)
async def accept_work_order(
    order_id: int,
    _: WorkOrderAccept,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    result = await db.execute(select(WorkOrder).where(WorkOrder.id == order_id))
    wo = result.scalar_one_or_none()
    if not wo:
        raise HTTPException(status_code=404, detail="工单不存在")
    if wo.assignee_id != current_user.id:
        raise HTTPException(status_code=403, detail="只能接受分配给自己的工单")

    await work_order_service.accept_work_order(db, wo, current_user.id)
    wo.started_at = datetime.utcnow()
    wo.status = WorkOrderStatus.IN_PROGRESS
    await db.commit()
    return SuccessResponse(message="工单已接受")


@work_order_router.post("/{order_id}/complete", response_model=SuccessResponse)
async def complete_work_order(
    order_id: int,
    data: WorkOrderComplete,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    result = await db.execute(select(WorkOrder).where(WorkOrder.id == order_id))
    wo = result.scalar_one_or_none()
    if not wo:
        raise HTTPException(status_code=404, detail="工单不存在")
    if wo.assignee_id != current_user.id and current_user.role not in [UserRole.ADMIN, UserRole.DISPATCHER]:
        raise HTTPException(status_code=403, detail="无权限完成此工单")

    await work_order_service.complete_work_order(db, wo, data.result, data.images)

    if wo.owner_id and wo.owner_id != current_user.id:
        await notification_service.create_notification(
            db, wo.owner_id, NotificationType.WORK_ORDER,
            f"工单已完成: {wo.title}",
            f"处理结果: {data.result[:100]}",
            wo.id, "work_order"
        )
    await db.commit()
    return SuccessResponse(message="工单已完成")


@work_order_router.post("/{order_id}/escalate", response_model=SuccessResponse)
async def escalate_work_order(
    order_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    result = await db.execute(select(WorkOrder).where(WorkOrder.id == order_id))
    wo = result.scalar_one_or_none()
    if not wo:
        raise HTTPException(status_code=404, detail="工单不存在")

    wo = await work_order_service.escalate_work_order(db, wo, reason=f"由用户 {current_user.real_name or current_user.username} 申请升级")

    area_result = await db.execute(
        select(Area).where(Area.id == wo.area_id)
    )
    area = area_result.scalar_one_or_none()
    if area and area.manager_id:
        await notification_service.create_notification(
            db, area.manager_id, NotificationType.WARNING,
            f"工单已升级: {wo.title}",
            f"工单编号: {wo.order_no}，请关注处理",
            wo.id, "work_order_escalation"
        )

    if wo.assignee_id:
        await notification_service.create_notification(
            db, wo.assignee_id, NotificationType.WORK_ORDER,
            f"升级工单已分配: {wo.title}",
            f"工单编号: {wo.order_no}",
            wo.id, "work_order"
        )
    await db.commit()
    return SuccessResponse(message="工单已升级")
