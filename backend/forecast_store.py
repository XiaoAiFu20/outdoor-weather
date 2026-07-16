"""Forecast 表 upsert helper.

用途: 把融合后的 daily 数据落库, 用于事后复盘 + UGC 实测对账.
- 按 (waypoint_id, date) upsert, 同一日多次刷新不产生重复.
- data_json 存完整 daily dict (除 glow: glow 由前端算无需持久化).
- 调用方负责 commit / close session.

未覆盖:
- hourly 不落库 (体量太大, 7 天 × 24 小时 × N POI), 仍走文件缓存.
- 历史回填需手动触发 (这里只处理刷新增量).
"""
from datetime import date as date_type, datetime
from typing import Any

from sqlalchemy.orm import Session

from models import Forecast
from logger import get_logger

logger = get_logger(__name__)

# 不持久化的字段: glow 是前端算的, 反序列化时会重建.
_EXCLUDE_KEYS = {'glow'}


def _to_date(val: Any) -> date_type | None:
    """容错转 date. OM 返回 'YYYY-MM-DD' 字符串."""
    if val is None:
        return None
    if isinstance(val, date_type):
        return val
    if isinstance(val, str):
        try:
            return date_type.fromisoformat(val[:10])
        except ValueError:
            return None
    return None


def upsert_forecast(db: Session, waypoint_id: int, daily_list: list[dict[str, Any]]) -> int:
    """按 (waypoint_id, date) upsert daily. 返回写入条数. 失败抛出, 由调用方 rollback + 记日志."""
    if not daily_list:
        return 0
    written = 0
    now = datetime.utcnow()
    for d in daily_list:
        date_val = _to_date(d.get('date'))
        if not date_val:
            continue
        payload = {k: v for k, v in d.items() if k not in _EXCLUDE_KEYS}
        existing = (
            db.query(Forecast)
            .filter(Forecast.waypoint_id == waypoint_id, Forecast.date == date_val)
            .first()
        )
        if existing:
            existing.data_json = payload
            existing.fetched_at = now
        else:
            db.add(Forecast(
                waypoint_id=waypoint_id, date=date_val, data_json=payload, fetched_at=now,
            ))
        written += 1
    db.commit()
    return written
