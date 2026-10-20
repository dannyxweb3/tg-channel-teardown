# Channel Teardown · 频道拆解

Telegram 中文生态的只读观察。抓公开频道和群的历史消息，算出可核验的指标，
一个频道写一份拆解报告：它在做什么、靠什么赚钱、成本结构、风险在哪。

目前收录 **310 个样本**。

## 这个项目不做什么

- 不发布内容。发布是 [tg-course-worker](../tg-course-worker) 的事，两边不共享代码
- 不产出「可借鉴 / 可迁移到自己项目」这类结论。只做观察记录
- 不碰任何 bot token，不写数据库

## 快速开始

```bash
conda env create -f environment.yml
conda activate channel-teardown
cp .env.example .env     # 填 TG_API_ID / TG_API_HASH / WEB_PASSWORD
```

抓一个频道，看画像，写报告，起站点：

```bash
python scripts/tg_recon.py dump <slug> --limit 300
python scripts/tg_analyze.py brief <slug>
# 手写 report/<slug>.md 和 report/<slug>.json
python report/build_index.py
python -m web.server
```

站点默认 <http://127.0.0.1:8090>，密码在 `.env` 的 `WEB_PASSWORD`。

容器跑：

```bash
docker compose up -d --build
```

## 目录

| 路径 | 作用 |
|---|---|
| `scripts/tg_recon.py` | Telethon 只读抓取 → `data/recon/<slug>.jsonl` |
| `scripts/tg_analyze.py` | 指标计算、口径核查、全语料扫描 |
| `report/` | 一频道一份 `.md` + `.json`；`index.json` 由 `build_index.py` 重建 |
| `web/` | 只读展示站（FastAPI + Jinja2，无前端构建） |
| `data/recon/` | 原始语料，gitignore |

抓与算是分开的：抓一次存盘，之后反复算不用再去打扰对方的服务器。

## 注意

报告正文引用了被拆频道的原始文案，按不可信输入处理，渲染时走 HTML 白名单清洗。

`index.json` 里的 `content_warning` 是硬约束：**原始广告文案、涉及个人身份的信息、
落地域名，不得在任何 UI 或对外内容中展示。** 页面带 `noindex, nofollow`。

更多约定见 [CLAUDE.md](CLAUDE.md)。
