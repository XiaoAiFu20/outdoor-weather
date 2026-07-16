"""FastAPI 主服务 - 提供路线数据 + 多源融合预报 + 装备建议 + 行程风险 + PDF 离线包"""
import asyncio
import logging
import sys
import time
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote
from typing import List

from fastapi import FastAPI, HTTPException, Query, Depends, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response
from fastapi.security import OAuth2PasswordRequestForm
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session

BACKEND_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(BACKEND_DIR))

import config
from logger import setup_logging, get_logger
from auth import (
    get_current_user, login_for_access_token, create_user, get_user, get_user_by_email
)
from database import get_db, init_db

logger = get_logger(__name__)
from gpx_parser import parse_gpx, KEY_TYPES
from forecast import get_forecast_for_point, get_forecast_for_points
from glow import score_glow_for_day
from forecast_store import upsert_forecast
import pdf_export
import minutely
import alerts as alerts_mod
from track_segment import compute_track_weather
from historical import get_historical_period
import admin
from models import User, Route, Waypoint, TripPlan, UGCReport
from schemas import (
    UserCreate, UserResponse, Token, RouteResponse, WaypointResponse, WaypointCreate,
    TripPlanCreate, TripPlanResponse, UGCReportCreate, UGCReportResponse
)

_route_forecast_cache: dict[str, dict] = {}
LAST_REFRESH = {'value': None, 'count': 0, 'in_progress': False}


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    logger.info("启动: 初始化数据库与路由缓存")
    init_db()
    await load_routes_from_db()
    task = asyncio.create_task(scheduler_loop())
    try:
        yield
    finally:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="徒步路线天气监测", version="0.3.0", lifespan=lifespan)
app.include_router(admin.router)

app.add_middleware(
    CORSMiddleware, allow_origins=['*'], allow_methods=['*'], allow_headers=['*'],
)


async def load_routes_from_db():
    """从数据库加载路线"""
    db = next(get_db())
    try:
        routes = db.query(Route).all()
        for route in routes:
            if route.gpx_data:
                track = route.track_geojson or []
                if track:
                    lats = [p[1] for p in track]
                    lons = [p[0] for p in track]
                    center = {
                        'lat': round((min(lats) + max(lats)) / 2, 5),
                        'lon': round((min(lons) + max(lons)) / 2, 5),
                    }
                else:
                    center = {'lat': 30.0, 'lon': 100.0}
                _route_forecast_cache[route.name] = {
                    'id': route.id,
                    'name': route.name,
                    'start': route.start,
                    'end': route.end,
                    'distance_km': route.distance_km,
                    'elevation_gain_m': route.elevation_gain_m,
                    'elevation_loss_m': route.elevation_loss_m,
                    'min_ele_m': route.min_ele_m,
                    'max_ele_m': route.max_ele_m,
                    'center': center,
                    'track': track,
                    'track_point_count_original': len(route.gpx_data.get('track', [])),
                    'track_point_count_simplified': len(track),
                    'waypoints': [
                        {'id': w.id, 'name': w.name, 'lat': w.lat, 'lon': w.lon, 'ele': w.ele, 'type': w.type}
                        for w in route.waypoints
                    ],
                    'key_pois': [
                        {'id': w.id, 'name': w.name, 'lat': w.lat, 'lon': w.lon, 'ele': w.ele, 'type': w.type}
                        for w in route.waypoints
                        if w.type in KEY_TYPES
                    ],
                    'total_waypoints': len(route.waypoints),
                }
                logger.info("从数据库加载路线: %s", route.name)
    finally:
        db.close()


async def refresh_all_routes(force: bool = True) -> dict:
    if LAST_REFRESH['in_progress']:
        return {'status': 'already_running'}
    LAST_REFRESH['in_progress'] = True
    start = time.time()
    total_points = 0
    persisted_rows = 0
    for route_name, route in _route_forecast_cache.items():
        pois = route['key_pois']
        results = await get_forecast_for_points(pois, days=16, force=force)
        success = sum(1 for r in results if isinstance(r, dict) and 'error' not in r)
        total_points += success
        # 落库: 失败不阻塞 refresh 主流程, 只记日志 + rollback
        db = next(get_db())
        try:
            for poi, fc in zip(pois, results):
                if not isinstance(fc, dict) or 'error' in fc or not fc.get('daily'):
                    continue
                if not poi.get('id'):
                    continue
                try:
                    persisted_rows += upsert_forecast(db, poi['id'], fc['daily'])
                except Exception:
                    db.rollback()
                    logger.exception("upsert_forecast 失败 wp=%s date_range=%s..%s",
                                     poi.get('id'),
                                     fc['daily'][0].get('date') if fc['daily'] else '?',
                                     fc['daily'][-1].get('date') if fc['daily'] else '?')
        finally:
            db.close()
    LAST_REFRESH['value'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    LAST_REFRESH['count'] += 1
    LAST_REFRESH['in_progress'] = False
    elapsed = time.time() - start
    logger.info("refresh 完成: %d 个 POI, 落库 %d 行, 用时 %.1fs",
                total_points, persisted_rows, elapsed)
    return {'status': 'ok', 'points': total_points, 'persisted': persisted_rows,
            'elapsed': round(elapsed, 1)}


async def scheduler_loop():
    await asyncio.sleep(30)
    while True:
        try:
            logger.info("scheduler 触发定时刷新 @ %s", datetime.now().strftime('%H:%M:%S'))
            await refresh_all_routes(force=True)
        except Exception:
            logger.exception("scheduler 刷新失败")
        await asyncio.sleep(config.SCHEDULER_INTERVAL)


# ============== 用户认证 API ==============
@app.post("/api/register", response_model=UserResponse)
async def register(user: UserCreate, db: Session = Depends(get_db)):
    if get_user(db, user.username):
        raise HTTPException(status_code=400, detail="用户名已存在")
    if get_user_by_email(db, user.email):
        raise HTTPException(status_code=400, detail="邮箱已被注册")
    return create_user(db, user.username, user.email, user.password)


@app.post("/api/token", response_model=Token)
async def login(db: Session = Depends(get_db), form_data: OAuth2PasswordRequestForm = Depends()):
    return await login_for_access_token(db, form_data)


@app.post("/api/login", response_model=Token)
async def login_json(user: dict, db: Session = Depends(get_db)):
    from fastapi.security import OAuth2PasswordRequestForm
    form_data = OAuth2PasswordRequestForm(
        username=user.get("username", ""),
        password=user.get("password", "")
    )
    return await login_for_access_token(db, form_data)


@app.get("/api/users/me", response_model=UserResponse)
async def read_users_me(current_user: User = Depends(get_current_user)):
    return current_user


# ============== GPX 上传 API ==============
@app.post("/api/routes/upload")
async def upload_gpx(
    file: UploadFile = File(...),
    is_public: bool = Form(True),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    if not file.filename.lower().endswith('.gpx'):
        raise HTTPException(status_code=400, detail="只支持 GPX 文件")
    
    content = await file.read()
    try:
        gpx_path = config.GPX_DIR / f"upload_{int(time.time())}.gpx"
        gpx_path.write_bytes(content)
        route_data = parse_gpx(str(gpx_path))
        gpx_path.unlink(missing_ok=True)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"GPX 解析失败: {e}")

    existing = db.query(Route).filter(
        Route.name == route_data['name'],
        Route.user_id == current_user.id,
    ).first()

    if existing:
        existing.gpx_data = route_data
        existing.track_geojson = route_data.get('track')
        existing.distance_km = route_data.get('distance_km')
        existing.elevation_gain_m = route_data.get('elevation_gain_m')
        existing.elevation_loss_m = route_data.get('elevation_loss_m')
        existing.min_ele_m = route_data.get('min_ele_m')
        existing.max_ele_m = route_data.get('max_ele_m')
        existing.start = route_data.get('start')
        existing.end = route_data.get('end')
        existing.is_public = is_public
        db.query(Waypoint).filter(Waypoint.route_id == existing.id).delete()
        db_route = existing
        action = 'updated'
    else:
        db_route = Route(
            name=route_data['name'],
            user_id=current_user.id,
            gpx_data=route_data,
            track_geojson=route_data.get('track'),
            distance_km=route_data.get('distance_km'),
            elevation_gain_m=route_data.get('elevation_gain_m'),
            elevation_loss_m=route_data.get('elevation_loss_m'),
            min_ele_m=route_data.get('min_ele_m'),
            max_ele_m=route_data.get('max_ele_m'),
            start=route_data.get('start'),
            end=route_data.get('end'),
            is_public=is_public,
        )
        db.add(db_route)
        action = 'created'

    db.commit()
    db.refresh(db_route)

    for i, wp in enumerate(route_data.get('waypoints', [])):
        db_wp = Waypoint(
            route_id=db_route.id,
            name=wp['name'],
            lat=wp['lat'],
            lon=wp['lon'],
            ele=wp['ele'],
            type=wp['type'],
            order_index=i,
        )
        db.add(db_wp)
    db.commit()

    # parse_gpx 返回值不含 id, 但前端 /api/route/<name> 直接读这份 cache,
    # 缺 id 会导致前端 routeNameToId 不被填充, 进而 "+添加标记点" 报 "路线 ID 未知"
    route_data['id'] = db_route.id
    _route_forecast_cache[route_data['name']] = route_data
    logger.info("上传路线 name=%s user=%s action=%s route_id=%d",
                route_data['name'], current_user.username, action, db_route.id)
    return {'status': 'ok', 'action': action, 'route_id': db_route.id, 'name': route_data['name']}


@app.get("/api/routes", response_model=List[RouteResponse])
async def list_routes(db: Session = Depends(get_db)):
    routes = db.query(Route).filter(Route.is_public).all()
    return routes


@app.get("/api/routes/mine", response_model=List[RouteResponse])
async def list_my_routes(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    routes = db.query(Route).filter(Route.user_id == current_user.id).all()
    return routes


@app.delete("/api/routes/{route_id}")
async def delete_route(
    route_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    route = db.query(Route).filter(
        Route.id == route_id, Route.user_id == current_user.id
    ).first()
    if not route:
        raise HTTPException(status_code=404, detail="路线不存在或无权删除")
    name = route.name
    db.delete(route)  # waypoints 配了 cascade="all, delete-orphan", 会一起删
    db.commit()
    _route_forecast_cache.pop(name, None)
    logger.info("删除路线 id=%d name=%s by user=%s", route_id, name, current_user.username)
    return {"status": "ok", "id": route_id, "name": name}


@app.get("/api/routes/{route_id}", response_model=RouteResponse)
async def get_route(route_id: int, db: Session = Depends(get_db)):
    route = db.query(Route).filter(Route.id == route_id).first()
    if not route:
        raise HTTPException(status_code=404, detail="路线不存在")
    return route


@app.get("/api/routes/{route_id}/waypoints", response_model=List[WaypointResponse])
async def get_route_waypoints(route_id: int, db: Session = Depends(get_db)):
    route = db.query(Route).filter(Route.id == route_id).first()
    if not route:
        raise HTTPException(status_code=404, detail="路线不存在")
    return route.waypoints


@app.post("/api/routes/{route_id}/waypoints", response_model=WaypointResponse)
async def add_custom_waypoint(
    route_id: int,
    wp: WaypointCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """用户手动添加自定义 POI (type='custom'), 同步更新内存缓存"""
    route = db.query(Route).filter(
        Route.id == route_id, Route.user_id == current_user.id
    ).first()
    if not route:
        raise HTTPException(status_code=404, detail="路线不存在或无权操作")

    name = (wp.name or '').strip() or f"自定义点 {len(route.waypoints) + 1}"
    existing_max = max([w.order_index for w in route.waypoints], default=-1)
    db_wp = Waypoint(
        route_id=route_id,
        name=name,
        lat=wp.lat,
        lon=wp.lon,
        ele=wp.ele,
        type='custom',
        order_index=existing_max + 1,
    )
    db.add(db_wp)
    db.commit()
    db.refresh(db_wp)

    cached = _route_forecast_cache.get(route.name)
    if cached:
        wp_dict = {
            'id': db_wp.id, 'name': db_wp.name, 'lat': db_wp.lat,
            'lon': db_wp.lon, 'ele': db_wp.ele, 'type': 'custom',
        }
        cached.setdefault('waypoints', []).append(wp_dict)
        cached.setdefault('key_pois', []).append(wp_dict)
        cached['total_waypoints'] = len(cached['waypoints'])

    logger.info("添加自定义 POI id=%d name=%s route=%s by user=%s",
                db_wp.id, db_wp.name, route.name, current_user.username)
    return db_wp


@app.delete("/api/waypoints/{waypoint_id}")
async def delete_waypoint(
    waypoint_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """删除自定义 POI (仅 type='custom'), 双校验: 类型 + 所有权"""
    wp = db.query(Waypoint).filter(Waypoint.id == waypoint_id).first()
    if not wp:
        raise HTTPException(status_code=404, detail="节点不存在")
    if wp.type != 'custom':
        raise HTTPException(status_code=403, detail="仅自定义点可删除")
    route = db.query(Route).filter(
        Route.id == wp.route_id, Route.user_id == current_user.id
    ).first()
    if not route:
        raise HTTPException(status_code=403, detail="无权删除该节点")

    route_name = route.name
    db.delete(wp)
    db.commit()

    cached = _route_forecast_cache.get(route_name)
    if cached:
        cached['waypoints'] = [w for w in cached.get('waypoints', []) if w['id'] != waypoint_id]
        cached['key_pois'] = [w for w in cached.get('key_pois', []) if w['id'] != waypoint_id]
        cached['total_waypoints'] = len(cached['waypoints'])

    logger.info("删除自定义 POI id=%d name=%s by user=%s",
                waypoint_id, wp.name, current_user.username)
    return {"status": "ok", "id": waypoint_id}


# ============== 行程计划 API ==============
@app.post("/api/trip_plans", response_model=TripPlanResponse)
async def create_trip_plan(
    plan: TripPlanCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    route = db.query(Route).filter(Route.id == plan.route_id).first()
    if not route:
        raise HTTPException(status_code=404, detail="路线不存在")
    
    db_plan = TripPlan(
        user_id=current_user.id,
        route_id=plan.route_id,
        departure_date=plan.departure_date,
        days=plan.days,
    )
    db.add(db_plan)
    db.commit()
    db.refresh(db_plan)
    return db_plan


@app.get("/api/trip_plans", response_model=List[TripPlanResponse])
async def list_trip_plans(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    plans = db.query(TripPlan).filter(TripPlan.user_id == current_user.id).all()
    return plans


@app.get("/api/trip_plans/{plan_id}", response_model=TripPlanResponse)
async def get_trip_plan(plan_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    plan = db.query(TripPlan).filter(TripPlan.id == plan_id, TripPlan.user_id == current_user.id).first()
    if not plan:
        raise HTTPException(status_code=404, detail="行程计划不存在")
    return plan


# ============== UGC 实测回传 API ==============
@app.post("/api/ugc_reports", response_model=UGCReportResponse)
async def create_ugc_report(
    report: UGCReportCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db)
):
    wp = db.query(Waypoint).filter(Waypoint.id == report.waypoint_id).first()
    if not wp:
        raise HTTPException(status_code=404, detail="节点不存在")
    
    db_report = UGCReport(
        user_id=current_user.id,
        waypoint_id=report.waypoint_id,
        date=report.date,
        temp_real=report.temp_real,
        precip_real=report.precip_real,
        wind_real=report.wind_real,
        comment=report.comment,
    )
    db.add(db_report)
    db.commit()
    db.refresh(db_report)
    return db_report


@app.get("/api/waypoints/{waypoint_id}/ugc_reports", response_model=List[UGCReportResponse])
async def get_waypoint_ugc_reports(waypoint_id: int, db: Session = Depends(get_db)):
    reports = db.query(UGCReport).filter(UGCReport.waypoint_id == waypoint_id).all()
    return reports


# ============== 历史同期数据 API ==============
@app.get("/api/historical")
async def get_historical_data(
    lat: float = Query(...),
    lon: float = Query(...),
    years: int = Query(5, ge=1, le=10),
):
    return await get_historical_period(lat, lon, years)


@app.get("/api/route/{route_name}/historical")
async def get_route_historical(route_name: str):
    if route_name not in _route_forecast_cache:
        raise HTTPException(status_code=404, detail=f'路线不存在: {route_name}')
    route = _route_forecast_cache[route_name]
    pois = route['key_pois']
    
    tasks = [get_historical_period(p['lat'], p['lon']) for p in pois]
    results = await asyncio.gather(*tasks)
    
    return {
        'route_name': route_name,
        'points': [{'poi': p, 'historical': r} for p, r in zip(pois, results)],
    }


# ============== 原有 API 端点（兼容）==============
async def _get_full_forecast(route_name: str) -> dict:
    if route_name not in _route_forecast_cache:
        raise HTTPException(status_code=404, detail=f'路线不存在: {route_name}')
    route = _route_forecast_cache[route_name]
    pois = route['key_pois']
    results = await get_forecast_for_points(pois, days=16)

    points = []
    for p, fc in zip(pois, results):
        if 'error' in fc:
            points.append({'poi': p, 'error': fc['error']})
            continue
        # 给每天算 glow (晚霞评分)
        hourly = fc.get('hourly', [])
        daily_with_glow = []
        for d in fc['daily']:
            d2 = dict(d)
            d2['glow'] = score_glow_for_day(d.get('sunset'), hourly)
            daily_with_glow.append(d2)
        points.append({
            'poi': p,
            'h_grid': fc['h_grid'],
            'h_real': fc['h_real'],
            'sources': fc['sources'],
            'daily': daily_with_glow,
            'hourly': hourly,
            'fetched_at': fc.get('fetched_at'),
            '_from_cache': fc.get('_from_cache', False),
        })

    return {
        'route_name': route_name,
        'total_points': len(points),
        'sources_available': {
            'open-meteo': True,
            'qweather': bool(config.QWEATHER_API_KEY),
            'caiyun': bool(config.CAIYUN_TOKEN),
        },
        'route': {
            'name': route.get('name'),
            'distance_km': route.get('distance_km'),
            'max_ele_m': route.get('max_ele_m'),
            'min_ele_m': route.get('min_ele_m'),
            'elevation_gain_m': route.get('elevation_gain_m'),
        },
        'points': points,
    }


@app.get('/api/route/{route_name}')
async def get_route_detail(route_name: str):
    if route_name not in _route_forecast_cache:
        raise HTTPException(status_code=404, detail=f'路线不存在: {route_name}')
    return _route_forecast_cache[route_name]


@app.get('/api/forecast')
async def get_forecast(route_name: str = Query(...)):
    return await _get_full_forecast(route_name)


@app.get('/api/minutely')
async def get_minutely(
    lat: float = Query(..., description='纬度'),
    lon: float = Query(..., description='经度'),
    hours: int = Query(2, ge=1, le=4, description='未来小时数 (1-4, 每 15 分钟一个桶)'),
):
    """Open-Meteo minutely_15 逐分钟降水. 返回 hours*4 个 15 分钟桶 + 摘要."""
    return await minutely.fetch_minutely(lat, lon, hours)


@app.get('/api/alerts')
async def get_alerts(
    lat: float = Query(..., description='纬度'),
    lon: float = Query(..., description='经度'),
):
    """QWeather 天气预警 (官方气象台蓝/黄/橙/红). 5 min 缓存. JWT 未配置时返回空数组."""
    return await alerts_mod.fetch_alerts(lat, lon)


@app.get('/api/track_weather')
async def get_track_weather(
    route_name: str = Query(..., description='路线名'),
    departure: str | None = Query(None, description='出发日 YYYY-MM-DD (默认今天)'),
    speed: float = Query(3.0, ge=1.5, le=6.0, description='徒步速度 km/h (1.5-6)'),
    departure_hour: int = Query(8, ge=0, le=23, description='出发小时 (0-23, 默认 8)'),
):
    """沿轨迹等距分段, 按速度估到达时刻, 查对应 hourly bucket."""
    if route_name not in _route_forecast_cache:
        raise HTTPException(status_code=404, detail=f'路线不存在: {route_name}')
    dep = departure or datetime.now().strftime('%Y-%m-%d')
    return await compute_track_weather(_route_forecast_cache[route_name], dep, speed, departure_hour)


@app.get('/api/refresh')
async def api_refresh():
    if LAST_REFRESH['in_progress']:
        return {'status': 'already_running', 'last_refresh': LAST_REFRESH['value']}
    asyncio.create_task(refresh_all_routes(force=True))
    return {'status': 'started', 'message': '后台刷新已启动'}


@app.get('/api/status')
async def api_status():
    return {
        'sources': {
            'open-meteo': True,
            'qweather': bool(config.QWEATHER_API_KEY),
            'caiyun': bool(config.CAIYUN_TOKEN),
        },
        'last_refresh': LAST_REFRESH['value'],
        'refresh_count': LAST_REFRESH['count'],
        'in_progress': LAST_REFRESH['in_progress'],
        'routes_count': len(_route_forecast_cache),
        'cache_ttl_seconds': config.CACHE_TTL,
        'scheduler_interval_seconds': config.SCHEDULER_INTERVAL,
    }


@app.get('/api/gear')
async def get_gear(
    route_name: str = Query(...),
    date: str | None = Query(None, description='出发日 YYYY-MM-DD (可选, 不传则看全程前 7 天高精度段)'),
    days: int | None = Query(None, ge=1, le=16, description='徒步天数 1-16 (可选; 超过 7 天部分精度低)'),
):
    forecast_data = await _get_full_forecast(route_name)
    trip_dates = None
    if date:
        try:
            dep = datetime.strptime(date, '%Y-%m-%d')
            n = days or 1
            trip_dates = [(dep + timedelta(days=i)).strftime('%Y-%m-%d') for i in range(n)]
        except ValueError:
            pass  # date 非法时 fallback 到全程, 不报错
    return pdf_export.recommend_gear(forecast_data, trip_dates=trip_dates)


@app.get('/api/trip_risk')
async def get_trip_risk(
    route_name: str = Query(...),
    date: str = Query(..., description='出发日 YYYY-MM-DD'),
    days: int = Query(..., ge=1, le=16, description='徒步天数 1-16 天, 由用户输入 (8-16 天精度低)'),
):
    forecast_data = await _get_full_forecast(route_name)
    return pdf_export.calc_trip_risk(forecast_data, date, days) or {'error': '日期格式错误'}


@app.get('/api/guide')
async def download_guide():
    guide_path = Path(__file__).resolve().parent / 'guide.md'
    if not guide_path.exists():
        raise HTTPException(status_code=404, detail='guide.md 不存在')
    content = guide_path.read_bytes()
    quoted = quote('操作指引.md')
    return Response(
        content=content,
        media_type='text/markdown; charset=utf-8',
        headers={
            'Content-Disposition': f"attachment; filename*=UTF-8''{quoted}",
        },
    )


@app.get('/api/export')
async def export_pdf(
    route_name: str = Query(...),
    date: str = Query(None, description='出发日 YYYY-MM-DD (可选)'),
    days: int = Query(4, ge=1, le=16, description='徒步天数 1-16 (默认 4, 与前端默认一致; 超 7 天部分精度低)'),
):
    if route_name not in _route_forecast_cache:
        raise HTTPException(status_code=404, detail=f'路线不存在: {route_name}')
    route = _route_forecast_cache[route_name]
    forecast_data = await _get_full_forecast(route_name)
    try:
        pdf_bytes = pdf_export.generate_route_pdf(route, forecast_data, date, days=days)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f'PDF 生成失败: {e}')

    fname = f"{route_name}_{date or 'current'}.pdf"
    quoted = quote(fname)
    return Response(
        content=pdf_bytes,
        media_type='application/pdf',
        headers={
            'Content-Disposition': f"attachment; filename*=UTF-8''{quoted}",
            'Content-Length': str(len(pdf_bytes)),
        },
    )


# ============== 静态文件 / 首页 ==============
if config.WEB_DIR.exists():
    app.mount('/static', StaticFiles(directory=str(config.WEB_DIR)), name='static')


@app.get('/')
async def index():
    idx = config.WEB_DIR / 'index.html'
    if not idx.exists():
        raise HTTPException(status_code=404, detail='web/index.html 不存在')
    return FileResponse(str(idx))


@app.get('/service-worker.js')
async def service_worker():
    # SW 必须从根路径返回, scope 才能覆盖 '/' (mount 在 /static 会让 scope=/static).
    sw = config.WEB_DIR / 'service-worker.js'
    if not sw.exists():
        raise HTTPException(status_code=404, detail='service-worker.js 不存在')
    return FileResponse(
        str(sw),
        media_type='application/javascript',
        headers={'Service-Worker-Allowed': '/', 'Cache-Control': 'no-cache'},
    )


@app.get('/login')
async def login_page():
    p = config.WEB_DIR / 'login.html'
    if not p.exists():
        raise HTTPException(status_code=404, detail='web/login.html 不存在')
    return FileResponse(str(p))


if __name__ == '__main__':
    import uvicorn
    setup_logging()
    logger.info("启动 http://localhost:8000")
    logger.info("GPX 目录: %s", config.GPX_DIR)
    logger.info("Web 目录: %s", config.WEB_DIR)
    logger.info("数据源: open-meteo=on, qweather=%s, caiyun=%s",
                'on' if config.QWEATHER_API_KEY else 'off',
                'on' if config.CAIYUN_TOKEN else 'off')
    uvicorn.run(app, host='0.0.0.0', port=8000, log_level='info')
