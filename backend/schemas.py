from datetime import date, datetime
from typing import Optional, List

from pydantic import BaseModel, EmailStr, Field


class UserCreate(BaseModel):
    username: str
    email: EmailStr
    password: str


class UserResponse(BaseModel):
    id: int
    username: str
    email: EmailStr
    created_at: datetime

    class Config:
        from_attributes = True


class Token(BaseModel):
    access_token: str
    token_type: str
    user_id: int
    username: str


class RouteCreate(BaseModel):
    name: str
    is_public: bool = True


class RouteResponse(BaseModel):
    id: int
    name: str
    user_id: int
    distance_km: Optional[float]
    elevation_gain_m: Optional[float]
    elevation_loss_m: Optional[float]
    min_ele_m: Optional[float]
    max_ele_m: Optional[float]
    start: Optional[str]
    end: Optional[str]
    is_public: bool
    created_at: datetime

    class Config:
        from_attributes = True


class WaypointResponse(BaseModel):
    id: int
    name: str
    lat: float
    lon: float
    ele: float
    type: str
    order_index: int

    class Config:
        from_attributes = True


class WaypointCreate(BaseModel):
    name: Optional[str] = None
    lat: float = Field(..., ge=-90, le=90)
    lon: float = Field(..., ge=-180, le=180)
    ele: float = 0.0


class TripPlanCreate(BaseModel):
    route_id: int
    departure_date: date
    days: int = Field(..., ge=1, le=7, description='徒步天数 1-7 天, 必填')


class TripPlanResponse(BaseModel):
    id: int
    user_id: int
    route_id: int
    departure_date: date
    days: int
    status: str
    created_at: datetime

    class Config:
        from_attributes = True


class UGCReportCreate(BaseModel):
    waypoint_id: int
    date: date
    temp_real: Optional[float]
    precip_real: Optional[float]
    wind_real: Optional[float]
    comment: Optional[str]


class UGCReportResponse(BaseModel):
    id: int
    user_id: int
    waypoint_id: int
    date: date
    temp_real: Optional[float]
    precip_real: Optional[float]
    wind_real: Optional[float]
    comment: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True
