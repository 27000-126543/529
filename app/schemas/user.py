from typing import Optional, List
from datetime import datetime
from decimal import Decimal
from pydantic import Field, EmailStr
from app.schemas.common import BaseSchema, PaginationResponse
from app.models import UserRole


class LoginRequest(BaseSchema):
    username: str
    password: str


class LoginResponse(BaseSchema):
    access_token: str
    token_type: str = "bearer"
    user: "UserInfo"


class UserBase(BaseSchema):
    username: str = Field(..., min_length=3, max_length=50)
    real_name: Optional[str] = Field(None, max_length=50)
    phone: Optional[str] = Field(None, max_length=20)
    email: Optional[EmailStr] = None
    role: UserRole
    area_id: Optional[int] = None
    team_id: Optional[int] = None
    is_active: bool = True


class UserCreate(UserBase):
    password: str = Field(..., min_length=6, max_length=100)


class UserUpdate(BaseSchema):
    real_name: Optional[str] = None
    phone: Optional[str] = None
    email: Optional[EmailStr] = None
    role: Optional[UserRole] = None
    area_id: Optional[int] = None
    team_id: Optional[int] = None
    is_active: Optional[bool] = None
    password: Optional[str] = Field(None, min_length=6)


class UserInfo(UserBase):
    id: int
    last_login: Optional[datetime] = None
    created_at: Optional[datetime] = None


class UserListResponse(PaginationResponse):
    items: List[UserInfo]


class AreaBase(BaseSchema):
    name: str
    code: str
    parent_id: Optional[int] = None
    level: int = 1
    manager_id: Optional[int] = None
    longitude: Optional[Decimal] = None
    latitude: Optional[Decimal] = None
    description: Optional[str] = None


class AreaCreate(AreaBase):
    pass


class AreaUpdate(BaseSchema):
    name: Optional[str] = None
    code: Optional[str] = None
    parent_id: Optional[int] = None
    level: Optional[int] = None
    manager_id: Optional[int] = None
    longitude: Optional[Decimal] = None
    latitude: Optional[Decimal] = None
    description: Optional[str] = None


class AreaInfo(AreaBase):
    id: int
    created_at: Optional[datetime] = None


class AreaListResponse(PaginationResponse):
    items: List[AreaInfo]


class MaintenanceTeamBase(BaseSchema):
    name: str
    area_id: int
    leader_id: Optional[int] = None
    max_capacity: int = 10
    longitude: Optional[Decimal] = None
    latitude: Optional[Decimal] = None
    contact_phone: Optional[str] = None


class MaintenanceTeamCreate(MaintenanceTeamBase):
    pass


class MaintenanceTeamUpdate(BaseSchema):
    name: Optional[str] = None
    area_id: Optional[int] = None
    leader_id: Optional[int] = None
    max_capacity: Optional[int] = None
    status: Optional[str] = None
    longitude: Optional[Decimal] = None
    latitude: Optional[Decimal] = None
    contact_phone: Optional[str] = None


class MaintenanceTeamInfo(MaintenanceTeamBase):
    id: int
    current_load: int = 0
    status: str = "active"
    created_at: Optional[datetime] = None


class MaintenanceTeamListResponse(PaginationResponse):
    items: List[MaintenanceTeamInfo]


LoginResponse.model_rebuild()
