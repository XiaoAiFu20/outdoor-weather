"""历史同期数据查询 - ERA5再分析数据"""
import asyncio
import hashlib
import json
import logging
import time
from datetime import datetime, timedelta

import httpx

import config

logger = logging.getLogger(__name__)


def _cache_key(lat: float, lon: float, year: int, month: int) -> str:
    return hashlib.md5(f"era5_{lat:.4f}_{lon:.4f}_{year}_{month}".encode()).hexdigest()


def _cache_path(key: str):
    return config.CACHE_DIR / f"era5_{key}.json"


def get_historical_cache(key: str):
    p = _cache_path(key)
    if not p.exists():
        return None
    meta = p.with_suffix('.meta')
    if meta.exists():
        try:
            cached_at = float(meta.read_text())
            if time.time() - cached_at > 86400 * 30:
                return None
        except ValueError:
            return None
    try:
        return json.loads(p.read_text(encoding='utf-8'))
    except Exception:
        return None


def set_historical_cache(key: str, data: dict):
    p = _cache_path(key)
    meta = p.with_suffix('.meta')
    p.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')
    meta.write_text(str(time.time()))


async def fetch_era5_monthly(lat: float, lon: float, year: int, month: int) -> dict:
    """获取ERA5月度历史数据"""
    cache_key = _cache_key(lat, lon, year, month)
    cached = get_historical_cache(cache_key)
    if cached:
        return cached

    url = "https://archive-api.open-meteo.com/v1/archive"
    params = {
        'latitude': lat,
        'longitude': lon,
        'start_date': f"{year}-{month:02d}-01",
        'end_date': f"{year}-{month:02d}-28",
        'daily': ','.join([
            'temperature_2m_max', 'temperature_2m_min',
            'precipitation_sum', 'wind_speed_10m_max',
        ]),
        'timezone': config.DEFAULT_TZ,
    }

    async with httpx.AsyncClient() as client:
        try:
            r = await client.get(url, params=params, timeout=30)
            r.raise_for_status()
            data = r.json()
            
            result = {
                'year': year,
                'month': month,
                'lat': lat,
                'lon': lon,
                'tmax_avg': None,
                'tmin_avg': None,
                'precip_sum': None,
                'wind_avg': None,
                'days': [],
            }
            
            if data.get('daily'):
                daily = data['daily']
                tmax_vals = [v for v in daily.get('temperature_2m_max', []) if v is not None]
                tmin_vals = [v for v in daily.get('temperature_2m_min', []) if v is not None]
                precip_vals = [v for v in daily.get('precipitation_sum', []) if v is not None]
                wind_vals = [v for v in daily.get('wind_speed_10m_max', []) if v is not None]
                
                if tmax_vals:
                    result['tmax_avg'] = round(sum(tmax_vals) / len(tmax_vals), 1)
                if tmin_vals:
                    result['tmin_avg'] = round(sum(tmin_vals) / len(tmin_vals), 1)
                if precip_vals:
                    result['precip_sum'] = round(sum(precip_vals), 1)
                if wind_vals:
                    result['wind_avg'] = round(sum(wind_vals) / len(wind_vals), 1)
                
                times = daily.get('time', [])
                for i in range(len(times)):
                    result['days'].append({
                        'date': times[i],
                        'tmax': daily['temperature_2m_max'][i],
                        'tmin': daily['temperature_2m_min'][i],
                        'precip': daily['precipitation_sum'][i],
                        'wind': daily['wind_speed_10m_max'][i],
                    })
            
            set_historical_cache(cache_key, result)
            return result
            
        except Exception:
            logger.warning("ERA5 拉取失败", exc_info=True)
            return {
                'year': year,
                'month': month,
                'lat': lat,
                'lon': lon,
                'error': str(e),
            }


async def get_historical_period(lat: float, lon: float, years: int = 5) -> dict:
    """获取最近N年同期数据"""
    now = datetime.now()
    current_month = now.month
    tasks = []
    
    for year_offset in range(1, years + 1):
        year = now.year - year_offset
        tasks.append(fetch_era5_monthly(lat, lon, year, current_month))
    
    results = await asyncio.gather(*tasks)
    
    valid_results = [r for r in results if 'error' not in r]
    
    if not valid_results:
        return {'error': '无法获取历史数据', 'years': []}
    
    tmax_vals = [r['tmax_avg'] for r in valid_results if r['tmax_avg'] is not None]
    tmin_vals = [r['tmin_avg'] for r in valid_results if r['tmin_avg'] is not None]
    precip_vals = [r['precip_sum'] for r in valid_results if r['precip_sum'] is not None]
    wind_vals = [r['wind_avg'] for r in valid_results if r['wind_avg'] is not None]
    
    return {
        'lat': lat,
        'lon': lon,
        'month': current_month,
        'years': valid_results,
        'avg_tmax': round(sum(tmax_vals) / len(tmax_vals), 1) if tmax_vals else None,
        'avg_tmin': round(sum(tmin_vals) / len(tmin_vals), 1) if tmin_vals else None,
        'avg_precip': round(sum(precip_vals) / len(precip_vals), 1) if precip_vals else None,
        'avg_wind': round(sum(wind_vals) / len(wind_vals), 1) if wind_vals else None,
    }
