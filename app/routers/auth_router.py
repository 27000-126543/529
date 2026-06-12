from typing import Optional, List
from datetime import datetime, timedelta
from fastapi import APIRouter, Depends, HTTPException, status, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import and_, func, or_
from app.database import get_db
from app.models import User, UserRole, Area, MaintenanceTeam
from app.schemas.user import (
    LoginRequest, LoginResponse, UserCreate, UserUpdate, UserInfo, UserListResponse,
    AreaCreate, AreaUpdate, AreaInfo, AreaListResponse,
    MaintenanceTeamCreate, MaintenanceTeamUpdate, MaintenanceTeamInfo, MaintenanceTeamListResponse
)
from app.schemas.common import IdResponse, SuccessResponse
from app.utils.security import (
    hash_password, verify_password, create_access_token,
    get_current_user, require_roles
)
from app.config import settings

router = APIRouter(prefix="/api/v1/auth", tags=["认证与用户管理"])


@router.post("/login", response_model=LoginResponse)
async def login(
    request: LoginRequest,
    db: AsyncSession = Depends(get_db)
):
    result = await db.execute(
        select(User).where(User.username == request.username, User.is_active == True)
    )
    user = result.scalar_one_or_none()
    if not user or not verify_password(request.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误"
        )

    user.last_login = datetime.utcnow()
    await db.commit()

    access_token = create_access_token(
        data={"user_id": user.id, "role": user.role.value, "username": user.username},
        expires_delta=timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    )
    return LoginResponse(
        access_token=access_token,
        token_type="bearer",
        user=UserInfo.model_validate(user)
    )


@router.get("/me", response_model=UserInfo)
async def get_me(current_user: User = Depends(get_current_user)):
    return UserInfo.model_validate(current_user)


@router.post("/users", response_model=IdResponse, dependencies=[Depends(require_roles(UserRole.ADMIN))])
async def create_user(
    user_data: UserCreate,
    db: AsyncSession = Depends(get_db)
):
    existing = await db.execute(select(User).where(User.username == user_data.username))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="用户名已存在")

    user = User(
        username=user_data.username,
        password_hash=hash_password(user_data.password),
        real_name=user_data.real_name,
        phone=user_data.phone,
        email=user_data.email,
        role=user_data.role,
        area_id=user_data.area_id,
        team_id=user_data.team_id,
        is_active=user_data.is_active
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return IdResponse(id=user.id)


@router.get("/users", response_model=UserListResponse, dependencies=[Depends(require_roles(UserRole.ADMIN, UserRole.DISPATCHER))])
async def list_users(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    role: Optional[UserRole] = None,
    area_id: Optional[int] = None,
    keyword: Optional[str] = None,
    db: AsyncSession = Depends(get_db)
):
    query = select(User)
    conditions = []
    if role:
        conditions.append(User.role == role)
    if area_id:
        conditions.append(User.area_id == area_id)
    if keyword:
        conditions.append(or_(
            User.username.ilike(f"%{keyword}%"),
            User.real_name.ilike(f"%{keyword}%"),
            User.phone.ilike(f"%{keyword}%")
        ))
    if conditions:
        query = query.where(and_(*conditions))

    total = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar_one()
    query = query.order_by(User.id.desc()).offset((page - 1) * page_size).limit(page_size)
    items = (await db.execute(query)).scalars().all()

    return UserListResponse(
        total=total,
        page=page,
        page_size=page_size,
        total_pages=(total + page_size - 1) // page_size,
        items=[UserInfo.model_validate(u) for u in items]
    )


@router.get("/users/{user_id}", response_model=UserInfo)
async def get_user(
    user_id: int,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    if current_user.id != user_id and current_user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="无权限查看其他用户")
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    return UserInfo.model_validate(user)


@router.put("/users/{user_id}", response_model=SuccessResponse)
async def update_user(
    user_id: int,
    user_data: UserUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    if current_user.id != user_id and current_user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="无权限修改其他用户")

    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    update_data = user_data.model_dump(exclude_unset=True)
    if "password" in update_data:
        update_data["password_hash"] = hash_password(update_data.pop("password"))

    for key, value in update_data.items():
        if value is not None:
            setattr(user, key, value)

    await db.commit()
    return SuccessResponse(message="用户更新成功")


@router.post("/areas", response_model=IdResponse, dependencies=[Depends(require_roles(UserRole.ADMIN))])
async def create_area(area_data: AreaCreate, db: AsyncSession = Depends(get_db)):
    existing = await db.execute(select(Area).where(Area.code == area_data.code))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="区域编码已存在")

    area = Area(**area_data.model_dump())
    db.add(area)
    await db.commit()
    await db.refresh(area)
    return IdResponse(id=area.id)


@router.get("/areas", response_model=AreaListResponse)
async def list_areas(
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=500),
    parent_id: Optional[int] = None,
    level: Optional[int] = None,
    db: AsyncSession = Depends(get_db)
):
    query = select(Area)
    if parent_id is not None:
        query = query.where(Area.parent_id == parent_id)
    if level:
        query = query.where(Area.level == level)

    total = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar_one()
    query = query.order_by(Area.code).offset((page - 1) * page_size).limit(page_size)
    items = (await db.execute(query)).scalars().all()

    return AreaListResponse(
        total=total,
        page=page,
        page_size=page_size,
        total_pages=(total + page_size - 1) // page_size,
        items=[AreaInfo.model_validate(a) for a in items]
    )


@router.get("/areas/{area_id}", response_model=AreaInfo)
async def get_area(area_id: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Area).where(Area.id == area_id))
    area = result.scalar_one_or_none()
    if not area:
        raise HTTPException(status_code=404, detail="区域不存在")
    return AreaInfo.model_validate(area)


@router.put("/areas/{area_id}", response_model=SuccessResponse, dependencies=[Depends(require_roles(UserRole.ADMIN))])
async def update_area(
    area_id: int,
    area_data: AreaUpdate,
    db: AsyncSession = Depends(get_db)
):
    result = await db.execute(select(Area).where(Area.id == area_id))
    area = result.scalar_one_or_none()
    if not area:
        raise HTTPException(status_code=404, detail="区域不存在")

    for key, value in area_data.model_dump(exclude_unset=True).items():
        if value is not None:
            setattr(area, key, value)
    await db.commit()
    return SuccessResponse(message="区域更新成功")


@router.post("/teams", response_model=IdResponse, dependencies=[Depends(require_roles(UserRole.ADMIN))])
async def create_team(team_data: MaintenanceTeamCreate, db: AsyncSession = Depends(get_db)):
    team = MaintenanceTeam(**team_data.model_dump())
    db.add(team)
    await db.commit()
    await db.refresh(team)
    return IdResponse(id=team.id)


@router.get("/teams", response_model=MaintenanceTeamListResponse)
async def list_teams(
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    area_id: Optional[int] = None,
    status: Optional[str] = None,
    db: AsyncSession = Depends(get_db)
):
    query = select(MaintenanceTeam)
    if area_id:
        query = query.where(MaintenanceTeam.area_id == area_id)
    if status:
        query = query.where(MaintenanceTeam.status == status)

    total = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar_one()
    query = query.order_by(MaintenanceTeam.id.desc()).offset((page - 1) * page_size).limit(page_size)
    items = (await db.execute(query)).scalars().all()

    return MaintenanceTeamListResponse(
        total=total,
        page=page,
        page_size=page_size,
        total_pages=(total + page_size - 1) // page_size,
        items=[MaintenanceTeamInfo.model_validate(t) for t in items]
    )


@router.put("/teams/{team_id}", response_model=SuccessResponse, dependencies=[Depends(require_roles(UserRole.ADMIN))])
async def update_team(
    team_id: int,
    team_data: MaintenanceTeamUpdate,
    db: AsyncSession = Depends(get_db)
):
    result = await db.execute(select(MaintenanceTeam).where(MaintenanceTeam.id == team_id))
    team = result.scalar_one_or_none()
    if not team:
        raise HTTPException(status_code=404, detail="维修队不存在")

    for key, value in team_data.model_dump(exclude_unset=True).items():
        if value is not None:
            setattr(team, key, value)
    await db.commit()
    return SuccessResponse(message="维修队更新成功")
