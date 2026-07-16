"""QWeather 新版天气预警拉取 (/weatheralert/v1/current/{lat}/{lng} + JWT Bearer).

5 分钟内存缓存 (预警不会分钟级变化)。
"""
import time
import logging

import httpx

import config
from qweather_jwt import make_qweather_jwt

logger = logging.getLogger(__name__)

_SOURCE = 'QWeather 天气预警'
_CACHE_TTL = 300  # 5 min
_cache: dict[str, tuple[float, dict]] = {}

_LEVEL_LABEL = {'blue': '蓝色', 'yellow': '黄色', 'orange': '橙色', 'red': '红色'}


def _normalize_alert(raw: dict) -> dict | None:
    """QWeather 原始 alert dict → 我们前端用的 dict."""
    et = raw.get('eventType') or {}
    title = et.get('name') or raw.get('title') or '未命名预警'
    color = raw.get('color') or {}
    level = (color.get('code') or '').lower()
    if level not in _LEVEL_LABEL:
        level = 'yellow'
    return {
        'title': title,
        'level': level,
        'level_label': _LEVEL_LABEL[level],
        'description': raw.get('description') or '',
        'instruction': raw.get('instruction') or '',
        'effective': raw.get('effectiveTime') or '',
        'expire': raw.get('expireTime') or '',
    }


async def fetch_alerts(lat: float, lon: float) -> dict:
    """拉 QWeather 天气预警. 5 min 内存缓存.

    Returns:
        {
          'alerts': [...],
          'source': 'QWeather 天气预警',
          'fetched_at': '...',
          'error': None or str (JWT 未配置 / 拉取失败时)
        }
    """
    cache_key = f"{lat:.2f}_{lon:.2f}"
    now = time.time()
    cached = _cache.get(cache_key)
    if cached and now - cached[0] < _CACHE_TTL:
        return cached[1]

    base = {
        'alerts': [],
        'source': _SOURCE,
        'fetched_at': time.strftime('%Y-%m-%dT%H:%M:%S'),
    }

    if not config.QWEATHER_JWT_PRIVATE_KEY or not config.QWEATHER_PROJECT_ID:
        base['error'] = 'JWT 未配置 (需 QWEATHER_JWT_PRIVATE_KEY + QWEATHER_PROJECT_ID)'
        return base

    try:
        token = make_qweather_jwt(config.QWEATHER_PROJECT_ID, config.QWEATHER_JWT_PRIVATE_KEY)
    except Exception as e:
        logger.exception('QWeather JWT 签发失败')
        base['error'] = f'JWT 签发失败: {e}'
        return base

    url = f"{config.QWEATHER_ALERT_URL}/{lat:.2f}/{lon:.2f}"
    try:
        async with httpx.AsyncClient() as client:
            r = await client.get(
                url,
                headers={'Authorization': f'Bearer {token}'},
                timeout=10,
            )
        if r.status_code != 200:
            base['error'] = f'QWeather HTTP {r.status_code}: {r.text[:200]}'
            logger.warning('QWeather alerts HTTP %d: %s', r.status_code, r.text[:200])
            _cache[cache_key] = (now, base)
            return base
        data = r.json()
    except Exception as e:
        base['error'] = f'拉取异常: {e}'
        logger.exception('QWeather alerts 拉取异常')
        _cache[cache_key] = (now, base)
        return base

    raw_alerts = data.get('alerts') or []
    alerts = []
    for raw in raw_alerts:
        norm = _normalize_alert(raw)
        if norm:
            alerts.append(norm)

    base['alerts'] = alerts
    _cache[cache_key] = (now, base)
    return base
