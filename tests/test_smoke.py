"""端到端冒烟测试 - 主流程 5 个用例.

跑法:
    .venv/bin/pytest tests/test_smoke.py -v

前置: 数据库已有 seed 路线 (措美峰穿扎尕那 / 香港鸡公岭). 没 seed 时先跑 backend/seed_data.py.
注意: 用例会真实拉外部 API (OM/QWeather/Caiyun), 网络不通会 fail.
"""
import pytest
from fastapi.testclient import TestClient

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'backend'))

from app import app


@pytest.fixture(scope='module')
def client():
    # TestClient with 触发 lifespan (init_db + load_routes + scheduler_loop).
    # 退出 with 时取消 scheduler task, 避免泄漏.
    with TestClient(app) as c:
        yield c


def test_status(client):
    """1. /api/status 返回 200 + 含 sources 字段"""
    r = client.get('/api/status')
    assert r.status_code == 200
    data = r.json()
    assert 'sources' in data
    assert 'open-meteo' in data['sources']
    assert isinstance(data['cache_ttl_seconds'], int)


def test_routes_list(client):
    """2. /api/routes 返回 list 且至少 1 条公开路线"""
    r = client.get('/api/routes')
    assert r.status_code == 200
    routes = r.json()
    assert isinstance(routes, list)
    assert len(routes) >= 1
    assert all('name' in route for route in routes)


def test_forecast_has_full_structure(client):
    """3. /api/forecast 返回 daily[0] 含 glow/badges/sources/sunset 等核心字段"""
    routes = client.get('/api/routes').json()
    name = routes[0]['name']
    r = client.get('/api/forecast', params={'route_name': name})
    assert r.status_code == 200
    points = r.json()['points']
    assert len(points) >= 1

    p = points[0]
    assert 'error' not in p, f"first POI has error: {p.get('error')}"
    assert 'daily' in p and len(p['daily']) >= 1
    d0 = p['daily'][0]
    # 海拔修正 + 融合核心字段
    for k in ('tmax_real', 'tmin_real', 'wct_night', 'phase', 'sources', 'badges', 'sunset', 'glow'):
        assert k in d0, f"daily[0] missing {k}"


def test_forecast_glow_field_is_meaningful(client):
    """4. daily[0].glow 字段结构合法 (available/score/level/reason)"""
    routes = client.get('/api/routes').json()
    name = routes[0]['name']
    data = client.get('/api/forecast', params={'route_name': name}).json()
    p = data['points'][0]
    glow = p['daily'][0]['glow']

    assert isinstance(glow, dict)
    assert 'available' in glow
    assert 'level' in glow
    assert 'reason' in glow
    valid_levels = {'red', 'yellow', 'green', 'low'}
    assert glow['level'] in valid_levels, f"glow.level={glow['level']} 不在 {valid_levels}"

    if glow['available']:
        # 可用时应含 sunset/best_time/window/score
        for k in ('sunset', 'best_time', 'window', 'score'):
            assert k in glow, f"glow (available=True) 缺 {k}"
        assert isinstance(glow['score'], int)
        assert -5 <= glow['score'] <= 8, f"score={glow['score']} 越界 (期望 -5~8)"


def test_export_pdf(client):
    """5. /api/export 能生成 PDF, content-type 正确, 体积合理"""
    routes = client.get('/api/routes').json()
    name = routes[0]['name']
    r = client.get('/api/export', params={'route_name': name})
    assert r.status_code == 200
    assert r.headers['content-type'] == 'application/pdf'
    assert int(r.headers['content-length']) > 1000, "PDF 太小, 可能生成失败"
    # PDF magic bytes
    assert r.content[:4] == b'%PDF'
