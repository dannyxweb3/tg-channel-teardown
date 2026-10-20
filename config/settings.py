"""集中配置。所有敏感项走 .env，不进代码库。

这个项目只做观察：抓取 → 指标计算 → 写报告 → 一个只读的展示站。
不发布、不写库、不碰 bot token。发布是 tg-course-worker 的事。
"""
from pathlib import Path
from zoneinfo import ZoneInfo

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
LOG_DIR = ROOT / "logs"
WEB_DIR = ROOT / "web"
# 报告正文。静态只读，随代码进版本库，不像 data/ 那样被 gitignore
REPORT_DIR = ROOT / "report"

for _d in (DATA_DIR, LOG_DIR):
    _d.mkdir(parents=True, exist_ok=True)

load_dotenv(ROOT / ".env", override=False)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    timezone: str = "Asia/Shanghai"

    # ---- 抓取（scripts/tg_recon.py 读）----
    # my.telegram.org 申请的 user API 凭据。读别人的频道必须用自己的账号：
    # bot 读不了没被拉进去的频道，也读不了开了内容保护的频道正文。
    tg_api_id: int = 0
    tg_api_hash: str = ""

    # ---- 展示站 ----
    # 默认只绑本机。这里的报告含 criminal 级样本和个人信息警示，
    # 不要图方便改成 0.0.0.0 暴露出去。
    web_enabled: bool = True
    web_host: str = "127.0.0.1"
    web_port: int = 8090
    web_password: str = ""
    web_secret: str = ""

    @property
    def tz(self) -> ZoneInfo:
        return ZoneInfo(self.timezone)

    @property
    def web_url(self) -> str:
        host = "127.0.0.1" if self.web_host in ("0.0.0.0", "::") else self.web_host
        return f"http://{host}:{self.web_port}"


settings = Settings()
