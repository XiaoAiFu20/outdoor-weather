from datetime import datetime

from sqlalchemy import (
    Column, Integer, String, Float, Boolean, Date, DateTime,
    ForeignKey, JSON, Text, Index
)
from sqlalchemy.orm import relationship

from database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True, nullable=False)
    email = Column(String, unique=True, index=True, nullable=False)
    password_hash = Column(String, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    routes = relationship("Route", back_populates="owner")
    trip_plans = relationship("TripPlan", back_populates="user")
    ugc_reports = relationship("UGCReport", back_populates="user")


class Route(Base):
    __tablename__ = "routes"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, nullable=False)
    user_id = Column(Integer, ForeignKey("users.id"))
    gpx_data = Column(JSON)
    track_geojson = Column(JSON)
    distance_km = Column(Float)
    elevation_gain_m = Column(Float)
    elevation_loss_m = Column(Float)
    min_ele_m = Column(Float)
    max_ele_m = Column(Float)
    start = Column(String)
    end = Column(String)
    is_public = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    owner = relationship("User", back_populates="routes")
    waypoints = relationship("Waypoint", back_populates="route", cascade="all, delete-orphan")
    trip_plans = relationship("TripPlan", back_populates="route")


class Waypoint(Base):
    __tablename__ = "waypoints"

    id = Column(Integer, primary_key=True, index=True)
    route_id = Column(Integer, ForeignKey("routes.id"))
    name = Column(String, nullable=False)
    lat = Column(Float, nullable=False)
    lon = Column(Float, nullable=False)
    ele = Column(Float, default=0)
    type = Column(String, default="note")
    order_index = Column(Integer, default=0)

    route = relationship("Route", back_populates="waypoints")
    forecasts = relationship("Forecast", back_populates="waypoint")
    ugc_reports = relationship("UGCReport", back_populates="waypoint")


class Forecast(Base):
    __tablename__ = "forecasts"
    __table_args__ = (
        Index('ix_forecasts_wp_date', 'waypoint_id', 'date'),
    )

    id = Column(Integer, primary_key=True, index=True)
    waypoint_id = Column(Integer, ForeignKey("waypoints.id"))
    date = Column(Date, nullable=False)
    data_json = Column(JSON, nullable=False)
    fetched_at = Column(DateTime, default=datetime.utcnow)

    waypoint = relationship("Waypoint", back_populates="forecasts")


class TripPlan(Base):
    __tablename__ = "trip_plans"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    route_id = Column(Integer, ForeignKey("routes.id"))
    departure_date = Column(Date, nullable=False)
    days = Column(Integer, nullable=False, default=1)
    status = Column(String, default="planned")
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="trip_plans")
    route = relationship("Route", back_populates="trip_plans")


class UGCReport(Base):
    __tablename__ = "ugc_reports"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"))
    waypoint_id = Column(Integer, ForeignKey("waypoints.id"))
    date = Column(Date, nullable=False)
    temp_real = Column(Float)
    precip_real = Column(Float)
    wind_real = Column(Float)
    comment = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)

    user = relationship("User", back_populates="ugc_reports")
    waypoint = relationship("Waypoint", back_populates="ugc_reports")
