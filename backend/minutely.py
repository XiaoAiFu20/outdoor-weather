"""Open-Meteo minutely_15 逐分钟降水拉取 + 摘要生成.

免费、15 分钟精度、最多 4 小时。中国大陆地区为插值估算 (精度低于彩云企业版)。
"""
import time
from datetime import datetime

import httpx

import config

logger = __import__('logging').getLogger(__name__)

_SOURCE = 'Open-Meteo minutely_15'


def _hhmm(iso_local: str) -> str:
    """'2026-07-09T14:15' → '14:15'."""
    try:
        return iso_local[11:16]
    except Exception:
        return iso_local


def _make_summary(times, precip_mm, peak_mm, peak_time, rain_start, rain_end) -> str:
    if peak_mm is None or peak_mm < 0.1:
        return '未来时段无明显降水'
    if rain_start and rain_end:
        dur_min = max(15, (times.index(next(t for t in times if _hhmm(t) == rain_end)) -
                          times.index(next(t for t in times if _hhmm(t) == rain_start))) * 15)
        if peak_mm >= 5:
            intensity = '强降水'
        elif peak_mm >= 1:
            intensity = '中雨'
        else:
            intensity = '小雨'
        return f'{rain_start} 起{intensity}，持续约 {dur_min} 分钟，峰值 {peak_mm:.1f}mm@{peak_time}'
    if rain_start:
        return f'{rain_start} 起开始降水，峰值 {peak_mm:.1f}mm@{peak_time}'
    return f'持续降水，峰值 {peak_mm:.1f}mm@{peak_time}'


async def fetch_minutely(lat: float, lon: float, hours: int = 2) -> dict:
    """拉 Open-Meteo minutely_15, 返回未来 hours*4 个 15 分钟桶.

    Returns:
        dict with times/precip_mm/precip_prob/weather_codes (list, len=hours*4),
        summary (str), peak_mm/peak_time, next_rain_start/end (HH:MM or None),
        source/fetched_at/note.
        失败时返回 {'error': str, 'source': str}.
    """
    buckets = hours * 4
    params = {
        'latitude': lat,
        'longitude': lon,
        'minutely_15': 'precipitation,precipitation_probability,weather_code',
        'timezone': config.DEFAULT_TZ,
        # forecast_days=2 保证即使深夜请求也有足够未来桶 (1 天仅 96 桶, 当前时刻已过部分)
        'forecast_days': 2,
    }
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(config.OPEN_METEO_URL, params=params, timeout=10)
            r.raise_for_status()
            data = r.json()
    except Exception as e:
        logger.exception('minutely 拉取失败 lat=%s lon=%s', lat, lon)
        return {'error': f'拉取失败: {e}', 'source': _SOURCE}

    block = data.get('minutely_15') or {}
    times_raw = block.get('time') or []
    precip_raw = block.get('precipitation') or []
    prob_raw = block.get('precipitation_probability') or []
    wmo_raw = block.get('weather_code') or []

    if not times_raw:
        return {'error': 'minutely_15 字段为空 (该坐标可能不支持)', 'source': _SOURCE}

    # Open-Meteo minutely_15 从当天 00:00 开始返回 96 个桶 (24h).
    # 找到 "当前所在 15-min 桶" 的索引, 从那里切片 buckets 个 (即 "未来 hours 小时").
    now_str = time.strftime('%Y-%m-%dT%H:%M')
    start_idx = 0
    for i, t in enumerate(times_raw):
        if t >= now_str:
            start_idx = i
            break
    else:
        # 所有桶都早于 now (理论上不该发生), fallback 取最后 buckets 个
        start_idx = max(0, len(times_raw) - buckets)

    end_idx = min(start_idx + buckets, len(times_raw))
    times = [_hhmm(t) for t in times_raw[start_idx:end_idx]]
    precip = [round(float(p or 0), 2) for p in precip_raw[start_idx:end_idx]]
    prob = [int(p or 0) for p in prob_raw[start_idx:end_idx]]
    wmo = [int(w or 0) for w in wmo_raw[start_idx:end_idx]]

    # 峰值
    if precip:
        peak_idx = max(range(len(precip)), key=lambda i: precip[i])
        peak_mm = precip[peak_idx]
        peak_time = times[peak_idx]
    else:
        peak_mm = 0.0
        peak_time = None

    # 首个 >=0.1mm 的桶 → rain_start; 之后首个 <0.1mm → rain_end
    rain_start = None
    rain_end = None
    for i, p in enumerate(precip):
        if p >= 0.1:
            rain_start = times[i]
            break
    if rain_start:
        start_idx = times.index(rain_start)
        for j in range(start_idx, len(precip)):
            if precip[j] < 0.1:
                rain_end = times[j]
                break
        if rain_end is None:
            rain_end = times[-1]

    summary = _make_summary(times, precip, peak_mm, peak_time, rain_start, rain_end)

    return {
        'times': times,
        'precip_mm': precip,
        'precip_prob': prob,
        'weather_codes': wmo,
        'summary': summary,
        'next_rain_start': rain_start,
        'next_rain_end': rain_end,
        'peak_mm': peak_mm,
        'peak_time': peak_time,
        'source': _SOURCE,
        'fetched_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
        'note': '中国大陆地区精度有限 (插值估算)',
    }
