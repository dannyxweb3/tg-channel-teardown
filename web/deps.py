"""模板环境和几个共用的小工具。"""
from __future__ import annotations

from datetime import datetime
from urllib.parse import urlencode

from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from jinja2 import Undefined

from config.settings import WEB_DIR, settings

templates = Jinja2Templates(directory=WEB_DIR / "templates")


def _fmt_dt(value: str | None, fmt: str = "%Y-%m-%d") -> str:
    """库里/报告里存的是 ISO 串，展示时转成本地时区。"""
    if not value:
        return "—"
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return value
    if dt.tzinfo is not None:
        dt = dt.astimezone(settings.tz)
    return dt.strftime(fmt)


def _missing(value) -> bool:
    """报告的 metrics 字段不是每份都齐（schema 演进过，老报告缺 median_views
    这类字段）。模板取不到的键在 Jinja 里是 Undefined，对它做 int() 抛的是
    UndefinedError —— 不属于 TypeError/ValueError，不拦就是整页 500。"""
    return value is None or isinstance(value, Undefined)


def _num(value) -> str:
    """12345 → 12,345。订阅数、阅读量都不小，不分节读不出量级。"""
    if _missing(value):
        return "—"
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return "—"


def _cny(value) -> str:
    """报告里的钱一律是 [下限, 上限] 两元数组，上下限相等时不重复写。"""
    if _missing(value) or not isinstance(value, (list, tuple)) or len(value) != 2:
        return "—"
    lo, hi = value
    try:
        lo, hi = int(lo), int(hi)
    except (TypeError, ValueError):
        return "—"
    return f"¥{lo:,}" if lo == hi else f"¥{lo:,}–{hi:,}"


def _dash(value):
    """None 统一显示成破折号。不是每个频道都测得到每个指标
    （比如 t.me/s 预览页看不到转发数），别把 Python 的 None 漏到页面上。"""
    return "—" if _missing(value) or value == "" else value


def _pct(value) -> str:
    return "—" if _missing(value) else f"{value}%"


templates.env.filters["dt"] = _fmt_dt
templates.env.filters["num"] = _num
templates.env.filters["cny"] = _cny
templates.env.filters["dash"] = _dash
templates.env.filters["pct"] = _pct

templates.env.globals["RISK_LABEL"] = {
    "low": "低风险", "medium": "中风险", "medium-high": "中高风险",
    "high": "高风险", "criminal": "刑事", "critical": "极高",
}
templates.env.globals["CONF_LABEL"] = {
    "high": "证据充分", "medium": "抽样推断", "low": "推测",
}
templates.env.globals["PAID_LABEL"] = {
    "none": "无", "subs": "买粉", "engagement": "刷互动",
    "exchange": "互推换量", "mixed": "混合", "unknown": "未知",
}


def redirect(path: str, msg: str = "", err: str = "") -> RedirectResponse:
    """POST-Redirect-GET，顺带把提示信息带回页面。"""
    params = {k: v for k, v in (("msg", msg), ("err", err)) if v}
    if params:
        sep = "&" if "?" in path else "?"
        path = f"{path}{sep}{urlencode(params)}"
    return RedirectResponse(path, status_code=303)


def page(request, name: str, **ctx):
    ctx.setdefault("msg", request.query_params.get("msg", ""))
    ctx.setdefault("err", request.query_params.get("err", ""))
    return templates.TemplateResponse(request, name, ctx)
