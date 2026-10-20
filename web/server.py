"""Channel Teardown 展示站：FastAPI + Jinja2 服务端渲染。

和 tg-course-worker 一样不上前端构建步骤——这个站只是把 report/ 里的
静态文件渲染出来，没有任何交互。
"""
from __future__ import annotations

import logging

import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.base import BaseHTTPMiddleware

from config.settings import WEB_DIR, settings
from web import auth

log = logging.getLogger(__name__)


def create_app() -> FastAPI:
    app = FastAPI(title="Channel Teardown", docs_url=None, redoc_url=None)
    app.add_middleware(BaseHTTPMiddleware, dispatch=auth.middleware)
    app.mount("/static", StaticFiles(directory=WEB_DIR / "static"), name="static")

    from web import routes
    app.include_router(routes.router)
    return app


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    if not settings.web_enabled:
        log.info("展示站未启用（WEB_ENABLED=false）")
        return
    if not settings.web_password.strip():
        # 这里的报告含 criminal 级样本和个人信息警示，不给无密码入口留口子
        log.warning("WEB_PASSWORD 没配置，站点不启动")
        return
    log.info("Channel Teardown: %s（默认只绑本机）", settings.web_url)
    uvicorn.run(
        create_app(), host=settings.web_host, port=settings.web_port,
        log_level="warning", access_log=False,
    )


if __name__ == "__main__":
    main()
