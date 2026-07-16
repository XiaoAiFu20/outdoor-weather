"""轨迹分段天气预估: 沿轨迹等距抽样, 按徒步速度估每段到达时刻, 查对应 hourly bucket.

track_geojson 格式: [[lon, lat, ele], ...]  (GeoJSON 约定, lon 在前)
"""
import asyncio
import time
from datetime import datetime, timedelta
from typing import Any

from gpx_parser import haversine_km
from forecast import get_forecast_for_point


def _haversine_from_tuple(a: list, b: list) -> float:
    """track 点 [lon, lat, ele] → haversine_km 需要的 dict."""
    return haversine_km({'lat': a[1], 'lon': a[0]}, {'lat': b[1], 'lon': b[0]})


def _interp(p_a: list, p_b: list, dist_a: float, seg_len: float) -> list:
    """在 p_a, p_b 之间按距离比例线性插值."""
    if seg_len <= 0:
        return p_a[:]
    t = dist_a / seg_len
    return [
        p_a[0] + (p_b[0] - p_a[0]) * t,
        p_a[1] + (p_b[1] - p_a[1]) * t,
        int(p_a[2] + (p_b[2] - p_a[2]) * t),
    ]


def segment_track(track: list, target_segments: int = 8) -> list[dict]:
    """按累计距离等距抽样分段.

    Returns:
        [{index, lat, lon, ele_m, cum_distance_km, distance_km}, ...]
        长度 = target_segments + 1 (含起点和终点)
    """
    if not track or len(track) < 2:
        return []
    cum = [0.0]
    for i in range(1, len(track)):
        cum.append(cum[-1] + _haversine_from_tuple(track[i - 1], track[i]))
    total = cum[-1]
    if total < 0.3:
        return [{
            'index': 0,
            'lat': round(track[0][1], 5),
            'lon': round(track[0][0], 5),
            'ele_m': int(track[0][2]),
            'cum_distance_km': 0.0,
            'distance_km': round(total, 2),
        }]
    target_segments = max(2, min(target_segments, 12))
    step = total / target_segments
    targets = [i * step for i in range(target_segments + 1)]
    segments = []
    j = 0
    for k, target in enumerate(targets):
        while j < len(cum) - 2 and cum[j + 1] < target:
            j += 1
        if cum[j] == target:
            seg_pt = track[j]
        else:
            seg_pt = _interp(track[j], track[j + 1], target - cum[j], cum[j + 1] - cum[j])
        segments.append({
            'index': k,
            'lat': round(seg_pt[1], 5),
            'lon': round(seg_pt[0], 5),
            'ele_m': int(seg_pt[2]),
            'cum_distance_km': round(target, 2),
            'distance_km': round(step, 2) if k > 0 else 0.0,
        })
    return segments


async def _fetch_segment_weather(seg: dict, dep_dt: datetime, speed_kmh: float, days: int) -> dict:
    """单段: 估到达时刻 + 拉该点 hourly + 匹配 bucket."""
    arrival = dep_dt + timedelta(hours=seg['cum_distance_km'] / speed_kmh)
    arrival_iso = arrival.strftime('%Y-%m-%dT%H:%M')
    base = {**seg, 'arrival_time': arrival_iso}
    try:
        fc = await get_forecast_for_point(seg['lat'], seg['lon'], seg['ele_m'], days=days)
    except Exception as e:
        base['error'] = f'预报拉取失败: {e}'
        return base
    if not isinstance(fc, dict) or 'error' in fc:
        base['error'] = fc.get('error', '未知错误') if isinstance(fc, dict) else '预报返回非 dict'
        return base
    hourly = fc.get('hourly') or []
    if not hourly:
        base['error'] = 'hourly 为空'
        return base
    arrival_hour = arrival_iso[:13]  # YYYY-MM-DDTHH
    match = next((h for h in hourly if h.get('time', '').startswith(arrival_hour)), None)
    if not match:
        base['error'] = f'无匹配 hourly (到达 {arrival_hour} 超 7 天预报范围?)'
        return base
    base.update({
        'temp_real': match.get('t_real'),
        'temp_wct': match.get('wct'),
        'precip_mm': match.get('precip_mm'),
        'weather_code': match.get('wmo_code'),
        'weather_label': match.get('wmo_label'),
        'wind_kmh': match.get('wind'),
        'gust_kmh': match.get('gust'),
        'rh': match.get('rh'),
        'has_thunder': bool(match.get('has_thunder')),
    })
    return base


async def compute_track_weather(route: dict, departure: str, speed_kmh: float = 3.0,
                                departure_hour: int = 8) -> dict:
    """主入口.

    Args:
        route: _route_forecast_cache[route_name], 含 'track' / 'distance_km' / 'max_ele_m'
        departure: 'YYYY-MM-DD' (本地)
        speed_kmh: 徒步速度, 1.5-6
        departure_hour: 出发小时 (0-23, 默认 8 = 早上 8 点)
    """
    track = route.get('track') or []
    total_km = float(route.get('distance_km') or 0)
    if not track:
        return {'error': '轨迹为空', 'segments': [], 'source': 'Open-Meteo + QWeather + Caiyun 融合'}
    if total_km <= 0:
        total_km = sum(_haversine_from_tuple(track[i - 1], track[i]) for i in range(1, len(track)))

    target_segs = max(4, min(10, int(total_km / 1.5)))
    segs = segment_track(track, target_segs)
    if not segs:
        return {'error': '分段失败 (轨迹太短?)', 'segments': [],
                'source': 'Open-Meteo + QWeather + Caiyun 融合'}

    try:
        dep_dt = datetime.strptime(departure, '%Y-%m-%d').replace(hour=departure_hour)
    except ValueError:
        dep_dt = datetime.now().replace(hour=departure_hour)

    total_hours = total_km / speed_kmh
    # days = (出发日 - 今天) + 行程天数, 上限 16 (Open-Meteo 免费版上限; 超 7 天为长波单源, 精度低)
    today = datetime.now().date()
    dep_date = dep_dt.date()
    days_offset = max(0, (dep_date - today).days)
    days = max(1, min(16, days_offset + int(total_hours // 24) + 1))

    results = await asyncio.gather(
        *[_fetch_segment_weather(s, dep_dt, speed_kmh, days) for s in segs],
        return_exceptions=False,
    )

    return {
        'segments': results,
        'total_distance_km': round(total_km, 2),
        'estimated_speed_kmh': speed_kmh,
        'estimated_total_hours': round(total_hours, 1),
        'departure': dep_dt.strftime('%Y-%m-%d'),
        'segment_count': len(results),
        'source': 'Open-Meteo + QWeather + Caiyun 融合',
        'fetched_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
        'note': '到达时刻按匀速估算, 实际可能 ±50% (受地形/休息/负重影响)',
    }
