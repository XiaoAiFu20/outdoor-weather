"""统一日志配置 - 按天滚动 + 完整异常栈 + 模块级 logger 工厂

设计:
- 应用日志独立写 logs/app.log (按天滚动, 保留 7 天)
- WARNING 及以上同时打 stderr (docker logs / 控制台可见)
- 不接管 uvicorn 自身的 access log, 避免混淆
- 业务模块用 `logging.getLogger(__name__)` 拿独立命名空间
- 异常处用 logger.exception() 自动带完整 traceback (替代 `print(f"{e}")`)
"""
import logging
import logging.handlers
import sys

import config

LOG_DIR = config.BASE_DIR / 'logs'
LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = LOG_DIR / 'app.log'

_FORMAT = '%(asctime)s | %(levelname)-7s | %(name)s:%(lineno)d | %(message)s'
_DATEFMT = '%Y-%m-%d %H:%M:%S'


def setup_logging(level: str = 'INFO') -> logging.Logger:
    """初始化应用日志, 在 app.py lifespan 启动时调用一次.

    幂等: 重复调用不会叠加 handler.
    """
    root = logging.getLogger()
    if getattr(root, '_app_configured', False):
        return logging.getLogger('app')

    lv = getattr(logging, level.upper(), logging.INFO)
    root.setLevel(lv)

    formatter = logging.Formatter(_FORMAT, datefmt=_DATEFMT)

    file_handler = logging.handlers.TimedRotatingFileHandler(
        LOG_FILE, when='midnight', backupCount=7, encoding='utf-8',
    )
    file_handler.setLevel(lv)
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setLevel(logging.WARNING)
    stderr_handler.setFormatter(formatter)
    root.addHandler(stderr_handler)

    root._app_configured = True  # type: ignore[attr-defined]

    # 静默噪音 logger:
    # - httpx INFO 级别会打每次请求的完整 URL, 含 QWeather/Caiyun 的 key 明文 → 提到 WARNING
    # - uvicorn access log 保留 (便于排查接口调用, 不含密钥)
    for noisy in ('httpx', 'httpcore', 'urllib3'):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    return logging.getLogger('app')


def get_logger(name: str) -> logging.Logger:
    """业务模块入口. 推荐 `logger = get_logger(__name__)`."""
    return logging.getLogger(name)
