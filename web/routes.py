"""report/ 目录的只读展示。

没有任何写操作——数据是 git 里的静态文件，改分析走提交，不走网页。
"""
from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse

from web import auth, render
from web.deps import page, redirect

router = APIRouter()


@router.get("/healthz", response_class=PlainTextResponse)
async def healthz():
    return "ok"


# ---------------------------------------------------------------- 登录

@router.get("/login", response_class=HTMLResponse)
async def login_form(request: Request):
    return page(request, "login.html")


@router.post("/login")
async def login(request: Request, password: str = Form("")):
    if not auth.password_ok(password):
        return redirect("/login", err="密码不对")
    resp = RedirectResponse("/", status_code=303)
    auth.issue(resp)
    return resp


@router.get("/logout")
async def logout():
    resp = RedirectResponse("/login", status_code=303)
    auth.clear(resp)
    return resp


# ---------------------------------------------------------------- 报告

@router.get("/", response_class=HTMLResponse)
@router.get("/reports", response_class=HTMLResponse)
async def report_list(request: Request):
    return page(request, "reports.html", idx=render.index())


@router.get("/reports/{slug}", response_class=HTMLResponse)
async def report_detail(request: Request, slug: str):
    found = render.detail(slug)
    if found is None:
        return page(
            request, "report_detail.html",
            data=None, body="", err=f"没有 {slug} 这份报告",
        )
    data, body = found
    return page(request, "report_detail.html", data=data, body=body)
