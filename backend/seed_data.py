"""预置路线种子数据 - 8条国内经典徒步路线"""
import logging
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from database import get_db, init_db
from models import Route, Waypoint
from gpx_parser import parse_gpx
from pathlib import Path

import config
from logger import setup_logging

logger = logging.getLogger(__name__)


def seed_routes():
    setup_logging()
    init_db()
    db = next(get_db())

    gpx_files = list(config.GPX_DIR.glob('*.gpx'))

    for gpx_file in gpx_files:
        try:
            route_data = parse_gpx(str(gpx_file))

            existing = db.query(Route).filter(Route.name == route_data['name']).first()
            if existing:
                logger.info("跳过已存在路线: %s", route_data['name'])
                continue
            
            db_route = Route(
                name=route_data['name'],
                user_id=1,
                gpx_data=route_data,
                track_geojson=route_data.get('track'),
                distance_km=route_data.get('distance_km'),
                elevation_gain_m=route_data.get('elevation_gain_m'),
                elevation_loss_m=route_data.get('elevation_loss_m'),
                min_ele_m=route_data.get('min_ele_m'),
                max_ele_m=route_data.get('max_ele_m'),
                start=route_data.get('start'),
                end=route_data.get('end'),
                is_public=True,
            )
            db.add(db_route)
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
            
            logger.info("导入路线: %s (%d 个节点)",
                        route_data['name'], len(route_data.get('waypoints', [])))

        except Exception:
            logger.exception("导入失败 %s", gpx_file)

    db.close()
    logger.info("种子数据导入完成")


if __name__ == '__main__':
    seed_routes()
