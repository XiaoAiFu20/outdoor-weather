"""日志监控 admin 路由.

提供浏览器实时查看 logs/app.log 的能力, 零外部依赖:
- GET /admin/logs          → HTML 页面
- GET /admin/logs/history  → 最近 N 行 JSON
- GET /admin/logs/stream   → SSE 推送 (text/event-stream)

鉴权: 仅允许本机访问 (127.0.0.1 / ::1). 远程部署请走 ssh tunnel 或反代 + auth.
"""
import asyncio
import json
import logging
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

import config
from logger import LOG_FILE

router = APIRouter(prefix='/admin', tags=['admin'])
logger = logging.getLogger(__name__)


def _is_local(request: Request) -> bool:
    client = request.client.host if request.client else ''
    return client in ('127.0.0.1', '::1', 'localhost')


def _ensure_local(request: Request):
    if not _is_local(request):
        raise HTTPException(status_code=403, detail="仅本机可访问 admin 日志, 远程请走 ssh tunnel")


@router.get('/logs')
async def logs_page(request: Request):
    _ensure_local(request)
    p = config.WEB_DIR / 'admin_logs.html'
    if not p.exists():
        raise HTTPException(status_code=404, detail='web/admin_logs.html 不存在')
    return FileResponse(str(p))


@router.get('/logs/history')
async def logs_history(
    request: Request,
    lines: int = Query(200, ge=1, le=2000),
):
    _ensure_local(request)
    if not LOG_FILE.exists():
        return JSONResponse({'lines': []})
    # 用 readlines 取最后 N 行, 避免 seek 反向读
    with open(LOG_FILE, 'r', encoding='utf-8', errors='replace') as f:
        all_lines = f.readlines()
    tail = all_lines[-lines:]
    return JSONResponse({'lines': [l.rstrip('\n') for l in tail]})


@router.get('/logs/stream')
async def logs_stream(request: Request):
    _ensure_local(request)
    media = 'text/event-stream'

    async def event_gen():
        # 先发 keep-alive, 让浏览器知道连接已建立
        yield ': connected\n\n'
        # 如果文件还不存在, 等到存在
        while not LOG_FILE.exists():
            await asyncio.sleep(1)
        with open(LOG_FILE, 'r', encoding='utf-8', errors='replace') as f:
            f.seek(0, 2)  # 跳到末尾, 只推新行
            while True:
                # 客户端断开时 request.is_disconnected() 反映状态; 检测到就退出
                if await request.is_disconnected():
                    break
                line = f.readline()
                if line:
                    payload = json.dumps({'line': line.rstrip('\n')}, ensure_ascii=False)
                    yield f"data: {payload}\n\n"
                else:
                    # 没新内容, 等一会儿 + 顺带发 keep-alive 防止代理超时
                    await asyncio.sleep(0.5)
                    yield ': ping\n\n'

    return StreamingResponse(event_gen(), media_type=media)
