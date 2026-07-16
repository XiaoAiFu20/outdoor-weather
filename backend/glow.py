"""晚霞 / 朝霞评分 (MVP)

输入: 日落 ISO 时间 + Open-Meteo hourly 列表 (含 cloud_cover/visibility/precip/wmo)
输出: {score, level, sunset_time, best_time, window, reason}

评分依据 (经验权重):
- 云层配置 (核心): 低中云 30-70% 当反射面 + 高云 < 40% 当透明度 = 完美
- 能见度: >15km 优, 8-15 良, <8 差 (雾霾会吸收红光)
- 降水: 日落窗口有大雨/雷雨 → 直接判低
- WMO: 雷暴 (>=95) 直接红转低

产品红线 (见 [[project_overview]]):
- 必须用概率/等级表达, 禁止 "一定能看到" 类话术
- 输出附 reason 字符串供前端展示 (含数据源 + 算法说明)
"""
from datetime import datetime, timedelta


def _to_dt(iso: str) -> datetime | None:
    if not iso:
        return None
    try:
        # Open-Meteo 返回 "2026-07-08T19:05" (无时区, 已是 local)
        return datetime.fromisoformat(iso)
    except ValueError:
        return None


def _score_one_hour(h: dict) -> tuple[int, list[str]]:
    """给单个 hourly 算分 + 因素列表. 满分约 8."""
    score = 0
    reasons = []

    cloud_low = h.get('cloud_low')
    cloud_mid = h.get('cloud_mid')
    cloud_high = h.get('cloud_high')
    cloud_total = h.get('cloud')
    vis = h.get('visibility_km')
    precip = h.get('precip_mm') or 0
    wmo = h.get('wmo_code') or 0

    # 降水/雷暴: 直接黑名单
    if wmo >= 95:
        return -5, ['雷暴, 不可见']
    if precip > 2.5:
        return -3, ['大雨遮蔽']
    if precip > 0.5:
        score -= 1
        reasons.append('小雨可能影响')

    # 低/中云 (反射面): 30-70% 最佳
    if cloud_low is not None and cloud_mid is not None:
        lm = (cloud_low + cloud_mid) / 2
        if 30 <= lm <= 70:
            score += 3
            reasons.append(f'低中云 {lm:.0f}% (理想反射面)')
        elif 20 <= lm < 30 or 70 < lm <= 85:
            score += 1
            reasons.append(f'低中云 {lm:.0f}% (尚可)')
        elif lm > 85:
            score -= 2
            reasons.append(f'低中云 {lm:.0f}% (过厚遮蔽)')
        # < 20% 不加分也不扣分 (无云=没有反射面, 但也意味着通透)

    # 高云 (透明度): < 40% 佳, 透过卷云看霞光最美
    if cloud_high is not None:
        if cloud_high < 40:
            score += 2
            reasons.append(f'高云 {cloud_high:.0f}% (透明度好)')
        elif cloud_high > 80:
            score -= 1
            reasons.append(f'高云 {cloud_high:.0f}% (过厚)')

    # 总云量兜底: 完全无云时降权 (霞光需要云来反射)
    if cloud_total is not None and cloud_total < 10:
        score -= 1
        reasons.append('近乎无云, 缺反射面')

    # 能见度: >15km 优, <5km 差
    if vis is not None:
        if vis >= 15:
            score += 2
            reasons.append(f'能见度 {vis:.0f}km (通透)')
        elif vis >= 8:
            score += 1
            reasons.append(f'能见度 {vis:.0f}km')
        elif vis < 5:
            score -= 2
            reasons.append(f'能见度 {vis:.0f}km (雾霾吸收霞光)')

    return score, reasons


def score_glow_for_day(sunset_iso: str | None, hourly: list[dict]) -> dict:
    """对一天的晚霞打分.

    Args:
        sunset_iso: 当天日落 ISO 时间 (Open-Meteo 格式 "2026-07-08T19:05")
        hourly: 该 POI 的完整 hourly 列表 (含 cloud_*/visibility_km)
    Returns:
        {
            'available': bool,         # 是否能算 (sunset 缺失/数据缺失时为 False)
            'score': int,              # -5 ~ 8, 越高越值得
            'level': 'low'|'green'|'yellow'|'red',
            'sunset': str,             # 原始 sunset_iso
            'best_time': str | None,   # 窗口内最高分时刻
            'window': [dict],          # 日落 -60min ~ +30min 的 hourly (含每点 score)
            'reason': str,             # 摘要 (供徽章 tooltip)
        }
    """
    sunset = _to_dt(sunset_iso)
    if not sunset or not hourly:
        return {'available': False, 'score': 0, 'level': 'low',
                'sunset': sunset_iso, 'best_time': None, 'window': [],
                'reason': '日落数据缺失'}

    # 窗口: 日落前 60 分钟到日落后 30 分钟 (这是摄影师公认的霞光最佳观测窗口)
    start = sunset - timedelta(minutes=60)
    end = sunset + timedelta(minutes=30)

    window = []
    for h in hourly:
        t = _to_dt(h.get('time'))
        if not t or t < start or t > end:
            continue
        s, _ = _score_one_hour(h)
        window.append({
            'time': h['time'],
            'cloud': h.get('cloud'),
            'cloud_low': h.get('cloud_low'),
            'cloud_mid': h.get('cloud_mid'),
            'cloud_high': h.get('cloud_high'),
            'visibility_km': h.get('visibility_km'),
            'precip_mm': h.get('precip_mm'),
            'wmo_code': h.get('wmo_code'),
            'score': s,
        })

    if not window:
        return {'available': False, 'score': 0, 'level': 'low',
                'sunset': sunset_iso, 'best_time': None, 'window': [],
                'reason': '日落窗口无 hourly 数据'}

    best = max(window, key=lambda w: w['score'])
    score = best['score']
    _, best_reasons = _score_one_hour(best)
    reason = '; '.join(best_reasons[:3]) if best_reasons else '无显著因素'

    if score >= 6:
        level = 'red'  # 注: 这里 "red" 指晚霞徽章颜色 (强烈推荐), 与风险徽章含义相反
    elif score >= 3:
        level = 'yellow'
    elif score >= 1:
        level = 'green'
    else:
        level = 'low'

    return {
        'available': True,
        'score': score,
        'level': level,
        'sunset': sunset_iso,
        'best_time': best['time'],
        'window': window,
        'reason': reason,
    }


LEVEL_LABEL = {
    'red': '强烈推荐',
    'yellow': '值得等待',
    'green': '可观望',
    'low': '不推荐',
}
