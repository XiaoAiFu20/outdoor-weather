"""PDF 离线包生成 - reportlab + CID 中文字体"""
import io
from datetime import datetime, timedelta

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm, mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import (
    Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle, PageBreak,
)

# 注册中文 CID 字体 (reportlab 自带, 无需系统字体)
pdfmetrics.registerFont(UnicodeCIDFont('STSong-Light'))
FONT = 'STSong-Light'

# 徽章颜色
BADGE_COLORS = {
    'red':    colors.HexColor('#dc2626'),
    'yellow': colors.HexColor('#eab308'),
    'green':  colors.HexColor('#16a34a'),
    'low':    colors.HexColor('#94a3b8'),
}
BADGE_LABEL = {
    'red': '红警', 'yellow': '警惕', 'green': '注意', 'low': '低',
}


# ============== 样式 ==============
def create_styles():
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(
        name='TitleZh', fontName=FONT, fontSize=22, alignment=TA_CENTER,
        spaceAfter=3*mm, textColor=colors.HexColor('#0f172a'),
        leading=28,
    ))
    styles.add(ParagraphStyle(
        name='SubZh', fontName=FONT, fontSize=10, alignment=TA_CENTER,
        textColor=colors.HexColor('#64748b'), spaceAfter=4*mm,
    ))
    styles.add(ParagraphStyle(
        name='H2Zh', fontName=FONT, fontSize=15,
        textColor=colors.HexColor('#b91c1c'),
        spaceBefore=4*mm, spaceAfter=2*mm,
        borderWidth=0, borderColor=colors.HexColor('#b91c1c'),
    ))
    styles.add(ParagraphStyle(
        name='NormalZh', fontName=FONT, fontSize=10, leading=14,
        textColor=colors.HexColor('#1e293b'),
    ))
    styles.add(ParagraphStyle(
        name='SmallZh', fontName=FONT, fontSize=8, leading=11,
        textColor=colors.HexColor('#475569'),
    ))
    styles.add(ParagraphStyle(
        name='Disclaimer', fontName=FONT, fontSize=9, leading=12,
        textColor=colors.HexColor('#b91c1c'), alignment=TA_CENTER,
    ))
    return styles


# ============== 工具 ==============
def level_severity(l: str) -> int:
    return {'low': 0, 'green': 1, 'yellow': 2, 'red': 3}.get(l, 0)


def calc_trip_risk(forecast_data: dict, departure_date: str, days: int) -> dict:
    """计算 D1..D{days} 行程风险 (days 必填, 范围 1-7)"""
    try:
        dep = datetime.strptime(departure_date, '%Y-%m-%d')
    except (ValueError, TypeError):
        return None
    trip_days = [(dep + timedelta(days=i)).strftime('%Y-%m-%d') for i in range(days)]

    day_risks = []
    for i, day in enumerate(trip_days):
        worst = {'hypothermia': 'green', 'thunderstorm': 'green'}
        for item in forecast_data['points']:
            day_data = next((d for d in item.get('daily', []) if d['date'] == day), None)
            if not day_data:
                continue
            for k in ['hypothermia', 'thunderstorm']:
                if level_severity(day_data['badges'][k]) > level_severity(worst[k]):
                    worst[k] = day_data['badges'][k]
        worst['date'] = day
        worst['label'] = f"D{i+1} {day}"
        day_risks.append(worst)

    overall = {'hypothermia': 'green', 'thunderstorm': 'green'}
    for d in day_risks:
        for k in ['hypothermia', 'thunderstorm']:
            if level_severity(d[k]) > level_severity(overall[k]):
                overall[k] = d[k]

    if overall['hypothermia'] == 'red' or overall['thunderstorm'] == 'red':
        rec = '高风险 · 建议改期或加强装备'
        rec_color = 'red'
    elif overall['hypothermia'] == 'yellow' or overall['thunderstorm'] == 'yellow':
        rec = '警惕 · 装备补足后可出发'
        rec_color = 'yellow'
    else:
        rec = '注意 · 标准装备可出发'
        rec_color = 'green'

    return {
        'departure_date': departure_date,
        'trip_days': trip_days,
        'day_risks': day_risks,
        'overall': overall,
        'recommendation': rec,
        'recommendation_color': rec_color,
    }


def classify_route(route_data: dict | None, points: list) -> dict:
    """从 route 元数据 + key_pois 类型推断路线特征.

    返回:
        is_multi_day: bool   - 是否需要过夜装备 (睡袋/营地点)
        has_pass: bool       - 是否含垭口节点
        has_camp: bool       - 是否含营地节点
        max_h: float         - 最高海拔 (m)
        route_type: str      - snow_mountain / alpine_pass / mid_mountain / low_hill
        scene_word: str      - 高风险/低温地段的描述词, 供文案动态使用
    """
    # 海拔: 优先 route.max_ele_m, 否则用 poi.ele 估算
    max_h = 0
    if route_data:
        max_h = route_data.get('max_ele_m') or 0
    if not max_h and points:
        max_h = max((p.get('poi', {}).get('ele') or 0) for p in points)
    distance = (route_data or {}).get('distance_km') or 0

    types = set()
    for p in points:
        t = (p.get('poi') or {}).get('type')
        if t:
            types.add(t)
    has_pass = 'pass' in types
    has_camp = 'camp' in types

    # 多日判定: 有营地节点 OR 距离 ≥ 25km
    is_multi_day = has_camp or distance >= 25

    # 路线类型 + 场景词
    if max_h >= 4000:
        route_type, scene_word = 'snow_mountain', '高海拔/垭口段'
    elif max_h >= 2500 and has_pass:
        route_type, scene_word = 'alpine_pass', '垭口段'
    elif max_h >= 1500:
        route_type, scene_word = 'mid_mountain', '山顶段'
    else:
        route_type, scene_word = 'low_hill', '山顶/开阔地带'

    return {
        'is_multi_day': is_multi_day,
        'has_pass': has_pass,
        'has_camp': has_camp,
        'max_h': max_h,
        'distance': distance,
        'route_type': route_type,
        'scene_word': scene_word,
    }


def recommend_gear(
    forecast_data: dict,
    trip_dates: list[str] | None = None,
) -> dict:
    """基于预报数据生成装备建议.

    Args:
        forecast_data: _get_full_forecast 返回值
        trip_dates: 用户选定的出行日期 ['YYYY-MM-DD', ...]. None 时用全部 7 天.
                    传了就只看这些天, 避免"我说今天 2mm 你说 7 天内最大 22mm"的错配.
    """
    points = forecast_data.get('points', [])
    route_data = forecast_data.get('route') or {}
    feat = classify_route(route_data, points)
    scene = feat['scene_word']

    all_days = []
    for item in points:
        for d in item.get('daily', []):
            # 跳过长期段 (8-16 天 OM 单源长波): 精度撑不起装备决策
            if d.get('is_long_range'):
                continue
            if trip_dates is None or d.get('date') in trip_dates:
                all_days.append(d)
    if not all_days:
        return {'must': [], 'suggest': [], 'skip': [], 'route_feature': _feat_label(feat), 'scope': _scope_label(trip_dates), 'stats': {}}

    tmin_min = min(d['tmin_real'] for d in all_days)
    wct_min = min(d['wct_night'] for d in all_days)
    tmax_max = max(d['tmax_real'] for d in all_days)
    wind_max = max(d['gust_max'] for d in all_days)
    precip_max = max(d['precip_mm'] for d in all_days)
    precip_prob_max = max(d['precip_prob'] for d in all_days)
    any_thunder = any(d['has_thunder'] for d in all_days)
    any_snow = any('雪' in d['phase'] or '冰' in d['phase'] for d in all_days)

    must, suggest, skip = [], [], []

    # === 必带 ===
    # 睡袋仅多日徒步需要 (鸡公岭这种单日 8km 不带)
    if feat['is_multi_day']:
        if tmin_min < -5:
            must.append(('羽绒睡袋', f'comfort ≤ -15°C ({scene}最低 {tmin_min:.0f}°C / 体感 {wct_min:.0f}°C)'))
        elif tmin_min < 0:
            must.append(('羽绒睡袋', f'comfort ≤ -10°C ({scene}最低 {tmin_min:.0f}°C)'))
        elif tmin_min < 10:
            must.append(('棉睡袋', f'comfort ≤ {max(-5, int(tmin_min)-5):.0f}°C ({scene}最低 {tmin_min:.0f}°C)'))
        else:
            must.append(('薄棉睡袋 / 睡袋内胆', f'{scene}最低 {tmin_min:.0f}°C, 山区夜温骤降'))

    # 雷暴 → 安全装备 (与路线类型无关)
    if any_thunder:
        must.append(('急救毯', '雷暴天气失温应急必备'))
        must.append(('GPS + 离线地图', '雷暴时段能见度差易迷路'))
        must.append(('头灯 + 备用电池', '雷暴时段光线差'))

    # 防风外套 (≥6 级阵风, 36km/h)
    if wind_max >= 36:
        must.append(('防风冲锋衣裤', f'Gore-Tex 或同等, 最大阵风 {wind_max:.0f}km/h'))
    else:
        suggest.append(('防风外套', f'最大阵风 {wind_max:.0f}km/h, 备一件以备降温'))

    # 防水 (降水量阈值 + 概率, 避免晴天带伞)
    if precip_max >= 10 or (precip_max >= 5 and precip_prob_max >= 50):
        must.append(('防水冲锋衣 + 裤', f'电子产品用防水内袋, 最大日降水 {precip_max:.1f}mm'))
    elif precip_max >= 3 or precip_prob_max >= 60:
        suggest.append(('轻便雨衣', f'降水概率 {precip_prob_max}%, 最大 {precip_max:.1f}mm'))

    # 高反 (路线类型相关)
    if feat['route_type'] == 'snow_mountain':
        must.append(('氧气罐 + 红景天', f'最高海拔 {feat["max_h"]:.0f}m, 高反风险高'))
    elif feat['max_h'] >= 3500:
        suggest.append(('红景天 / 蓝养', f'最高海拔 {feat["max_h"]:.0f}m, 提前 3 天服用'))

    # === 建议带 ===
    # 冰爪/雪套仅在有雪/冰雪相态
    if any_snow:
        suggest.append(('冰爪简装', f'{scene}可能有积雪/暗冰'))
        suggest.append(('雪套', '泥泞/积雪路段'))

    # 中间层 (温度 + 路线类型)
    if tmax_max < 10:
        suggest.append(('羽绒/抓绒中间层', '全天低温'))
    elif tmin_min < 5:
        suggest.append(('抓绒/薄羽绒中间层', f'{scene}清晨低温'))
    elif feat['route_type'] in ('alpine_pass', 'snow_mountain'):
        suggest.append(('抓绒中间层', f'{scene}保暖'))
    # 低山丘陵且温度合适时不强推中间层

    # 手套/帽子 (温度 + 路线类型)
    if tmin_min < 5 or feat['route_type'] in ('alpine_pass', 'snow_mountain'):
        suggest.append(('保暖手套 + 帽子 + 颈套', f'{scene}防风保暖'))
    elif tmin_min < 15:
        suggest.append(('遮阳帽 + 速干手套', '白天防晒'))

    # 充电宝 (低温 + 多日)
    if feat['is_multi_day'] or tmin_min < 5:
        suggest.append(('充电宝 10000mAh+', '低温掉电快'))
    else:
        suggest.append(('充电宝 5000mAh+', '手机导航耗电'))

    # 防晒 (高海拔 + 强 UV)
    uv_max = max((d.get('uv') or 0) for d in all_days)
    if uv_max >= 6 or feat['max_h'] >= 3000:
        suggest.append(('防晒霜 SPF50+ + 墨镜', f'UV 指数最大 {uv_max:.0f}'))

    # === 不需要 (反向提示) ===
    if tmax_max >= 25 and not any_snow and feat['route_type'] == 'low_hill':
        # 鸡公岭这种: 28°C 闷热天气, 速干短袖反而合适
        skip.append(('厚外套 / 抓绒', f'最高 {tmax_max:.0f}°C, 闷热, 用速干衣即可'))
    if not feat['is_multi_day']:
        skip.append(('睡袋 / 营地装备', '单日徒步无需过夜'))
    if precip_max < 3 and precip_prob_max < 40:
        skip.append(('重型防水装备', f'降水概率 {precip_prob_max}%, 不需要'))

    return {
        'must': must,
        'suggest': suggest,
        'skip': skip,
        'route_feature': _feat_label(feat),
        'scope': _scope_label(trip_dates),
        'stats': {
            'tmin_min': round(tmin_min, 1),
            'tmax_max': round(tmax_max, 1),
            'wct_min': round(wct_min, 1),
            'precip_max': round(precip_max, 1),
            'precip_prob_max': precip_prob_max,
            'wind_max': round(wind_max, 1),
            'uv_max': round(uv_max, 1),
            'any_thunder': any_thunder,
            'any_snow': any_snow,
        },
    }


def _scope_label(trip_dates: list[str] | None) -> str:
    if not trip_dates:
        return '未来 7 天全程'
    if len(trip_dates) == 1:
        return f'仅 {trip_dates[0]}'
    return f'{trip_dates[0]} → {trip_dates[-1]} ({len(trip_dates)} 天)'


def _feat_label(feat: dict) -> str:
    """把 classify_route 的 dict 转成一句话标签, 给前端/PDF 顶部摘要用."""
    rt = feat.get('route_type')
    label_map = {
        'snow_mountain': '雪山高海拔',
        'alpine_pass': '高海拔垭口',
        'mid_mountain': '中山',
        'low_hill': '低山丘陵',
    }
    base = label_map.get(rt, '一般路线')
    days = '多日徒步' if feat.get('is_multi_day') else '单日徒步'
    bits = [base]
    if feat.get('has_pass'): bits.append('含垭口')
    if feat.get('has_camp'): bits.append('含营地')
    bits.append(days)
    if feat.get('max_h'):
        bits.append(f"最高 {feat['max_h']:.0f}m")
    if feat.get('distance'):
        bits.append(f"{feat['distance']:.1f}km")
    return ' · '.join(bits)


# ============== PDF 区块构建 ==============
def make_route_summary_table(route: dict, forecast_data: dict) -> Table:
    src_avail = forecast_data.get('sources_available', {})
    src_str = ' + '.join(k for k, v in src_avail.items() if v)
    rows = [
        ['路线名称', route['name']],
        ['起点 / 终点', f"{route['start']} → {route['end']}"],
        ['总距离', f"{route['distance_km']} km"],
        ['累计爬升 / 下降', f"{route['elevation_gain_m']} m / {route['elevation_loss_m']} m"],
        ['海拔区间', f"{route['min_ele_m']} - {route['max_ele_m']} m"],
        ['关键节点数', f"{len(route['key_pois'])} 个 (共 {route['total_waypoints']} 标注点)"],
        ['数据源', src_str or 'Open-Meteo'],
        ['报告生成时间', datetime.now().strftime('%Y-%m-%d %H:%M')],
    ]
    t = Table(rows, colWidths=[4*cm, 12*cm])
    t.setStyle(TableStyle([
        ('FONTNAME', (0, 0), (-1, -1), FONT),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('BACKGROUND', (0, 0), (0, -1), colors.HexColor('#f1f5f9')),
        ('TEXTCOLOR', (0, 0), (0, -1), colors.HexColor('#475569')),
        ('GRID', (0, 0), (-1, -1), 0.4, colors.HexColor('#cbd5e1')),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('LEFTPADDING', (0, 0), (-1, -1), 8),
        ('RIGHTPADDING', (0, 0), (-1, -1), 8),
        ('TOPPADDING', (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
    ]))
    return t


def make_risk_table(risk: dict, styles) -> list:
    """行程整体风险汇总"""
    out = []
    color = BADGE_COLORS[risk['recommendation_color']]
    # 推荐意见 banner
    rec_data = [[Paragraph(f"<b>整体评估: {risk['recommendation']}</b>", styles['NormalZh'])]]
    rec_tbl = Table(rec_data, colWidths=[16*cm])
    rec_tbl.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, -1), color),
        ('TEXTCOLOR', (0, 0), (-1, -1), colors.white),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 10),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 10),
    ]))
    out.append(rec_tbl)
    out.append(Spacer(1, 3*mm))

    # 逐日徽章
    header = ['行程日', '日期', '失温风险', '雷暴风险']
    rows = [header]
    for d in risk['day_risks']:
        rows.append([
            d['label'].split(' ')[0],
            d['date'],
            BADGE_LABEL[d['hypothermia']],
            BADGE_LABEL[d['thunderstorm']],
        ])
    t = Table(rows, colWidths=[3*cm, 4*cm, 4.5*cm, 4.5*cm])
    style_cmds = [
        ('FONTNAME', (0, 0), (-1, -1), FONT),
        ('FONTSIZE', (0, 0), (-1, -1), 10),
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1e293b')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('GRID', (0, 0), (-1, -1), 0.4, colors.HexColor('#cbd5e1')),
        ('TOPPADDING', (0, 0), (-1, -1), 5),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 5),
    ]
    for i, d in enumerate(risk['day_risks'], start=1):
        style_cmds.append(('BACKGROUND', (2, i), (2, i), BADGE_COLORS[d['hypothermia']]))
        style_cmds.append(('TEXTCOLOR', (2, i), (2, i), colors.white))
        style_cmds.append(('BACKGROUND', (3, i), (3, i), BADGE_COLORS[d['thunderstorm']]))
        style_cmds.append(('TEXTCOLOR', (3, i), (3, i), colors.white))
    t.setStyle(TableStyle(style_cmds))
    out.append(t)
    return out


def make_gear_table(gear: dict, styles) -> Table:
    rows = [['类别', '装备', '说明']]
    for name, desc in gear['must']:
        rows.append(['必带', name, desc])
    for name, desc in gear['suggest']:
        rows.append(['建议', name, desc])
    for name, desc in gear['skip']:
        rows.append(['不必', name, desc])
    t = Table(rows, colWidths=[2*cm, 5*cm, 9*cm])
    style_cmds = [
        ('FONTNAME', (0, 0), (-1, -1), FONT),
        ('FONTSIZE', (0, 0), (-1, -1), 9),
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#1e293b')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('ALIGN', (0, 0), (0, -1), 'CENTER'),
        ('GRID', (0, 0), (-1, -1), 0.4, colors.HexColor('#cbd5e1')),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
    ]
    must_n = len(gear['must'])
    sug_n = len(gear['suggest'])
    if must_n:
        style_cmds.append(('BACKGROUND', (0, 1), (0, must_n), BADGE_COLORS['red']))
        style_cmds.append(('TEXTCOLOR', (0, 1), (0, must_n), colors.white))
    if sug_n:
        style_cmds.append(('BACKGROUND', (0, must_n+1), (0, must_n+sug_n), BADGE_COLORS['yellow']))
        style_cmds.append(('TEXTCOLOR', (0, must_n+1), (0, must_n+sug_n), colors.white))
    if gear['skip']:
        style_cmds.append(('BACKGROUND', (0, must_n+sug_n+1), (0, -1), BADGE_COLORS['low']))
        style_cmds.append(('TEXTCOLOR', (0, must_n+sug_n+1), (0, -1), colors.white))
    t.setStyle(TableStyle(style_cmds))
    return t


def make_poi_section(item: dict, styles) -> Table:
    """单节点 7 天预报"""
    poi = item['poi']
    # 节点标题
    title = f"#{poi['id']} {poi['name']} [{poi['type']}] · {poi['ele']:.0f}m"

    rows = [['日期', '天气', '温度(高/低)', '体感(夜/日)', '降水', '相态', '风/阵风', '徽章']]
    for d in item.get('daily', []):
        b = d['badges']
        badge_str = f"失{BADGE_LABEL[b['hypothermia']]}/雷{BADGE_LABEL[b['thunderstorm']]}/高{BADGE_LABEL[b['altitude']]}"
        rows.append([
            d['date'][5:],
            d['wmo_label'] + (' ⚡' if d['has_thunder'] else ''),
            f"{d['tmax_real']}/{d['tmin_real']}°",
            f"{d['wct_night']}/{d['wct_day']}°",
            f"{d['precip_mm']}mm({d['precip_prob']}%)",
            d['phase'],
            f"{d['wind_max']}/{d['gust_max']}",
            badge_str,
        ])
    t = Table(rows, colWidths=[1.5*cm, 2.4*cm, 2.2*cm, 2.4*cm, 2.6*cm, 1.6*cm, 1.8*cm, 2.5*cm])
    style_cmds = [
        ('FONTNAME', (0, 0), (-1, -1), FONT),
        ('FONTSIZE', (0, 0), (-1, -1), 8),
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#334155')),
        ('TEXTCOLOR', (0, 0), (-1, 0), colors.white),
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('GRID', (0, 0), (-1, -1), 0.3, colors.HexColor('#cbd5e1')),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 3),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 3),
    ]
    # 8-16 天长期段: 灰底 + 浅色文字, 视觉与 web .long-term 一致 (避免触产品红线)
    for idx, d in enumerate(item.get('daily', []), start=1):
        if d.get('is_long_range'):
            style_cmds.append(('BACKGROUND', (0, idx), (-1, idx), colors.HexColor('#f1f5f9')))
            style_cmds.append(('TEXTCOLOR', (0, idx), (-1, idx), colors.HexColor('#64748b')))
    t.setStyle(TableStyle(style_cmds))

    # 标题 + 表格组合
    title_para = Paragraph(f"<b>{title}</b>", styles['NormalZh'])
    wrapper = Table([[title_para], [t]], colWidths=[16*cm])
    wrapper.setStyle(TableStyle([
        ('BOX', (0, 0), (-1, -1), 0.5, colors.HexColor('#94a3b8')),
        ('LEFTPADDING', (0, 0), (-1, -1), 0),
        ('RIGHTPADDING', (0, 0), (-1, -1), 0),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('BACKGROUND', (0, 0), (0, 0), colors.HexColor('#f8fafc')),
    ]))
    return wrapper


# ============== 主入口 ==============
def generate_route_pdf(route: dict, forecast_data: dict, departure_date: str = None, days: int = 4) -> bytes:
    """生成完整 PDF"""
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        topMargin=1.5*cm, bottomMargin=1.5*cm,
        leftMargin=1.5*cm, rightMargin=1.5*cm,
        title=f"{route['name']} 行程天气报告",
    )
    styles = create_styles()
    elements = []

    # 标题
    elements.append(Paragraph(f"⛰️ {route['name']} · 行程天气报告", styles['TitleZh']))
    elements.append(Paragraph(
        f"出发日: {departure_date or '未指定'} · 起点 {route['start']} → 终点 {route['end']}",
        styles['SubZh']
    ))

    # 路线概况
    elements.append(Paragraph("📋 路线概况", styles['H2Zh']))
    elements.append(make_route_summary_table(route, forecast_data))
    elements.append(Spacer(1, 4*mm))

    # 整体风险 + 装备建议（如有出发日）
    if departure_date:
        risk = calc_trip_risk(forecast_data, departure_date, days)
        if risk:
            elements.append(Paragraph("🚨 行程整体风险", styles['H2Zh']))
            for el in make_risk_table(risk, styles):
                elements.append(el)
            elements.append(Spacer(1, 4*mm))

    elements.append(Paragraph("🎒 装备建议", styles['H2Zh']))
    trip_dates = None
    if departure_date:
        try:
            dep = datetime.strptime(departure_date, '%Y-%m-%d')
            trip_dates = [(dep + timedelta(days=i)).strftime('%Y-%m-%d') for i in range(days)]
        except ValueError:
            trip_dates = None
    gear = recommend_gear(forecast_data, trip_dates=trip_dates)
    if gear.get('scope') or gear.get('route_feature'):
        bits = []
        if gear.get('scope'): bits.append(f"评估范围: {gear['scope']}")
        if gear.get('route_feature'): bits.append(f"路线特征: {gear['route_feature']}")
        elements.append(Paragraph(' · '.join(bits), styles['SmallZh']))
        elements.append(Spacer(1, 2*mm))
    elements.append(make_gear_table(gear, styles))

    elements.append(PageBreak())

    # 逐节点
    elements.append(Paragraph("📅 逐节点 7 天预报", styles['H2Zh']))
    elements.append(Paragraph(
        "以下预报已做海拔订正（Γ=6°C/km）。夜间温度对应营地住宿，"
        "白天温度对应徒步时段。风寒体感在 T<10°C 且 V>4.8km/h 时启用。",
        styles['SmallZh']
    ))
    elements.append(Spacer(1, 3*mm))

    # 关键节点（营地 + 垭口 + 下撤 + 危险）
    for item in forecast_data['points']:
        if 'error' in item:
            continue
        elements.append(make_poi_section(item, styles))
        elements.append(Spacer(1, 3*mm))

    elements.append(Spacer(1, 6*mm))
    elements.append(Paragraph(
        "⚠️ 本报告仅供参考。山区天气多变，最终是否出行请咨询当地向导和应急部门。<br/>"
        "  · 风险徽章规则: 见 汇报/01_产品经理_需求与功能分析.md<br/>"
        "  · 海拔修正 + WCT 公式: 见 汇报/02_全栈工程师_技术方案.md",
        styles['Disclaimer']
    ))

    doc.build(elements)
    return buf.getvalue()
