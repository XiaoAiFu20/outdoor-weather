"""GPX 解析 + 关键节点提取 + 轨迹压缩"""
import math
import xml.etree.ElementTree as ET
import gpxpy

NS = {'g': 'http://www.topografix.com/GPX/1/1'}

# 关键节点类别（影响天气决策的） + 'custom' (用户手加点)
KEY_TYPES = {'camp', 'pass', 'retreat', 'danger', 'custom'}


def classify_waypoint(name: str) -> str:
    """根据 waypoint 名称分类"""
    n = (name or '').strip()
    if any(k in n for k in ['营地', '扎营', 'D1', 'D2', 'D3', 'D4', 'D5']):
        return 'camp'
    if '垭口' in n:
        return 'pass'
    if '下撤' in n:
        return 'retreat'
    if any(k in n for k in ['危险', '小心', '注意']):
        return 'danger'
    if any(k in n for k in ['水源', '过河', '瀑布', '小溪', '河']):
        return 'water'
    if any(k in n for k in ['风景', '日出', '景']):
        return 'scenery'
    return 'note'


def haversine_km(a, b) -> float:
    R = 6371.0
    lat1, lon1 = math.radians(a['lat']), math.radians(a['lon'])
    lat2, lon2 = math.radians(b['lat']), math.radians(b['lon'])
    dlat, dlon = lat2 - lat1, lon2 - lon1
    h = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


def simplify_track(pts: list, max_points: int = 500) -> list:
    """按距离间隔重采样, 保留海拔极值"""
    if len(pts) <= max_points:
        return pts
    # 第一步: 按最小距离 0.15km 重采样
    resampled = [pts[0]]
    min_dist = 0.15
    for p in pts[1:]:
        if haversine_km(resampled[-1], p) >= min_dist:
            resampled.append(p)
    # 第二步: 如果仍超量, 等距抽样
    if len(resampled) > max_points:
        step = len(resampled) / max_points
        resampled = [resampled[int(i * step)] for i in range(max_points)]
    return resampled


def parse_gpx(path: str) -> dict:
    """解析 GPX 文件 -> 结构化路线"""
    # 用 gpxpy 解析轨迹点 + waypoint
    with open(path, encoding='utf-8') as f:
        gpx = gpxpy.parse(f)

    # 用 ET 解析两步路/六只脚的 extensions 自定义字段
    tree = ET.parse(path)
    root = tree.getroot()
    name = ''
    distance_m = 0.0
    ele_gain = 0.0
    ele_loss = 0.0
    start_name = ''
    end_name = ''

    # 名字 fallback 链 (优先级从高到低):
    # 1. <extensions><name>          两步路/六只脚自定义
    # 2. <metadata><name>            标准 GPX
    # 3. <trk><name>                 轨迹段名
    # 4. 文件名 (去 .gpx 后缀)
    # 5. '未命名路线'
    def _el_text(parent, tag):
        if parent is None:
            return ''
        el = parent.find(f'g:{tag}', NS)
        if el is None:
            el = parent.find(tag)  # 不带命名空间, 兼容写得不规范的 GPX
        return el.text.strip() if el is not None and el.text else ''

    ext = root.find('g:extensions', NS)
    metadata = root.find('g:metadata', NS)
    trk = root.find('g:trk', NS)

    name = (_el_text(ext, 'name')
            or _el_text(metadata, 'name')
            or _el_text(trk, 'name'))
    if not name:
        # 文件名兜底
        try:
            from os.path import basename
            name = basename(path).rsplit('.', 1)[0]
        except Exception:
            name = ''
    if not name:
        name = '未命名路线'

    # 距离 / 爬升 / 起终点名仍然只从两步路 extensions 读 (标准 GPX 没这些字段)
    if ext is not None:
        d = _el_text(ext, 'Distance')
        if d:
            try: distance_m = float(d)
            except ValueError: pass
        eg = _el_text(ext, 'ElevationGain')
        if eg:
            try: ele_gain = float(eg)
            except ValueError: pass
        el = _el_text(ext, 'ElevationLoss')
        if el:
            try: ele_loss = float(el)
            except ValueError: pass
        start_name = _el_text(ext, 'PosStartName')
        end_name = _el_text(ext, 'PosEndName')

    # waypoints
    waypoints = []
    for i, w in enumerate(gpx.waypoints):
        cls = classify_waypoint(w.name)
        waypoints.append({
            'id': i + 1,
            'name': (w.name or '').strip(),
            'lat': round(w.latitude, 6),
            'lon': round(w.longitude, 6),
            'ele': round(w.elevation, 1) if w.elevation else 0,
            'type': cls,
        })

    # 轨迹点
    raw_pts = []
    for track in gpx.tracks:
        for seg in track.segments:
            for pt in seg.points:
                raw_pts.append({
                    'lat': pt.latitude,
                    'lon': pt.longitude,
                    'ele': pt.elevation or 0,
                })

    simplified = simplify_track(raw_pts, max_points=500)
    # 序列化为 [lon, lat, ele] 三元组 (适配 Leaflet/GeoJSON)
    track_geojson = [[p['lon'], p['lat'], round(p['ele'])] for p in simplified]

    # 海拔统计
    eles = [p['ele'] for p in raw_pts if p['ele'] > 0]
    min_ele = min(eles) if eles else 0
    max_ele = max(eles) if eles else 0

    # 关键节点
    key_pois = [w for w in waypoints if w['type'] in KEY_TYPES]

    # 中心点 (地图初始 view)
    lats = [p[1] for p in track_geojson]
    lons = [p[0] for p in track_geojson]
    center = {
        'lat': round((min(lats) + max(lats)) / 2, 5),
        'lon': round((min(lons) + max(lons)) / 2, 5),
    }

    return {
        'name': name,
        'start': start_name,
        'end': end_name,
        'distance_km': round(distance_m / 1000, 2),
        'elevation_gain_m': round(ele_gain),
        'elevation_loss_m': round(ele_loss),
        'min_ele_m': round(min_ele),
        'max_ele_m': round(max_ele),
        'center': center,
        'track': track_geojson,
        'track_point_count_original': len(raw_pts),
        'track_point_count_simplified': len(simplified),
        'waypoints': waypoints,
        'key_pois': key_pois,
        'total_waypoints': len(waypoints),
    }
