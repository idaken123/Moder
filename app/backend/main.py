"""MH Agent Web Backend — FastAPI 入口"""
from __future__ import annotations
import asyncio
import logging
import sys
from contextlib import asynccontextmanager

# Windows 上必须使用 ProactorEventLoop 才能支持 asyncio.create_subprocess_exec
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from config import IS_DESKTOP, FRONTEND_DIST, API_PORT
from services.state_store import init_db
from services.workflow_engine import set_broadcast
from routers import workflows, artifacts, checkpoints, ws, settings, editor

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    # 注入 WebSocket 广播函数到 workflow_engine
    set_broadcast(ws.manager.broadcast)

    # 启动时主动验证激活码 + 获取 dk_hex（确保自动恢复的工作流能解密 skills）
    global _license_verified_this_session
    try:
        if check_license_local(IS_DESKTOP):
            _license_verified_this_session = True
            logging.getLogger(__name__).info("License verified on startup, dk_hex loaded")
    except Exception as e:
        logging.getLogger(__name__).warning("License check on startup failed: %s", e)

    # 自动恢复被后端重启中断的工作流
    from services.state_store import get_workflows_to_resume
    from services.workflow_engine import run_workflow
    from routers.workflows import _tasks
    resume_ids = get_workflows_to_resume()
    for wf_id in resume_ids:
        logging.getLogger(__name__).info("Auto-resuming workflow %s after restart", wf_id)
        task = asyncio.create_task(run_workflow(wf_id))
        _tasks[wf_id] = task  # 注册到 _tasks 防止心跳检测重复触发

    # 启动心跳检测（每 60 秒检查僵尸工作流并自动恢复）
    from routers.workflows import start_heartbeat
    start_heartbeat()

    yield


app = FastAPI(title="MH Agent Web", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(workflows.router)
app.include_router(artifacts.router)
app.include_router(checkpoints.router)
app.include_router(settings.router)
app.include_router(editor.router)
app.include_router(ws.router)


# ============================================================
# 激活码验证（后端）— 核心逻辑在 services/license_guard.pyd 中
# ============================================================
from fastapi import Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from services.license_guard import (
    check_license_local,
    verify_license_online,
    apply_dk_from_verify,
)


class LicenseVerifyRequest(BaseModel):
    license_key: str
    machine_id: str


@app.post("/api/license/verify")
async def license_verify(req: LicenseVerifyRequest):
    """转发激活码验证到外部服务器，成功后保存到本地并获取解密密钥。"""
    valid, message, dk_encrypted = verify_license_online(
        req.license_key, req.machine_id, IS_DESKTOP
    )
    if valid:
        global _license_verified_this_session
        _license_verified_this_session = True
        apply_dk_from_verify(dk_encrypted, req.license_key)
        return {"valid": True, "message": message}
    else:
        return {"valid": False, "message": message}


@app.get("/api/license/status")
async def license_status():
    """检查本地激活状态。"""
    return {"licensed": check_license_local(IS_DESKTOP)}


# 激活码中间件：启动时验证一次，之后用内存缓存
_LICENSE_WHITELIST = {"/api/license/verify", "/api/license/status", "/api/health", "/ws"}
_license_verified_this_session = False  # 本次启动是否已验证通过

@app.middleware("http")
async def license_middleware(request: Request, call_next):
    global _license_verified_this_session
    path = request.url.path
    # 放行白名单、静态资源、WebSocket
    if any(path.startswith(w) for w in _LICENSE_WHITELIST) or not path.startswith("/api/"):
        return await call_next(request)
    # 本次启动已验证过 → 直接放行
    if _license_verified_this_session:
        return await call_next(request)
    # 首次请求：检查激活状态
    if check_license_local(IS_DESKTOP):
        _license_verified_this_session = True
        return await call_next(request)
    return JSONResponse(status_code=403, content={"detail": "未激活，请先输入激活码"})


@app.get("/api/health")
async def health():
    return {"status": "ok", "desktop": IS_DESKTOP}


@app.get("/api/templates")
async def get_templates():
    """返回可用的工作流模板"""
    from services.workflow_engine import TEMPLATES
    result = {}
    for key, tmpl in TEMPLATES.items():
        result[key] = {
            "name": tmpl.display_name,
            "pipeline_skill": tmpl.pipeline_skill,
            "steps": [
                {"skill_name": s.skill_name, "display_name": s.display_name,
                 "has_checkpoint": s.has_checkpoint, "checkpoint_type": s.checkpoint_type}
                for s in tmpl.sub_steps
            ],
        }
    return result


# --- 桌面模式：托管前端静态文件 ---
if IS_DESKTOP and FRONTEND_DIST.is_dir():
    # 静态资源（js/css/images）
    app.mount("/assets", StaticFiles(directory=str(FRONTEND_DIST / "assets")), name="static-assets")

    # logo 等 public 文件
    @app.get("/logo.svg")
    async def serve_logo():
        logo = FRONTEND_DIST / "logo.svg"
        if logo.exists():
            return FileResponse(str(logo), media_type="image/svg+xml")

    # SPA fallback：所有非 /api /ws 路径返回 index.html
    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        # 先检查是否是静态文件
        static_file = FRONTEND_DIST / full_path
        if static_file.is_file() and not full_path.startswith("api") and not full_path.startswith("ws"):
            return FileResponse(str(static_file))
        # 否则返回 index.html（SPA 路由）
        return FileResponse(str(FRONTEND_DIST / "index.html"))
