"""多源数据融合 + 海拔修正 + WCT + 降水相态 + 三色风险徽章

数据源:
- Open-Meteo (默认, 免费, 无 key)
- 和风天气 QWeather (env: QWEATHER_API_KEY)
- 彩云天气 Caiyun (env: CAIYUN_TOKEN)

融合策略:
- IQR(1.5) 异常剔除
- 加权平均 (open-meteo 0.5, qweather 0.3, caiyun 0.2)
- WMO code 取众数 / 雷暴优先 (max)
"""
import asyncio
import hashlib
import json
import logging
import time
from datetime import datetime, date as _date
from typing import Any

import httpx

import config

logger = logging.getLogger(__name__)


# ============== 缓存 ==============
def _cache_path(key: str):
    return config.CACHE_DIR / f"{key}.json"


def get_cache(key: str):
    p = _cache_path(key)
    if not p.exists():
        return None
    meta = p.with_suffix('.meta')
    if meta.exists():
        try:
            cached_at = float(meta.read_text())
            if time.time() - cached_at > config.CACHE_TTL:
                return None
        except ValueError:
            return None
    try:
        return json.loads(p.read_text(encoding='utf-8'))
    except Exception:
        return None


def set_cache(key: str, data: Any):
    p = _cache_path(key)
    meta = p.with_suffix('.meta')
    p.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    meta.write_text(str(time.time()))


# ============== 数据源 - 原始拉取 ==============
# 彩云个人版 QPS 较严, 全局信号量限并发, 防止 26 个 POI 并发拉触发 Rate limit.
_caiyun_sem = asyncio.Semaphore(2)
# 彩云网格缓存: 按 0.2° 聚类共享响应, 一条路线只需 1-3 个网格.
# 进程级缓存 (不写文件), 重启即清空.
_caiyun_grid_cache: dict[tuple[float, float], dict | None] = {}
# per-grid 锁, 防止 thundering herd (同网格多 POI 并发 cache-miss 同时拉).
_caiyun_grid_locks: dict[tuple[float, float], asyncio.Lock] = {}


_OM_DAILY_FIELDS = [
    'temperature_2m_max', 'temperature_2m_min',
    'precipitation_sum', 'precipitation_hours', 'precipitation_probability_max',
    'wind_speed_10m_max', 'wind_gusts_10m_max',
    'weather_code', 'uv_index_max',
    'sunrise', 'sunset',
]
_OM_HOURLY_FIELDS = [
    'temperature_2m', 'precipitation', 'weather_code',
    'wind_speed_10m', 'wind_gusts_10m', 'relative_humidity_2m',
    'cloud_cover', 'cloud_cover_low', 'cloud_cover_mid', 'cloud_cover_high',
    'visibility',
]


async def fetch_open_meteo(client: httpx.AsyncClient, lat: float, lon: float, days: int = 7) -> dict:
    params = {
        'latitude': lat, 'longitude': lon,
        'daily': ','.join(_OM_DAILY_FIELDS),
        'hourly': ','.join(_OM_HOURLY_FIELDS),
        'timezone': config.DEFAULT_TZ,
        'forecast_days': days,
    }
    r = await client.get(config.OPEN_METEO_URL, params=params, timeout=10)
    r.raise_for_status()
    return r.json()


async def fetch_open_meteo_batch(
    client: httpx.AsyncClient,
    points: list[dict],
    days: int = 7,
) -> list[dict | None]:
    """Open-Meteo 批量拉取: 一次请求拉 N 个点的预报 (官方上限 50 点).

    Args:
        points: [{'lat': float, 'lon': float}, ...]  最多 50 个
        days: 预报天数
    Returns:
        list[dict|None], 长度与 points 一一对应, 拉取失败的位置为 None
    """
    if not points:
        return []
    if len(points) > 50:
        raise ValueError(f"Open-Meteo batch 上限 50 点, 收到 {len(points)}")
    lats = ','.join(f"{p['lat']:.4f}" for p in points)
    lons = ','.join(f"{p['lon']:.4f}" for p in points)
    params = {
        'latitude': lats, 'longitude': lons,
        'daily': ','.join(_OM_DAILY_FIELDS),
        'hourly': ','.join(_OM_HOURLY_FIELDS),
        'timezone': config.DEFAULT_TZ,
        'forecast_days': days,
    }
    try:
        r = await client.get(config.OPEN_METEO_URL, params=params, timeout=15)
        r.raise_for_status()
        data = r.json()
    except Exception:
        logger.exception("Open-Meteo batch 拉取失败 (n=%d)", len(points))
        return [None] * len(points)
    if not isinstance(data, list) or len(data) != len(points):
        logger.warning("Open-Meteo batch 返回结构异常, 期望 %d 项实际 %d",
                       len(points), len(data) if isinstance(data, list) else -1)
        return [None] * len(points)
    return data


async def fetch_qweather(client: httpx.AsyncClient, lat: float, lon: float) -> dict | None:
    key = config.QWEATHER_API_KEY
    if not key:
        return None
    try:
        params = {'location': f"{lon:.3f},{lat:.3f}", 'key': key}
        r = await client.get(config.QWEATHER_7D_URL, params=params, timeout=10)
        # 新版和风 403 时返回 application/problem+json, 不含 code 字段
        if r.status_code != 200:
            try:
                err = r.json()
                detail = err.get('error', {}).get('detail') or err.get('error', {}).get('title') or r.text[:200]
            except Exception:
                detail = r.text[:200]
            logger.warning("QWeather HTTP %d: %s", r.status_code, detail)
            if r.status_code == 403:
                host_filled = bool(config.QWEATHER_API_HOST)
                hint = ("API Host 配置正确但无权访问 (检查项目/订阅)"
                        if host_filled else
                        "QWEATHER_API_HOST 未配置 → 用了旧 devapi 端点. 新版账号必须填专属 API Host "
                        "(在控制台 → 设置里看, 形如 xxx.qweatherapi.com)")
                logger.warning("QWeather 403 排查: %s", hint)
            return None
        d = r.json()
        if d.get('code') != '200':
            logger.warning("QWeather code=%s msg=%s", d.get('code'), d.get('msg'))
            return None
        return d
    except Exception:
        logger.exception("QWeather 拉取异常")
        return None


async def fetch_caiyun(client: httpx.AsyncClient, lat: float, lon: float) -> dict | None:
    """彩云天气 daily 拉取 (含 QPS 限流处理 + 网格共享缓存).

    彩云个人版 QPS 较严, 但预报是大网格数据 (一个网格点 ~5km 覆盖).
    策略:
    - 按 0.1° 网格 (~11km) 共享响应, 同网格的 POI 只拉一次, 大幅减少调用量
    - 全局信号量限并发到 2, 防止瞬时大量请求
    - 遇到 Rate limit 指数退避重试 2 次
    """
    token = config.CAIYUN_TOKEN
    if not token:
        return None
    # 网格缓存: 一条 56km 路线 26 个 POI 聚到 0.2° (~22km) 后通常只剩 1-3 个网格.
    # 彩云预报在 22km 范围内差异极小 (一个天气系统).
    grid_key = (round(lat * 5) / 5, round(lon * 5) / 5)

    # fast path: 缓存命中直接返回 (含拉取过的 None 也会命中, 避免失败重复拉)
    if grid_key in _caiyun_grid_cache:
        return _caiyun_grid_cache[grid_key]

    # per-grid 锁, 同网格多 POI 并发时只让第一个拉
    lock = _caiyun_grid_locks.setdefault(grid_key, asyncio.Lock())
    async with lock:
        # double-check (上一个持有者可能已经填了)
        if grid_key in _caiyun_grid_cache:
            return _caiyun_grid_cache[grid_key]
        url = f"{config.CAIYUN_BASE}/{token}/{grid_key[1]:.4f},{grid_key[0]:.4f}/daily"
        daily = None
        for attempt in range(3):
            async with _caiyun_sem:
                try:
                    r = await client.get(url, timeout=10)
                    d = r.json()
                except Exception:
                    logger.warning("Caiyun 拉取异常 (try %d)", attempt, exc_info=True)
                    break
                if d.get('status') == 'ok':
                    daily = d['result']['daily']
                    break
                if d.get('error') == 'Rate limit exceeded' and attempt < 2:
                    await asyncio.sleep(0.8 * (attempt + 1))
                    continue
                logger.warning("Caiyun 返回非 ok: %s", d)
                break
        # 即使失败也写缓存 (None), 短期内不再重复拉 (避免持续触发限流)
        _caiyun_grid_cache[grid_key] = daily
        return daily


# ============== 归一化（各家 -> 统一 daily dict）==============
QWEATHER_TEXT_TO_WMO = {
    '晴': 0, '多云': 2, '阴': 3,
    '小雨': 61, '中雨': 63, '大雨': 65, '暴雨': 65, '大暴雨': 65, '特大暴雨': 65,
    '小雪': 71, '中雪': 73, '大雪': 75, '暴雪': 75,
    '雨夹雪': 56, '阵雨': 80, '雷阵雨': 95, '雷阵雨伴有冰雹': 99,
    '雾': 45, '冻雨': 66, '浮尘': 3, '扬沙': 3, '沙尘暴': 3,
}

CAIYUN_SKYCON_TO_WMO = {
    'CLEAR_DAY': 0, 'CLEAR_NIGHT': 0,
    'PARTLY_CLOUDY_DAY': 2, 'PARTLY_CLOUDY_NIGHT': 2,
    'CLOUDY': 3, 'WHITE_HAZE': 3, 'LIGHT_HAZE': 45, 'MODERATE_HAZE': 45, 'HEAVY_HAZE': 45,
    'LIGHT_RAIN': 61, 'MODERATE_RAIN': 63, 'HEAVY_RAIN': 65, 'STORM_RAIN': 65,
    'FOG': 45, 'LIGHT_SNOW': 71, 'MODERATE_SNOW': 73, 'HEAVY_SNOW': 75, 'STORM_SNOW': 75,
    'DUST': 3, 'SAND': 3, 'WIND': 3,
}


def normalize_open_meteo(daily: dict) -> list[dict]:
    out = []
    n = len(daily['time'])
    for i in range(n):
        out.append({
            'source': 'open-meteo',
            'date': daily['time'][i],
            'tmax': daily['temperature_2m_max'][i],
            'tmin': daily['temperature_2m_min'][i],
            'precip_mm': daily['precipitation_sum'][i] or 0,
            'precip_prob': daily['precipitation_probability_max'][i] or 0,
            'wind_max': daily['wind_speed_10m_max'][i] or 0,
            'gust_max': daily['wind_gusts_10m_max'][i] or 0,
            'wmo_code': daily['weather_code'][i],
            'uv': daily['uv_index_max'][i] or 0,
        })
    return out


def normalize_qweather(resp: dict) -> list[dict]:
    out = []
    for d in resp.get('daily', []):
        try:
            text = d.get('text', '阴')
            wmo = QWEATHER_TEXT_TO_WMO.get(text, 3)
            wind_scale = float(d.get('windScaleMax', 0) or 0)
            # Beaufort scale -> km/h: scale 0=1, 5=30, 10=89, 12=118 (近似公式)
            gust_km = 1 if wind_scale == 0 else max(2, (wind_scale ** 2) + wind_scale + 1)
            out.append({
                'source': 'qweather',
                'date': d['fxDate'],
                'tmax': float(d['tempMax']),
                'tmin': float(d['tempMin']),
                'precip_mm': float(d.get('precip', 0) or 0),
                'precip_prob': float(d.get('pop', 0) or 0),
                'wind_max': float(d.get('windSpeedMax', 0) or 0),
                'gust_max': gust_km,
                'wmo_code': wmo,
                'uv': float(d.get('uvIndex', 0) or 0),
            })
        except (KeyError, ValueError):
            continue
    return out


def normalize_caiyun(daily: dict) -> list[dict]:
    """彩云 daily -> 归一化 list.

    注意彩云的 wind 字段是嵌套字典: {'max': {'speed':.., 'direction':..}, 'min': {...}}
    temperature/precipitation 是扁平数字.
    """
    out = []
    temps = daily.get('temperature', [])
    precips = daily.get('precipitation', [])
    winds = daily.get('wind', [])
    skycons = daily.get('skycon', [])
    for i in range(len(temps)):
        try:
            skycon_val = skycons[i].get('value', 'CLOUDY') if i < len(skycons) else 'CLOUDY'
            wmo = CAIYUN_SKYCON_TO_WMO.get(skycon_val, 3)
            # wind.max 可能是 dict {'speed':..,'direction':..} (新版) 或 数字 (旧版)
            wind_max = 0
            if i < len(winds):
                wmax = winds[i].get('max', 0)
                if isinstance(wmax, dict):
                    wind_max = float(wmax.get('speed', 0))
                else:
                    wind_max = float(wmax or 0)
            # date 是 "2026-06-17T00:00+08:00", 截到 YYYY-MM-DD
            date_raw = temps[i].get('date', '')
            date_short = date_raw[:10] if date_raw else ''
            out.append({
                'source': 'caiyun',
                'date': date_short,
                'tmax': float(temps[i].get('max', 0)),
                'tmin': float(temps[i].get('min', 0)),
                'precip_mm': float(precips[i].get('avg', 0)) if i < len(precips) else 0,
                'precip_prob': float(precips[i].get('probability', 0) or 0) if i < len(precips) else 0,
                'wind_max': wind_max,
                'gust_max': wind_max * 1.3,
                'wmo_code': wmo,
                'uv': 0,
            })
        except (KeyError, ValueError, IndexError, TypeError):
            logger.warning("Caiyun normalize 跳过第 %d 天", i, exc_info=True)
            continue
    return out


# ============== 多源融合 ==============
def iqr_filter(values: list[float], weights: list[float]) -> tuple[list, list]:
    """IQR 1.5 倍异常剔除"""
    if len(values) <= 2:
        return values, weights
    sv = sorted(values)
    n = len(sv)
    q1 = sv[n // 4]
    q3 = sv[(3 * n) // 4]
    iqr = q3 - q1
    lower, upper = q1 - 1.5 * iqr, q3 + 1.5 * iqr
    fv, fw = [], []
    for v, w in zip(values, weights):
        if lower <= v <= upper:
            fv.append(v)
            fw.append(w)
    if not fv:
        return values, weights
    return fv, fw


def weighted_avg(values: list[float], weights: list[float]) -> float:
    # sum/total 已隐式归一化: 单源等于原值, N 源按比例加权 (无 key 缺失时偏移问题).
    # 单源 / 两源 / 三源已实测 2026-07-09, 详见 [[project_issues]] P1-1.
    if not values:
        return 0
    total = sum(weights)
    if total == 0:
        return sum(values) / len(values)
    return sum(v * w for v, w in zip(values, weights)) / total


def fuse_one_day(date: str, day_sources: list[dict]) -> dict:
    """
    day_sources: [{'source':'open-meteo', 'tmax':..., ...}, ...]
    融合策略: IQR + 加权 (数值字段); 雷暴优先 (WMO)
    """
    if not day_sources:
        return None
    if len(day_sources) == 1:
        d = dict(day_sources[0])
        d['source_count'] = 1
        d['sources'] = [day_sources[0]['source']]
        return d

    weights_map = config.WEIGHTS
    names = [s['source'] for s in day_sources]
    weights = [weights_map.get(n, 0.1) for n in names]

    fused = {'date': date, 'sources': names, 'source_count': len(day_sources)}

    # 数值字段: IQR + 加权平均
    for field in ['tmax', 'tmin', 'precip_mm', 'wind_max', 'gust_max', 'uv']:
        vals = [s.get(field, 0) for s in day_sources]
        fv, fw = iqr_filter(vals, weights)
        fused[field] = round(weighted_avg(fv, fw), 1)

    # precip_prob: 加权
    pp_vals = [s.get('precip_prob', 0) for s in day_sources]
    pp_fv, pp_fw = iqr_filter(pp_vals, weights)
    fused['precip_prob'] = round(weighted_avg(pp_fv, pp_fw))

    # WMO: 雷暴优先, 否则取众数
    wmos = [s.get('wmo_code', 0) for s in day_sources]
    if any(w >= 95 for w in wmos):
        fused['wmo_code'] = max(wmos)
    else:
        fused['wmo_code'] = max(set(wmos), key=wmos.count)

    return fused


def fuse_daily(all_sources_normalized: list[list[dict]], days: int = 7) -> list[dict]:
    """多源 daily 数据 -> 融合后 daily"""
    # 收集所有日期
    dates_set = set()
    for src in all_sources_normalized:
        for d in src:
            dates_set.add(d['date'])
    all_dates = sorted(dates_set)[:days]

    fused = []
    for date in all_dates:
        day_sources = []
        for src in all_sources_normalized:
            for d in src:
                if d['date'] == date:
                    day_sources.append(d)
                    break
        f = fuse_one_day(date, day_sources)
        if f:
            fused.append(f)
    return fused


# ============== 海拔修正 / WCT / 相态 ==============
def adjust_temp(T_grid: float, h_grid: float, h_real: float, rh: float) -> float:
    lapse = config.LAPSE_DRY if rh < 40 else config.LAPSE_WET
    return T_grid - lapse * (h_real - h_grid) / 1000.0


def wind_chill(T: float, V_kmh: float) -> float:
    if T >= 10 or V_kmh < 4.8:
        return T
    return 13.12 + 0.6215 * T - 11.37 * (V_kmh ** 0.16) + 0.3965 * T * (V_kmh ** 0.16)


def precip_phase(T: float, h: float) -> str:
    if h > 3500 and T < 0:
        return '雪/冰'
    if T > 3:
        return '雨'
    if T < -1:
        return '雪'
    return '雨夹雪'


def wmo_label(code: int) -> tuple[str, bool]:
    mapping = {
        0: '晴', 1: '主要晴', 2: '多云', 3: '阴',
        45: '雾', 48: '雾凇',
        51: '毛毛雨(轻)', 53: '毛毛雨(中)', 55: '毛毛雨(重)',
        56: '冻毛毛雨', 57: '冻毛毛雨(强)',
        61: '小雨', 63: '中雨', 65: '大雨',
        66: '冻雨', 67: '冻雨(强)',
        71: '小雪', 73: '中雪', 75: '大雪', 77: '霰',
        80: '阵雨', 81: '阵雨(中)', 82: '阵雨(强)',
        85: '阵雪', 86: '阵雪(强)',
        95: '雷暴', 96: '雷暴+轻冰雹', 99: '强雷暴+冰雹',
    }
    return mapping.get(code, f'WMO{code}'), code >= 95


# ============== 风险徽章 ==============
def hypothermia_risk(wct_night: float, precip_mm: float, has_thunder: bool, tmin_real: float) -> tuple[str, int]:
    score = 0
    if wct_night < -5: score += 3
    elif wct_night < 0: score += 2
    elif wct_night < 5: score += 1
    if precip_mm > 10: score += 2
    elif precip_mm > 3: score += 1
    if has_thunder: score += 1
    if tmin_real < -2: score += 1
    if score >= 4: return 'red', score
    if score >= 2: return 'yellow', score
    return 'green', score


def thunderstorm_risk(wmo_code: int, precip_prob: int) -> str:
    if wmo_code >= 95: return 'red'
    if wmo_code >= 80 and precip_prob >= 70: return 'yellow'
    if precip_prob >= 80 and wmo_code in (61, 63, 80, 81): return 'yellow'
    return 'green'


def altitude_risk(h: float) -> str:
    if h >= 4000: return 'red'
    if h >= 3500: return 'yellow'
    if h >= 3000: return 'green'
    return 'low'


def process_hourly(hourly_data: dict, h_grid: float, h_real: float) -> list[dict]:
    """对 hourly 数据做海拔修正 + WCT + 相态"""
    out = []
    times = hourly_data.get('time', [])
    n = len(times)
    # 云层/能见度字段 Open-Meteo 始终有, 缺失时填 None 由上层处理
    cloud_total = hourly_data.get('cloud_cover') or [None] * n
    cloud_low = hourly_data.get('cloud_cover_low') or [None] * n
    cloud_mid = hourly_data.get('cloud_cover_mid') or [None] * n
    cloud_high = hourly_data.get('cloud_cover_high') or [None] * n
    visibility = hourly_data.get('visibility') or [None] * n
    for i in range(n):
        T_grid = hourly_data['temperature_2m'][i]
        precip = hourly_data['precipitation'][i] or 0
        wmo = hourly_data['weather_code'][i]
        wind = hourly_data['wind_speed_10m'][i] or 0
        gust = hourly_data['wind_gusts_10m'][i] or 0
        rh = hourly_data['relative_humidity_2m'][i] or 60

        T_real = adjust_temp(T_grid, h_grid, h_real, rh)
        wct = wind_chill(T_real, gust)
        phase = precip_phase(T_real, h_real) if precip > 0.1 else '无降水'
        wmo_lab, has_thunder = wmo_label(wmo)

        out.append({
            'time': times[i],                  # "2026-06-16T13:00"
            't_grid': round(T_grid, 1),
            't_real': round(T_real, 1),
            'wct': round(wct, 1),
            'precip_mm': round(precip, 2),
            'wmo_code': wmo,
            'wmo_label': wmo_lab,
            'has_thunder': has_thunder,
            'phase': phase,
            'wind': round(wind, 1),
            'gust': round(gust, 1),
            'rh': rh,
            # 云层/能见度: 直接透传, 不做海拔修正 (云在山顶上反而更近)
            'cloud': cloud_total[i],
            'cloud_low': cloud_low[i],
            'cloud_mid': cloud_mid[i],
            'cloud_high': cloud_high[i],
            'visibility_km': round(visibility[i] / 1000, 1) if visibility[i] is not None else None,
        })
    return out


# ============== 主入口 ==============
CACHE_VERSION = 'v6'  # bump 这个会清空所有旧缓存 (v6: days 7→16 + is_long_range 字段)

async def get_forecast_for_point(
    lat: float, lon: float, h_real: float, days: int = 7, force: bool = False,
    om_data: dict | None = None,
) -> dict:
    """单点预报入口.

    Args:
        om_data: 由批量入口预拉的 Open-Meteo 数据, 传入则跳过自身 OM 拉取 (省一次请求).
                 None 时单点自拉 (向后兼容).
    """
    cache_key = hashlib.md5(
        f"{CACHE_VERSION}_{lat:.4f}_{lon:.4f}_{h_real:.0f}_{days}".encode()
    ).hexdigest()
    if not force:
        cached = get_cache(cache_key)
        if cached:
            cached['_from_cache'] = True
            return cached

    async with httpx.AsyncClient() as client:
        if om_data is not None:
            om = om_data
        else:
            om = await fetch_open_meteo(client, lat, lon, days)
        qw_task = fetch_qweather(client, lat, lon)
        cy_task = fetch_caiyun(client, lat, lon)
        qw, cy = await asyncio.gather(qw_task, cy_task, return_exceptions=True)

    if isinstance(om, Exception) or om is None:
        return {'error': f'Open-Meteo 拉取失败: {om}', 'lat': lat, 'lon': lon}

    # 归一化
    sources_norm = []
    sources_used = ['open-meteo']
    om_elev = om.get('elevation')
    try:
        sources_norm.append(normalize_open_meteo(om['daily']))
    except Exception:
        pass

    if qw and not isinstance(qw, Exception):
        try:
            sources_norm.append(normalize_qweather(qw))
            sources_used.append('qweather')
        except Exception:
            pass

    if cy and not isinstance(cy, Exception):
        try:
            sources_norm.append(normalize_caiyun(cy))
            sources_used.append('caiyun')
        except Exception:
            pass

    # 多源融合
    fused_daily = fuse_daily(sources_norm, days)

    # 日出日落: OM daily 的 sunrise/sunset 是 ISO 字符串列表, 与 date 对齐
    om_sunrise = (om.get('daily') or {}).get('sunrise') or []
    om_sunset = (om.get('daily') or {}).get('sunset') or []
    sunset_by_date = {}
    for i, d in enumerate(om.get('daily', {}).get('time', [])):
        if i < len(om_sunset) and om_sunset[i]:
            sunset_by_date[d] = om_sunset[i]
    sunrise_by_date = {}
    for i, d in enumerate(om.get('daily', {}).get('time', [])):
        if i < len(om_sunrise) and om_sunrise[i]:
            sunrise_by_date[d] = om_sunrise[i]

    # 海拔修正 + 风险徽章
    h_grid = om_elev
    daily_results = []
    today = _date.today()
    for fday in fused_daily:
        rh = 60
        tmax_r = adjust_temp(fday['tmax'], h_grid, h_real, rh)
        tmin_r = adjust_temp(fday['tmin'], h_grid, h_real, rh)
        wct_night = wind_chill(tmin_r, fday['gust_max'])
        wct_day = wind_chill(tmax_r, fday['wind_max'])
        phase = precip_phase(tmin_r, h_real) if fday['precip_mm'] > 0.1 else '无降水'
        wmo_lab, has_thunder = wmo_label(fday['wmo_code'])

        hyp_color, hyp_score = hypothermia_risk(wct_night, fday['precip_mm'], has_thunder, tmin_r)
        tstorm = thunderstorm_risk(fday['wmo_code'], fday['precip_prob'])
        alt = altitude_risk(h_real)

        # 长期预报标志: 距今 ≥ 7 天, OM 单源长波数据, 精度低. 前端灰显 + 装备过滤用它
        try:
            d_obj = datetime.strptime(fday['date'], '%Y-%m-%d').date()
            is_long_range = (d_obj - today).days >= 7
        except (ValueError, TypeError):
            is_long_range = False

        daily_results.append({
            'date': fday['date'],
            'tmax_grid': round(fday['tmax'], 1),
            'tmin_grid': round(fday['tmin'], 1),
            'tmax_real': round(tmax_r, 1),
            'tmin_real': round(tmin_r, 1),
            'wct_night': round(wct_night, 1),
            'wct_day': round(wct_day, 1),
            'precip_mm': round(fday['precip_mm'], 1),
            'precip_prob': fday['precip_prob'],
            'wind_max': round(fday['wind_max'], 1),
            'gust_max': round(fday['gust_max'], 1),
            'wmo_code': fday['wmo_code'],
            'wmo_label': wmo_lab,
            'has_thunder': has_thunder,
            'phase': phase,
            'uv': round(fday['uv'], 1),
            'sources': fday.get('sources', sources_used),
            'source_count': fday.get('source_count', len(sources_used)),
            'sunrise': sunrise_by_date.get(fday['date']),
            'sunset': sunset_by_date.get(fday['date']),
            'is_long_range': is_long_range,
            'badges': {
                'hypothermia': hyp_color,
                'thunderstorm': tstorm,
                'altitude': alt,
            },
        })

    # hourly 保留 16 天完整: track_segment 需要按到达时刻匹配, 不能切.
    # 长波 hourly 精度低的判断放在 UI 层 (8-16 天 daily 强制灰显, hourly 默认不渲染该段).
    result = {
        'lat': round(lat, 6),
        'lon': round(lon, 6),
        'h_real': h_real,
        'h_grid': h_grid,
        'sources': sources_used,
        'daily': daily_results,
        'hourly': process_hourly(om.get('hourly', {}), h_grid, h_real),
        'fetched_at': time.strftime('%Y-%m-%d %H:%M:%S'),
        '_from_cache': False,
    }
    set_cache(cache_key, result)
    return result


_OM_BATCH_LIMIT = 50  # Open-Meteo 官方批量端点上限


async def get_forecast_for_points(
    points: list[dict],
    days: int = 7,
    force: bool = False,
) -> list[dict]:
    """批量入口. points: [{'lat','lon','ele', ...}, ...].

    与逐点调用 get_forecast_for_point 等价, 但 Open-Meteo 用 1 个批量请求替代 N 个,
    显著减少网络往返和外部 API 调用次数. QWeather/Caiyun 仍各自并发拉 (走各自的缓存/限流).

    单点缓存语义保留: force=False 时命中缓存的点直接返回, 不参与批量请求.
    返回顺序与 points 一一对应.
    """
    results: list[dict | None] = [None] * len(points)
    pending_idx: list[int] = []

    # 1) 缓存过滤
    for i, p in enumerate(points):
        if force:
            pending_idx.append(i)
            continue
        cache_key = hashlib.md5(
            f"{CACHE_VERSION}_{p['lat']:.4f}_{p['lon']:.4f}_{p['ele']:.0f}_{days}".encode()
        ).hexdigest()
        cached = get_cache(cache_key)
        if cached:
            cached['_from_cache'] = True
            results[i] = cached
        else:
            pending_idx.append(i)

    if not pending_idx:
        return results  # type: ignore[return-value]

    # 2) Open-Meteo 批量拉取 (分块, 每块 ≤ 50)
    pending_points = [{'lat': points[i]['lat'], 'lon': points[i]['lon']} for i in pending_idx]
    om_chunks: list[list[int]] = [
        pending_idx[k:k + _OM_BATCH_LIMIT] for k in range(0, len(pending_idx), _OM_BATCH_LIMIT)
    ]
    async with httpx.AsyncClient() as client:
        om_data_by_idx: dict[int, dict | None] = {}
        for chunk_indices in om_chunks:
            chunk_points = [{'lat': points[i]['lat'], 'lon': points[i]['lon']}
                            for i in chunk_indices]
            om_list = await fetch_open_meteo_batch(client, chunk_points, days)
            for idx, om in zip(chunk_indices, om_list):
                om_data_by_idx[idx] = om

        # 3) 各点并发融合 (QWeather/Caiyun 仍走 client; Open-Meteo 用预拉数据)
        async def _one(i: int) -> dict:
            return await get_forecast_for_point(
                points[i]['lat'], points[i]['lon'], points[i]['ele'],
                days=days, force=force, om_data=om_data_by_idx.get(i),
            )

        tasks = [_one(i) for i in pending_idx]
        sub_results = await asyncio.gather(*tasks, return_exceptions=True)
        for i, r in zip(pending_idx, sub_results):
            if isinstance(r, Exception):
                results[i] = {'error': f'融合失败: {r}',
                              'lat': points[i]['lat'], 'lon': points[i]['lon']}
            else:
                results[i] = r

    return results  # type: ignore[return-value]
