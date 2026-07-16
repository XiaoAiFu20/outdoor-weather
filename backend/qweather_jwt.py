"""QWeather 新版 API 的 JWT 签发 (EdDSA / ED25519).

QWeather 2024 起天气预警等资源改用 JWT Bearer 鉴权, 不再用旧 query key。
私钥由用户在 QWeather 控制台生成或本地 openssl 生成, 公钥上传到控制台。
"""
import jwt
from datetime import datetime, timedelta, timezone


def make_qweather_jwt(project_id: str, private_key_pem: str) -> str:
    """签发 QWeather JWT (EdDSA / ED25519), 30 分钟有效.

    Args:
        project_id: QWeather 项目 ID (控制台 → 设置 → 项目 ID)
        private_key_pem: PEM 格式 ED25519 私钥 (含 BEGIN/END 标记)

    Returns:
        三段式 JWT 字符串 (eyJ...)
    """
    now = datetime.now(timezone.utc)
    payload = {
        'sub': project_id,
        'iat': now,
        'exp': now + timedelta(minutes=30),
    }
    return jwt.encode(
        payload,
        private_key_pem,
        algorithm='EdDSA',
        headers={'kid': project_id},
    )
