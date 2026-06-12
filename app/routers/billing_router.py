from typing import Optional, List
from datetime import datetime, date, timedelta
from decimal import Decimal
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import and_, func, or_
from app.database import get_db
from app.models import (
    User, UserRole, ResidentAccount, ResidentReport, WorkOrder,
    GasPriceTier, Bill, BillStatus, GasStatus, Payment, MeterReading,
    EngineeringProject, ApprovalStage, ApprovalStatus, ApprovalRecord,
    GasSupplier, GasInventory, GasPurchasePlan, PurchaseStatus,
    DailyReport, NotificationType, Area
)
from app.schemas.billing import (
    ResidentAccountCreate, ResidentAccountUpdate, ResidentAccountInfo, ResidentAccountListResponse,
    ResidentReportCreate, ResidentReportInfo, ResidentReportListResponse,
    GasPriceTierCreate, GasPriceTierInfo, GasPriceTierListResponse,
    BillInfo, BillListResponse, PaymentCreate, PaymentInfo,
    MeterReadingCreate, MeterReadingInfo,
    EngineeringProjectCreate, EngineeringProjectInfo, EngineeringProjectListResponse,
    ApprovalRecordUpdate, ApprovalRecordInfo,
    GasSupplierCreate, GasSupplierInfo,
    GasInventoryCreate, GasInventoryInfo,
    GasPurchasePlanCreate, GasPurchasePlanInfo, GasPurchasePlanListResponse,
    DailyReportInfo, DailyReportListResponse
)
from app.schemas.common import IdResponse, SuccessResponse, ProjectCreateResponse, FinalApprovalResponse, ProjectDetailResponse
from app.utils.security import get_current_user, require_roles, generate_order_no
from app.services import billing_service, project_service, sensor_service, notification_service, work_order_service
from app.schemas.sensor import WorkOrderCreate
from app.models import WorkOrderType
import openpyxl
from io import BytesIO
from fastapi.responses import StreamingResponse

router = APIRouter(prefix="/api/v1/resident", tags=["居民账户与报修"])


@router.post("/accounts", response_model=IdResponse, dependencies=[Depends(require_roles(UserRole.ADMIN, UserRole.DISPATCHER))])
async def create_account(data: ResidentAccountCreate, db: AsyncSession = Depends(get_db)):
    existing = await db.execute(select(ResidentAccount).where(ResidentAccount.account_no == data.account_no))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="账户编号已存在")
    if data.meter_no:
        existing2 = await db.execute(select(ResidentAccount).where(ResidentAccount.meter_no == data.meter_no))
        if existing2.scalar_one_or_none():
            raise HTTPException(status_code=400, detail="燃气表编号已存在")
    acc = ResidentAccount(**data.model_dump())
    db.add(acc)
    await db.commit()
    await db.refresh(acc)
    return IdResponse(id=acc.id)


@router.get("/accounts", response_model=ResidentAccountListResponse)
async def list_accounts(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    area_id: Optional[int] = None,
    gas_status: Optional[GasStatus] = None,
    keyword: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    query = select(ResidentAccount)
    if area_id:
        query = query.where(ResidentAccount.area_id == area_id)
    elif current_user.area_id and current_user.role in [UserRole.AREA_MANAGER, UserRole.COLLECTOR]:
        query = query.where(ResidentAccount.area_id == current_user.area_id)
    if gas_status:
        query = query.where(ResidentAccount.gas_status == gas_status)
    if keyword:
        query = query.where(or_(
            ResidentAccount.account_no.ilike(f"%{keyword}%"),
            ResidentAccount.resident_name.ilike(f"%{keyword}%"),
            ResidentAccount.phone.ilike(f"%{keyword}%"),
            ResidentAccount.meter_no.ilike(f"%{keyword}%")
        ))

    total = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar_one()
    query = query.order_by(ResidentAccount.id.desc()).offset((page - 1) * page_size).limit(page_size)
    items = (await db.execute(query)).scalars().all()

    return ResidentAccountListResponse(
        total=total, page=page, page_size=page_size,
        total_pages=(total + page_size - 1) // page_size,
        items=[ResidentAccountInfo.model_validate(a) for a in items]
    )


@router.get("/accounts/{acc_id}", response_model=ResidentAccountInfo)
async def get_account(acc_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(ResidentAccount).where(ResidentAccount.id == acc_id))
    acc = result.scalar_one_or_none()
    if not acc:
        raise HTTPException(status_code=404, detail="账户不存在")
    return ResidentAccountInfo.model_validate(acc)


@router.put("/accounts/{acc_id}", response_model=SuccessResponse)
async def update_account(
    acc_id: int, data: ResidentAccountUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    result = await db.execute(select(ResidentAccount).where(ResidentAccount.id == acc_id))
    acc = result.scalar_one_or_none()
    if not acc:
        raise HTTPException(status_code=404, detail="账户不存在")

    old_status = acc.gas_status
    for k, v in data.model_dump(exclude_unset=True).items():
        if v is not None:
            setattr(acc, k, v)

    if data.gas_status and data.gas_status != old_status:
        if data.gas_status == GasStatus.RESTRICTED:
            acc.gas_restricted_at = datetime.utcnow()
        elif data.gas_status == GasStatus.NORMAL:
            acc.gas_restricted_at = None

    await db.commit()
    return SuccessResponse(message="账户更新成功")


@router.post("/reports", response_model=IdResponse)
async def create_resident_report(
    data: ResidentReportCreate,
    db: AsyncSession = Depends(get_db)
):
    account_result = await db.execute(select(ResidentAccount).where(ResidentAccount.id == data.account_id))
    account = account_result.scalar_one_or_none()
    if not account:
        raise HTTPException(status_code=404, detail="居民账户不存在")

    report = ResidentReport(
        report_no=generate_order_no("RR"),
        account_id=data.account_id,
        area_id=data.area_id or account.area_id,
        report_type=data.report_type,
        description=data.description,
        longitude=data.longitude or account.longitude,
        latitude=data.latitude or account.latitude,
        status="submitted",
        reported_at=datetime.utcnow()
    )
    db.add(report)
    await db.flush()

    diagnosis, confidence, hist_count = await project_service.auto_diagnose_report(db, report)
    report.auto_diagnosis = diagnosis
    report.confidence_score = confidence
    report.historical_match_count = hist_count

    if confidence >= 60 or "泄漏" in diagnosis or hist_count >= 2:
        wo = await project_service.create_work_order_from_report(db, report, diagnosis)
        report.status = "processing"

        if wo and wo.assignee_id:
            await notification_service.create_notification(
                db, wo.assignee_id, NotificationType.WORK_ORDER,
                f"居民报修工单: {data.report_type}",
                f"自动诊断: {diagnosis}",
                wo.id, "work_order"
            )

    await db.commit()
    await db.refresh(report)
    return IdResponse(id=report.id)


@router.get("/reports", response_model=ResidentReportListResponse)
async def list_reports(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    area_id: Optional[int] = None,
    report_type: Optional[str] = None,
    status: Optional[str] = None,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    query = select(ResidentReport)
    if area_id:
        query = query.where(ResidentReport.area_id == area_id)
    elif current_user.area_id and current_user.role in [UserRole.AREA_MANAGER]:
        query = query.where(ResidentReport.area_id == current_user.area_id)
    if report_type:
        query = query.where(ResidentReport.report_type == report_type)
    if status:
        query = query.where(ResidentReport.status == status)
    if start_date:
        query = query.where(func.date(ResidentReport.reported_at) >= start_date)
    if end_date:
        query = query.where(func.date(ResidentReport.reported_at) <= end_date)

    total = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar_one()
    query = query.order_by(ResidentReport.reported_at.desc()).offset((page - 1) * page_size).limit(page_size)
    items = (await db.execute(query)).scalars().all()

    return ResidentReportListResponse(
        total=total, page=page, page_size=page_size,
        total_pages=(total + page_size - 1) // page_size,
        items=[ResidentReportInfo.model_validate(r) for r in items]
    )


billing_router = APIRouter(prefix="/api/v1/billing", tags=["账单与收费"])


@billing_router.post("/price-tiers", response_model=IdResponse, dependencies=[Depends(require_roles(UserRole.ADMIN))])
async def create_price_tier(data: GasPriceTierCreate, db: AsyncSession = Depends(get_db)):
    tier = GasPriceTier(**data.model_dump())
    db.add(tier)
    await db.commit()
    await db.refresh(tier)
    return IdResponse(id=tier.id)


@billing_router.get("/price-tiers", response_model=GasPriceTierListResponse)
async def list_price_tiers(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    is_active: Optional[bool] = None,
    db: AsyncSession = Depends(get_db)
):
    query = select(GasPriceTier)
    if is_active is not None:
        query = query.where(GasPriceTier.is_active == is_active)

    total = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar_one()
    query = query.order_by(GasPriceTier.tier, GasPriceTier.effective_date.desc()).offset((page - 1) * page_size).limit(page_size)
    items = (await db.execute(query)).scalars().all()

    return GasPriceTierListResponse(
        total=total, page=page, page_size=page_size,
        total_pages=(total + page_size - 1) // page_size,
        items=[GasPriceTierInfo.model_validate(t) for t in items]
    )


@billing_router.get("/bills", response_model=BillListResponse)
async def list_bills(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    account_id: Optional[int] = None,
    billing_month: Optional[str] = None,
    status: Optional[BillStatus] = None,
    area_id: Optional[int] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    query = select(Bill).join(ResidentAccount, ResidentAccount.id == Bill.account_id)
    if account_id:
        query = query.where(Bill.account_id == account_id)
    if billing_month:
        query = query.where(Bill.billing_month == billing_month)
    if status:
        query = query.where(Bill.status == status)
    if area_id:
        query = query.where(ResidentAccount.area_id == area_id)
    elif current_user.area_id and current_user.role in [UserRole.AREA_MANAGER, UserRole.COLLECTOR]:
        query = query.where(ResidentAccount.area_id == current_user.area_id)
    if current_user.role == UserRole.RESIDENT and current_user.resident_account:
        query = query.where(Bill.account_id == current_user.resident_account.id)

    total = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar_one()
    query = query.order_by(Bill.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    items = (await db.execute(query)).scalars().all()

    return BillListResponse(
        total=total, page=page, page_size=page_size,
        total_pages=(total + page_size - 1) // page_size,
        items=[BillInfo.model_validate(b) for b in items]
    )


@billing_router.get("/bills/{bill_id}", response_model=BillInfo)
async def get_bill(bill_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Bill).where(Bill.id == bill_id))
    bill = result.scalar_one_or_none()
    if not bill:
        raise HTTPException(status_code=404, detail="账单不存在")
    return BillInfo.model_validate(bill)


@billing_router.post("/generate-monthly", response_model=SuccessResponse, dependencies=[Depends(require_roles(UserRole.ADMIN, UserRole.DISPATCHER))])
async def trigger_monthly_billing(
    billing_month: str = Query(..., description="格式: YYYY-MM"),
    db: AsyncSession = Depends(get_db)
):
    count = await billing_service.generate_all_monthly_bills(db, billing_month)
    await db.commit()
    return SuccessResponse(message=f"已生成 {count} 份账单")


@billing_router.post("/payments", response_model=IdResponse)
async def create_payment(
    data: PaymentCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    try:
        payment = await billing_service.process_payment(
            db, data.bill_id, data.amount, data.payment_method, data.transaction_no
        )

        bill_result = await db.execute(select(Bill).where(Bill.id == data.bill_id))
        bill = bill_result.scalar_one_or_none()
        if bill and bill.status == BillStatus.PAID and bill.restriction_issued:
            acc_result = await db.execute(select(ResidentAccount).where(ResidentAccount.id == bill.account_id))
            acc = acc_result.scalar_one_or_none()
            if acc and acc.gas_status == GasStatus.RESTRICTED:
                wo_data = WorkOrderCreate(
                    type=WorkOrderType.GAS_RESTORATION,
                    title=f"恢复供气-{acc.account_no}",
                    description=f"用户已缴费，需恢复供气。账户: {acc.resident_name}",
                    area_id=acc.area_id,
                    longitude=acc.longitude,
                    latitude=acc.latitude,
                    priority=1
                )
                wo = await work_order_service.create_work_order(db, wo_data, owner_id=current_user.id)
                if wo and wo.assignee_id:
                    await notification_service.create_notification(
                        db, wo.assignee_id, NotificationType.WORK_ORDER,
                        f"恢复供气工单: {acc.resident_name}",
                        f"账户: {acc.account_no}",
                        wo.id, "work_order"
                    )

        await db.commit()
        await db.refresh(payment)
        return IdResponse(id=payment.id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@billing_router.post("/meter-readings", response_model=IdResponse, dependencies=[Depends(require_roles(UserRole.ADMIN, UserRole.COLLECTOR, UserRole.DISPATCHER))])
async def create_meter_reading(
    data: MeterReadingCreate,
    db: AsyncSession = Depends(get_db)
):
    reading = MeterReading(**data.model_dump())
    db.add(reading)
    await db.flush()

    acc_result = await db.execute(select(ResidentAccount).where(ResidentAccount.id == data.account_id))
    acc = acc_result.scalar_one_or_none()
    if acc:
        acc.meter_reading = data.reading_value
        acc.last_reading_date = data.reading_date

    await db.commit()
    await db.refresh(reading)
    return IdResponse(id=reading.id)


project_router = APIRouter(prefix="/api/v1/projects", tags=["工程改造审批"])


@project_router.post("", response_model=ProjectCreateResponse)
async def create_project(
    data: EngineeringProjectCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    try:
        project = await project_service.create_project(db, data, applicant_id=current_user.id)

        safety_stages = await db.execute(
            select(ApprovalRecord).where(
                and_(
                    ApprovalRecord.project_id == project.id,
                    ApprovalRecord.stage == ApprovalStage.SAFETY
                )
            )
        )
        stage = safety_stages.scalar_one_or_none()
        if stage:
            area_result = await db.execute(select(User).where(
                and_(User.role == UserRole.SAFETY_INSPECTOR, User.is_active == True)
            ))
            safety_users = area_result.scalars().all()
            for u in safety_users[:5]:
                await notification_service.create_notification(
                    db, u.id, NotificationType.APPROVAL,
                    f"待审批: {data.name}",
                    f"工程编号: {project.project_no}，请及时审批",
                    project.id, "project"
                )

        await db.commit()
        await db.refresh(project)
        return ProjectCreateResponse(
            success=True,
            project_id=project.id,
            project_no=project.project_no,
            message="申报成功"
        )
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"申报失败: {str(e)}")


@project_router.get("", response_model=EngineeringProjectListResponse)
async def list_projects(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    area_id: Optional[int] = None,
    approval_status: Optional[ApprovalStatus] = None,
    current_stage: Optional[ApprovalStage] = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    query = select(EngineeringProject)
    if area_id:
        query = query.where(EngineeringProject.area_id == area_id)
    if approval_status:
        query = query.where(EngineeringProject.approval_status == approval_status)
    if current_stage:
        query = query.where(EngineeringProject.current_stage == current_stage)

    total = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar_one()
    query = query.order_by(EngineeringProject.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    items = (await db.execute(query)).scalars().all()

    return EngineeringProjectListResponse(
        total=total, page=page, page_size=page_size,
        total_pages=(total + page_size - 1) // page_size,
        items=[EngineeringProjectInfo.model_validate(p) for p in items]
    )


@project_router.get("/{project_id}", response_model=ProjectDetailResponse)
async def get_project(project_id: int, db: AsyncSession = Depends(get_db)):
    try:
        result = await db.execute(select(EngineeringProject).where(EngineeringProject.id == project_id))
        p = result.scalar_one_or_none()
        if not p:
            raise HTTPException(status_code=404, detail="工程项目不存在")

        approvals_result = await db.execute(
            select(ApprovalRecord).where(ApprovalRecord.project_id == project_id).order_by(ApprovalRecord.id)
        )
        approvals = approvals_result.scalars().all()

        stages_order = [
            ApprovalStage.SAFETY,
            ApprovalStage.DESIGN,
            ApprovalStage.ENGINEERING,
            ApprovalStage.FINAL
        ]

        approval_flow_status = "审批中"
        if p.approval_status == ApprovalStatus.REJECTED:
            approval_flow_status = "已驳回"
        elif p.approval_status == ApprovalStatus.APPROVED:
            approval_flow_status = "已通过"
        else:
            for i, stage in enumerate(stages_order):
                stage_approval = next((a for a in approvals if a.stage == stage), None)
                if stage_approval:
                    if stage_approval.status == ApprovalStatus.PENDING:
                        stage_names = {
                            ApprovalStage.SAFETY: "安监审批",
                            ApprovalStage.DESIGN: "设计审批",
                            ApprovalStage.ENGINEERING: "工程审批",
                            ApprovalStage.FINAL: "终审"
                        }
                        approval_flow_status = f"待{stage_names.get(stage, stage.value)}"
                        break
                    elif stage_approval.status == ApprovalStatus.REJECTED:
                        approval_flow_status = "已驳回"
                        break

        return ProjectDetailResponse(
            id=p.id,
            project_no=p.project_no,
            name=p.name,
            area_id=p.area_id,
            applicant_id=p.applicant_id,
            project_type=p.project_type,
            scope=p.scope,
            budget=p.budget,
            planned_start_date=p.planned_start_date,
            planned_end_date=p.planned_end_date,
            current_stage=p.current_stage,
            approval_status=p.approval_status,
            construction_team_id=p.construction_team_id,
            actual_start_date=p.actual_start_date,
            actual_end_date=p.actual_end_date,
            status=p.status,
            created_at=p.created_at,
            approval_flow_status=approval_flow_status
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取项目详情失败: {str(e)}")


@project_router.post("/{project_id}/approval", response_model=None)
async def process_approval(
    project_id: int,
    stage: ApprovalStage,
    data: ApprovalRecordUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    required_roles = {
        ApprovalStage.SAFETY: [UserRole.SAFETY_INSPECTOR, UserRole.ADMIN],
        ApprovalStage.DESIGN: [UserRole.DESIGNER, UserRole.ADMIN],
        ApprovalStage.ENGINEERING: [UserRole.ENGINEER, UserRole.ADMIN],
        ApprovalStage.FINAL: [UserRole.ADMIN, UserRole.DISPATCHER]
    }
    allowed_roles = required_roles.get(stage, [UserRole.ADMIN])
    if current_user.role not in allowed_roles:
        raise HTTPException(status_code=403, detail=f"无权限处理 {stage.value} 阶段审批，需要角色: {[r.value for r in allowed_roles]}")

    try:
        record = await project_service.approve_stage(
            db, project_id, stage, current_user.id, data.status, data.comment
        )
    except ValueError as e:
        await db.rollback()
        error_msg = str(e)
        if "上一阶段" in error_msg:
            raise HTTPException(status_code=400, detail=error_msg)
        elif "已被处理" in error_msg:
            raise HTTPException(status_code=409, detail=error_msg)
        elif "不存在" in error_msg:
            raise HTTPException(status_code=404, detail=error_msg)
        else:
            raise HTTPException(status_code=400, detail=error_msg)
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"审批失败: {str(e)}")

    project_result = await db.execute(select(EngineeringProject).where(EngineeringProject.id == project_id))
    project = project_result.scalar_one_or_none()
    if not project:
        await db.rollback()
        raise HTTPException(status_code=404, detail="工程项目不存在")

    if data.status == ApprovalStatus.APPROVED:
        next_stage = project.current_stage
        if next_stage and next_stage != stage:
            notify_role = {
                ApprovalStage.SAFETY: UserRole.SAFETY_INSPECTOR,
                ApprovalStage.DESIGN: UserRole.DESIGNER,
                ApprovalStage.ENGINEERING: UserRole.ENGINEER,
                ApprovalStage.FINAL: UserRole.DISPATCHER
            }.get(next_stage)
            if notify_role:
                users_result = await db.execute(
                    select(User).where(and_(User.role == notify_role, User.is_active == True))
                )
                for u in users_result.scalars().all()[:5]:
                    await notification_service.create_notification(
                        db, u.id, NotificationType.APPROVAL,
                        f"下一阶段待审批: {project.name}",
                        f"当前阶段: {next_stage.value}",
                        project.id, "project"
                    )

    if project.applicant_id:
        await notification_service.create_notification(
            db, project.applicant_id, NotificationType.APPROVAL,
            f"审批结果: {project.name}",
            f"阶段 {stage.value}: {'通过' if data.status == ApprovalStatus.APPROVED else '驳回'}",
            project.id, "project"
        )

    await db.commit()
    await db.refresh(project)

    if stage == ApprovalStage.FINAL and data.status == ApprovalStatus.APPROVED:
        return FinalApprovalResponse(
            success=True,
            message=f"审批已{data.status.value}",
            construction_team_id=project.construction_team_id,
            actual_start_date=project.actual_start_date,
            status=project.status
        )

    return SuccessResponse(message=f"审批已{data.status.value}")


@project_router.get("/{project_id}/approvals", response_model=List[ApprovalRecordInfo])
async def get_project_approvals(project_id: int, db: AsyncSession = Depends(get_db)):
    try:
        project_result = await db.execute(
            select(EngineeringProject).where(EngineeringProject.id == project_id)
        )
        project = project_result.scalar_one_or_none()
        if not project:
            raise HTTPException(status_code=404, detail="工程项目不存在")

        stages_order = [
            ApprovalStage.SAFETY,
            ApprovalStage.DESIGN,
            ApprovalStage.ENGINEERING,
            ApprovalStage.FINAL
        ]

        result = await db.execute(
            select(ApprovalRecord).where(ApprovalRecord.project_id == project_id).order_by(ApprovalRecord.id)
        )
        records = result.scalars().all()

        record_map = {r.stage: r for r in records}
        ordered_records = []
        for stage in stages_order:
            if stage in record_map:
                ordered_records.append(record_map[stage])
            else:
                dummy_record = ApprovalRecord(
                    project_id=project_id,
                    stage=stage,
                    approver_id=0,
                    status=ApprovalStatus.PENDING,
                    submitted_at=datetime.utcnow()
                )
                ordered_records.append(dummy_record)

        return [ApprovalRecordInfo.model_validate(r) for r in ordered_records]
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"获取审批记录失败: {str(e)}")


purchase_router = APIRouter(prefix="/api/v1/purchase", tags=["气源采购"])


@purchase_router.post("/suppliers", response_model=IdResponse, dependencies=[Depends(require_roles(UserRole.ADMIN))])
async def create_supplier(data: GasSupplierCreate, db: AsyncSession = Depends(get_db)):
    existing = await db.execute(select(GasSupplier).where(GasSupplier.code == data.code))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="供应商编码已存在")
    s = GasSupplier(**data.model_dump())
    db.add(s)
    await db.commit()
    await db.refresh(s)
    return IdResponse(id=s.id)


@purchase_router.get("/suppliers", response_model=List[GasSupplierInfo])
async def list_suppliers(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(GasSupplier).order_by(GasSupplier.name))
    return [GasSupplierInfo.model_validate(s) for s in result.scalars().all()]


@purchase_router.post("/inventory", response_model=IdResponse, dependencies=[Depends(require_roles(UserRole.ADMIN, UserRole.DISPATCHER))])
async def create_inventory(data: GasInventoryCreate, db: AsyncSession = Depends(get_db)):
    inv = GasInventory(**data.model_dump())
    db.add(inv)
    await db.commit()
    await db.refresh(inv)
    return IdResponse(id=inv.id)


@purchase_router.get("/inventory", response_model=List[GasInventoryInfo])
async def list_inventory(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(GasInventory))
    return [GasInventoryInfo.model_validate(i) for i in result.scalars().all()]


@purchase_router.post("/plans", response_model=IdResponse)
async def create_purchase_plan(
    data: GasPurchasePlanCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles(UserRole.ADMIN, UserRole.DISPATCHER))
):
    plan = GasPurchasePlan(
        plan_no=generate_order_no("PP"),
        **data.model_dump()
    )
    db.add(plan)
    await db.commit()
    await db.refresh(plan)
    return IdResponse(id=plan.id)


@purchase_router.get("/plans", response_model=GasPurchasePlanListResponse)
async def list_purchase_plans(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    plan_month: Optional[str] = None,
    status: Optional[PurchaseStatus] = None,
    supplier_id: Optional[int] = None,
    db: AsyncSession = Depends(get_db)
):
    query = select(GasPurchasePlan)
    if plan_month:
        query = query.where(GasPurchasePlan.plan_month == plan_month)
    if status:
        query = query.where(GasPurchasePlan.status == status)
    if supplier_id:
        query = query.where(GasPurchasePlan.supplier_id == supplier_id)

    total = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar_one()
    query = query.order_by(GasPurchasePlan.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    items = (await db.execute(query)).scalars().all()

    return GasPurchasePlanListResponse(
        total=total, page=page, page_size=page_size,
        total_pages=(total + page_size - 1) // page_size,
        items=[GasPurchasePlanInfo.model_validate(p) for p in items]
    )


@purchase_router.post("/plans/{plan_id}/approve", response_model=SuccessResponse, dependencies=[Depends(require_roles(UserRole.ADMIN))])
async def approve_purchase_plan(
    plan_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    result = await db.execute(select(GasPurchasePlan).where(GasPurchasePlan.id == plan_id))
    plan = result.scalar_one_or_none()
    if not plan:
        raise HTTPException(status_code=404, detail="采购计划不存在")

    plan.approver_id = current_user.id
    plan.approved_at = datetime.utcnow()
    plan.status = PurchaseStatus.APPROVED

    plan.status = PurchaseStatus.ORDERED
    plan.ordered_at = datetime.utcnow()

    dispatchers_result = await db.execute(
        select(User).where(and_(User.role == UserRole.DISPATCHER, User.is_active == True)).limit(5)
    )
    dispatchers = dispatchers_result.scalars().all()

    approval_title = f"采购计划已审批通过"
    approval_content = f"计划编号: {plan.plan_no}，采购量: {plan.planned_volume}，金额: {plan.total_amount or '待确认'}"
    for d in dispatchers:
        await notification_service.create_notification(
            db, d.id, NotificationType.APPROVAL,
            approval_title, approval_content,
            plan.id, "gas_purchase_plan"
        )

    if plan.supplier_id:
        supplier_result = await db.execute(select(GasSupplier).where(GasSupplier.id == plan.supplier_id))
        supplier = supplier_result.scalar_one_or_none()
        if supplier:
            supplier_info = f"{supplier.code} - {supplier.name}"
            system_title = "采购计划已通知供应商"
            system_content = f"计划编号: {plan.plan_no}，供应商: {supplier_info}，采购量: {plan.planned_volume}"
            for d in dispatchers:
                await notification_service.create_notification(
                    db, d.id, NotificationType.SYSTEM,
                    system_title, system_content,
                    plan.id, "gas_purchase_plan"
                )

    await db.commit()
    return SuccessResponse(message="采购计划已审批通过并下单")


@purchase_router.post("/plans/{plan_id}/status", response_model=SuccessResponse)
async def update_purchase_status(
    plan_id: int,
    status: PurchaseStatus,
    delivered_volume: Optional[Decimal] = None,
    db: AsyncSession = Depends(get_db)
):
    result = await db.execute(select(GasPurchasePlan).where(GasPurchasePlan.id == plan_id))
    plan = result.scalar_one_or_none()
    if not plan:
        raise HTTPException(status_code=404, detail="采购计划不存在")

    plan.status = status
    if status == PurchaseStatus.SHIPPED:
        plan.shipped_at = datetime.utcnow()
    elif status == PurchaseStatus.DELIVERED:
        plan.delivered_at = datetime.utcnow()
        if delivered_volume:
            plan.delivered_volume = delivered_volume
            inv_result = await db.execute(select(GasInventory).limit(1))
            inv = inv_result.scalar_one_or_none()
            if inv:
                inv.current_volume += delivered_volume
                inv.last_updated = datetime.utcnow()
    elif status == PurchaseStatus.COMPLETED:
        plan.completed_at = datetime.utcnow()
        plan.status = PurchaseStatus.COMPLETED

    dispatchers_result = await db.execute(
        select(User).where(and_(User.role == UserRole.DISPATCHER, User.is_active == True)).limit(5)
    )
    dispatchers = dispatchers_result.scalars().all()

    notify_title = f"采购计划状态更新"
    notify_content = f"计划编号: {plan.plan_no}，当前状态: {status.value}"

    if plan.approver_id:
        await notification_service.create_notification(
            db, plan.approver_id, NotificationType.APPROVAL,
            notify_title, notify_content,
            plan.id, "gas_purchase_plan"
        )

    for d in dispatchers:
        await notification_service.create_notification(
            db, d.id, NotificationType.APPROVAL,
            notify_title, notify_content,
            plan.id, "gas_purchase_plan"
        )

    await db.commit()
    return SuccessResponse(message="状态已更新")


report_router = APIRouter(prefix="/api/v1/reports", tags=["运行报表"])


@report_router.post("/generate-daily", response_model=IdResponse, dependencies=[Depends(require_roles(UserRole.ADMIN, UserRole.DISPATCHER))])
async def generate_daily_report(
    report_date: date,
    area_id: Optional[int] = None,
    db: AsyncSession = Depends(get_db)
):
    report = await sensor_service.generate_daily_report(db, report_date, area_id)
    await db.commit()
    await db.refresh(report)
    return IdResponse(id=report.id)


@report_router.get("/daily", response_model=DailyReportListResponse)
async def list_daily_reports(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    area_id: Optional[int] = None,
    db: AsyncSession = Depends(get_db)
):
    query = select(DailyReport)
    if start_date:
        query = query.where(DailyReport.report_date >= start_date)
    if end_date:
        query = query.where(DailyReport.report_date <= end_date)
    if area_id:
        query = query.where(DailyReport.area_id == area_id)

    total = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar_one()
    query = query.order_by(DailyReport.report_date.desc()).offset((page - 1) * page_size).limit(page_size)
    items = (await db.execute(query)).scalars().all()

    return DailyReportListResponse(
        total=total, page=page, page_size=page_size,
        total_pages=(total + page_size - 1) // page_size,
        items=[DailyReportInfo.model_validate(r) for r in items]
    )


@report_router.get("/export-excel")
async def export_excel(
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
    area_id: Optional[int] = None,
    db: AsyncSession = Depends(get_db)
):
    query = select(DailyReport)
    if start_date:
        query = query.where(DailyReport.report_date >= start_date)
    if end_date:
        query = query.where(DailyReport.report_date <= end_date)
    if area_id:
        query = query.where(DailyReport.area_id == area_id)
    query = query.order_by(DailyReport.report_date.asc(), DailyReport.area_id.asc().nullslast())
    items = (await db.execute(query)).scalars().all()

    area_ids = list(set(r.area_id for r in items if r.area_id is not None))
    area_name_map: dict = {}
    if area_ids:
        area_result = await db.execute(select(Area).where(Area.id.in_(area_ids)))
        areas = area_result.scalars().all()
        area_name_map = {a.id: a.name for a in areas}

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "运行报表"
    headers = [
        "报表日期", "区域", "总输气量(m³)", "高峰小时输气量(m³)",
        "泄漏事件(起)", "已解决泄漏(起)", "泄漏发现率(%)",
        "工单总数", "已完成工单", "平均响应(分)", "平均解决(分)",
        "投诉数(起)", "投诉率(‰)", "新增开户(户)", "欠费账单数", "营收(元)"
    ]
    ws.append(headers)

    from openpyxl.styles import Font
    header_font = Font(bold=True)
    for col_idx in range(1, len(headers) + 1):
        ws.cell(row=1, column=col_idx).font = header_font

    for r in items:
        area_name = area_name_map.get(r.area_id, "全局") if r.area_id else "全局"
        ws.append([
            str(r.report_date),
            area_name,
            float(r.total_gas_volume),
            float(r.peak_hour_volume),
            r.leak_count,
            r.leak_resolved,
            float(r.leak_detection_rate),
            r.work_order_count,
            r.work_order_completed,
            float(r.avg_response_minutes),
            float(r.avg_resolution_minutes),
            r.complaint_count,
            float(r.complaint_rate),
            r.new_connection_count,
            r.overdue_bill_count,
            float(r.revenue)
        ])

    for col in range(1, len(headers) + 1):
        col_letter = chr(64 + col) if col <= 26 else chr(64 + (col - 1) // 26) + chr(65 + (col - 1) % 26)
        ws.column_dimensions[col_letter].width = 16

    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)

    date_range = f"{start_date}_to_{end_date}" if start_date and end_date else (f"from_{start_date}" if start_date else (f"to_{end_date}" if end_date else "all"))
    filename = f"gas_daily_report_{date_range}.xlsx"
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


notif_router = APIRouter(prefix="/api/v1/notifications", tags=["通知"])


@notif_router.get("", response_model=None)
async def list_notifications(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=500),
    unread_only: bool = False,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    total, items = await notification_service.get_user_notifications(
        db, current_user.id, page, page_size, unread_only
    )
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": (total + page_size - 1) // page_size,
        "items": items
    }


@notif_router.post("/{notif_id}/read", response_model=SuccessResponse)
async def mark_read(
    notif_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    ok = await notification_service.mark_notification_read(db, notif_id, current_user.id)
    if not ok:
        raise HTTPException(status_code=404, detail="通知不存在")
    await db.commit()
    return SuccessResponse(message="已标记为已读")


@notif_router.post("/read-all", response_model=SuccessResponse)
async def mark_all_read(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    count = await notification_service.mark_all_read(db, current_user.id)
    await db.commit()
    return SuccessResponse(message=f"已标记 {count} 条通知为已读")


@notif_router.get("/unread-count", response_model=None)
async def unread_count(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    count = await notification_service.get_unread_count(db, current_user.id)
    return {"count": count}


predict_router = APIRouter(prefix="/api/v1/prediction", tags=["负荷预测"])


@predict_router.post("/daily")
async def predict_daily_demand(
    for_date: date,
    area_id: Optional[int] = None,
    db: AsyncSession = Depends(get_db)
):
    result = await sensor_service.predict_demand(db, area_id, for_date)
    await db.commit()
    return result
