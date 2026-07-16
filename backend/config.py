"""配置文件 - 多源数据源 + 路径 + 算法参数 + 数据库"""
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
GPX_DIR = BASE_DIR / 'gpx'
WEB_DIR = BASE_DIR / 'web'
CACHE_DIR = Path(__file__).resolve().parent / 'cache'
CACHE_DIR.mkdir(exist_ok=True)

load_dotenv(BASE_DIR / '.env')

# 数据库配置 (单机 SQLite. 多实例生产时切 Postgres + REDIS_URL 由未来引入)
DATABASE_URL = os.getenv(
    'DATABASE_URL',
    'sqlite:///' + str(BASE_DIR / 'data' / 'app.db')
)

# 多源数据源 key (从环境变量读, 没配置就只用 Open-Meteo)
QWEATHER_API_KEY = os.getenv('QWEATHER_API_KEY', '').strip()
QWEATHER_API_HOST = os.getenv('QWEATHER_API_HOST', '').strip()
CAIYUN_TOKEN = os.getenv('CAIYUN_TOKEN', '').strip()

# QWeather 新版天气预警 API (JWT Bearer 鉴权, 与上面的 query key 是不同体系)
# 用户需在 QWeather 控制台开通「天气预警」资源, 并配置 ED25519 公私钥对
# 私钥本地保存 (PEM 格式), 公钥上传到控制台
QWEATHER_PROJECT_ID = os.getenv('QWEATHER_PROJECT_ID', '').strip()
QWEATHER_JWT_PRIVATE_KEY = os.getenv('QWEATHER_JWT_PRIVATE_KEY', '').strip().replace('\\n', '\n')

# JWT 配置
SECRET_KEY = os.getenv('SECRET_KEY', 'dev-secret-key-change-in-production')
ALGORITHM = 'HS256'
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv('ACCESS_TOKEN_EXPIRE_MINUTES', '30'))

# 算法参数
DEFAULT_TZ = 'Asia/Shanghai'
FORECAST_DAYS = 7
LAPSE_WET = 6.0
LAPSE_DRY = 9.8

# 缓存有效期 (秒)
CACHE_TTL = int(os.getenv('CACHE_TTL', '3600'))

# 定时刷新间隔 (秒)
SCHEDULER_INTERVAL = int(os.getenv('SCHEDULER_INTERVAL', '3600'))

# 数据源权重
WEIGHTS = {
    'open-meteo': 0.5,
    'qweather': 0.3,
    'caiyun': 0.2,
}

# 数据源 URL
OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
QWEATHER_7D_URL = (
    f"https://{QWEATHER_API_HOST}/v7/weather/7d"
    if QWEATHER_API_HOST
    else "https://devapi.qweather.com/v7/weather/7d"
)
QWEATHER_ALERT_URL = (
    f"https://{QWEATHER_API_HOST}/weatheralert/v1/current"
    if QWEATHER_API_HOST
    else "https://devapi.qweather.com/weatheralert/v1/current"
)
CAIYUN_BASE = "https://api.caiyunapp.com/v2.6"

