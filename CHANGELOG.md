# Changelog

## 2026-07-15

### Added
- **择日对比**：选 2-3 个候选出发日并排看 glow/风险徽章/极端值；系统按 glow + 风险等级 + 降水综合打分推荐最优日（绿色 banner + 推荐理由）；候选日是 8-16 天长期段时卡片加黄色警示 + glow 灰显
- 8-16 天长期段 PDF 导出整行灰底（避免把长波数据当高精度看，触产品红线）
- 8-16 天 hourly 子表顶部精度警示条

## 2026-07-14

### Added
- **预报扩展到 16 天**（原 7 天上限）：覆盖「下周六」「下下周六」等中长期计划场景
- daily 加 `is_long_range` 字段（距今 ≥ 7 天）
- 融合算法自动降级：0-7 天三源融合（OM+QW+CY），8-16 天 OM 单源
- 前端 8-16 天强视觉分层：灰底 + 黄色虚线分隔标签 + glow 灰显「—」
- `track_weather` 支持长期出发日（轨迹分段天气不再报「超 7 天预报范围」）
- 装备建议（`/api/gear`）只看 0-7 天高精度段，过滤长期段避免被低精度数据带偏

## 2026-07-10

### Added
- **逐分钟降水**（`/api/minutely`）：Open-Meteo `minutely_15`，15-min 桶精度，回答「现在出发会不会被淋」
- **恶劣天气预警**（`/api/alerts`）：QWeather 新版 JWT API（EdDSA/ED25519），官方气象台蓝/黄/橙/红预警；5 min 内存缓存；JWT 未配置时 graceful degrade
- **轨迹分段天气**（`/api/track_weather`）：沿轨迹等距分段，按徒步速度估到达时刻，查对应 hourly bucket；speed slider 1.5-6 km/h，departure_hour 默认 8

## 2026-07-09

### Added
- Forecast 表落库：`refresh_all_routes` 拉到结果后 upsert，支持 UGC 历史对账
- `tests/test_smoke.py`：5 个 pytest smoke 用例
- SSE 日志监控（`/admin/logs`）：黑底级别染色 + 正则搜索 + 上限 1000 行防内存爆，仅 localhost 可访问
- service-worker 注册 + PWA 缓存生效

### Changed
- 多源权重归一化验证：实测单源/两源/三源融合结果与加权归一化期望一致（`weighted_avg` 已隐式归一化）
- docker-compose 砍 Redis 走 SQLite 单机部署

### Fixed
- `/api/routes` 路由重复定义死代码
- `upload_gpx` 不去重导致 DB 副本累积
- `backend/lib/` 脏依赖移除（统一走 venv + requirements.txt）

## 2026-07-08

### Added
- GPX 上传：同 user 同名覆盖（`action: created | updated`），不再累积副本
- 前端 localStorage「我的路线」 + 动态天数
- logging 框架（`backend/logger.py`，按天滚动）

### Changed
- 「4 天硬编码」去除：`app.py` / `schemas.py` / `models.py` / `pdf_export.py` 全部支持动态 trip-days
