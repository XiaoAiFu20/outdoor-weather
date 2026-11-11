# outdoor-weather

把通用气象数据**翻译**成重装徒步可执行的决策。不是「天气查询」，是「这条路线这几天该不该走、怎么走、带什么」。

## 技术栈

- **后端**：FastAPI + SQLAlchemy + JWT (python-jose) + bcrypt
- **数据库**：SQLite（开发）/ PostgreSQL（生产，docker-compose 已配）
- **前端**：原生 HTML/JS + PWA（manifest + service-worker）
- **部署**：Dockerfile + docker-compose.yml

## 数据源

| 源 | 用途 | 免费额度 |
|---|---|---|
| Open-Meteo | 主源，全球，forecast_days 上限 16 天 | 无 key, 无限 |
| 和风 QWeather | 7 天 daily/hourly 增强 + 恶劣天气预警 | 1000 次/天 |
| 彩云 Caiyun | 分钟级短临降水强项 | 1000 次/天 |

融合策略：IQR 1.5 异常剔除 + 加权平均（OM 0.5 / QW 0.3 / CY 0.2）。QWeather / 彩云缺数据时自动降级，单源也能出值（`fuse_daily` 单源分支）。

## 核心能力

- **海拔订正**：气温递减率 Γ=6°C/km（湿绝热，山区默认），把网格温度修正到 POI 实际海拔
- **风寒 WCT** + **降水相态识别**（雨/雪/雨夹雪）+ **UV 等级**
- **三色风险徽章**：失温 / 雷暴 / 高反（绿/黄/红）
- **glow 晚霞评分**：日落前 60min ~ 后 30min 窗口，按低中云 + 高云 + 能见度打分（0-8 分）
- **轨迹分段天气**：按徒步速度估到达时刻，匹配对应 hourly bucket
- **择日对比**：选 2-3 个候选出发日并排，系统推荐最优日（含理由 + 长期精度警示）
- **16 天预报 + 长期视觉分层**：8-16 天 OM 单源长波，前端强视觉区分（灰底 + 虚线分隔 + glow 灰显）
- **PDF 导出** + **PWA 离线缓存**

## API 端点（主要）

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/routes/upload` | 上传 GPX 路线（鉴权） |
| GET | `/api/routes` `/api/routes/mine` | 公开 / 我的路线列表 |
| GET | `/api/route/{name}` | 路线详情（含 track GeoJSON） |
| GET | `/api/forecast?route_name=` | 多源融合 16 天预报 |
| GET | `/api/minutely?lat=&lon=&hours=2` | 逐分钟降水（15-min 桶） |
| GET | `/api/alerts?lat=&lon=` | 恶劣天气预警（QWeather JWT） |
| GET | `/api/track_weather?route_name=&departure=&speed=` | 轨迹分段天气 |
| GET | `/api/gear?route_name=` | 装备建议（只看 0-7 天高精度段） |
| GET | `/api/export?route_name=&days=` | PDF 导出 |
| GET | `/admin/logs` | SSE 日志监控（仅 localhost） |

完整 schema 见 `backend/app.py` 端点定义 + `backend/schemas.py`。

## 项目结构

```
backend/
  app.py            FastAPI 入口 + 端点
  forecast.py       多源融合 + 海拔修正 + 风险徽章
  glow.py           晚霞评分
  track_segment.py  轨迹分段天气
  minutely.py       逐分钟降水
  alerts.py         恶劣天气预警 (QWeather JWT)
  pdf_export.py     PDF 导出 + 装备建议
  gpx_parser.py     GPX 解析 + 关键节点分类
  models.py         SQLAlchemy 模型
  config.py         配置 + env 读取
  cache/            forecast 文件缓存 (gitignored)
web/
  index.html        顶部控制区 + 主体 sections
  app.js            全部前端逻辑（约 1500 行）
  style.css         forecast-table / badge / glow 样式
  service-worker.js PWA 缓存
  manifest.json     PWA manifest
data/               SQLite (gitignored)
tests/
  test_smoke.py     5 个 pytest smoke 用例
```

## 开发环境

> **macOS 前置要求**：需 Python 3.10+；macOS 自带的 `python3` 一般只有 3.9，请先 `python3 --version` 确认，低于 3.10 就用 `brew install python@3.12`（Homebrew 需先 `xcode-select --install`）或 python.org 安装包另装 3.10+ 版本，并确保 `python3` 指向它。

```bash
# 1. venv + 依赖
python3 -m venv .venv
.venv/bin/pip install -r backend/requirements.txt

# 2. 环境变量
cp .env.example .env
# 编辑 .env 填 QWEATHER_API_KEY / CAIYUN_TOKEN (可选, 缺失时自动降级单源)

# 3. 启动
.venv/bin/uvicorn backend.app:app --port 8000 --reload

# 4. 访问 http://localhost:8000
#    注册用户 → 上传 GPX → 查看预报
```

## 测试

```bash
.venv/bin/pytest tests/test_smoke.py -v
```

覆盖：status / routes list / forecast 结构 / glow 字段 / PDF 导出。

## 部署

```bash
docker-compose up -d
```

单机部署走 SQLite（Redis 已砍，多实例生产时再引入）。生产环境把 `.env` 真实 key 配上即可。

## 产品红线

- UI 严禁出现「安全 / 可前往」字眼
- 所有预报强制标注「参考值 + 数据源 + 更新时间」
- 8-16 天长期段必须明显标注精度低（避免绝对化承诺）

## 常见问题

**Q: 为什么需要 Python 3.10+？**

A: 因为后端代码的类型注解直接使用了 Python 3.10 才支持的 `X | Y` 联合类型写法（PEP 604），比如 `dict | None`、`str | None`、`list[dict | None]`，并且没有用 `from __future__ import annotations` 延迟求值——模块导入时这些注解就会被执行。在 Python 3.9 及以下会直接抛 `TypeError: unsupported operand type(s) for |: 'type' and 'NoneType'`，服务根本起不来。所以 3.10 是硬性下限，不是建议；用 Docker 部署不受影响（镜像基于 `python:3.12-slim`）。
