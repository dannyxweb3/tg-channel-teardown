# Channel Teardown · 频道拆解

Telegram 中文生态的只读观察项目：抓取公开频道/群的历史消息，算出可核验的指标，
写成一份份拆解报告，再用一个只读站点展示。

**这个项目不发布、不写库、不碰 bot token。** 内容的发布交给 tg-course-worker
（运营项目），两边通过产出的 Markdown 对接，不共享代码。

## 立场（重要）

报告**只做观察记录**，不产出「可借鉴 / 可迁移到自己项目」这类结论。
这条线是刻意的：样本里有大量灰黑产，分析它们怎么运转是一回事，
把做法搬过来是另一回事，后者不在本项目范围内。写新报告时不要加回去。

## 运行环境

**一律在 conda 环境 `channel-teardown` 中运行，Python 3.12。**

```bash
conda env create -f environment.yml     # 首次
conda activate channel-teardown
```

一次性命令用 `conda run` 避免忘记激活：

```bash
conda run -n channel-teardown python -m web.server
```

目标平台 Ubuntu。路径统一用 `pathlib`，**所有文件一律 LF 换行**（`.gitattributes` 已强制）。

## 常用命令

```bash
# 抓：公开频道历史消息 -> data/recon/<slug>.jsonl
conda run -n channel-teardown python scripts/tg_recon.py dump <slug> --limit 300

# 算
conda run -n channel-teardown python scripts/tg_analyze.py brief <slug>      # 一屏画像，写报告前先看这个
conda run -n channel-teardown python scripts/tg_analyze.py maturity <slug>   # 阅读半衰期，用来定 ERR 的成熟门槛
conda run -n channel-teardown python scripts/tg_analyze.py gaps <slug>...    # id 缺口形状
conda run -n channel-teardown python scripts/tg_analyze.py scan reactions    # 全语料扫描
conda run -n channel-teardown python scripts/tg_analyze.py rank err          # 全语料排名
conda run -n channel-teardown python scripts/tg_analyze.py audit             # 口径核查，找出报告里失真的数字
conda run -n channel-teardown python scripts/tg_analyze.py verify            # 报告校验

# 索引与展示
conda run -n channel-teardown python report/build_index.py                   # 重建 report/index.json
conda run -n channel-teardown python -m web.server                           # 起展示站
```

`tg_analyze.py --help` 写了各口径为什么是现在这样（频率不要按天取整、ERR 门槛要先测
半衰期、存活率不能单独读）——都是踩过坑定下来的，改之前先看一眼。
抓多个频道时每三个歇两分钟，避免请求过密。

## 结构

```
scripts/tg_recon.py      Telethon 只读抓取。用 user 账号不用 bot：
                         bot 读不了没被拉进去的频道，也读不了开内容保护的正文
scripts/tg_analyze.py    指标计算、口径核查、全语料扫描。抓与算分开
report/                  一频道一份 .md + .json，index.json 由 build_index.py 扫目录重建，不手写
web/render.py            读盘 + Markdown 渲染 + HTML 白名单清洗
web/routes.py            两个只读路由，没有任何写操作
data/recon/*.jsonl       原始语料。gitignore，不进版本库，也不要让 UI 去读
```

## 约定

- 配置全部走 `config/settings.py`（pydantic-settings 读 `.env`），不散落 `os.getenv`
- **抓取用的 user API 凭据和 session 文件永不入库、永不提交**。
  `.env`、`data/`、`*.session` 已在 `.gitignore` 中
- Markdown 必须**先渲染再清洗**。反过来（先 escape 再渲染）会把正文里
  `<slug>` 这类行内代码二次转义成 `&lt;slug&gt;` 显示出来
- 正文引用了被拆频道的原始广告文案，当成**不可信输入**处理：
  清洗器只放行 http(s) 链接
- slug 只认 `index.json` 里登记过的，白名单匹配，不做路径拼接
- 报告的 `metrics` 字段不是每份都齐（schema 演进过）。模板取不到的键在 Jinja 里是
  `Undefined`，对它做 `int()` 抛的是 `UndefinedError`，不属于 `TypeError/ValueError`。
  `web/deps.py` 的 `_missing()` 专门拦这个，加新过滤器时照着来
- 站点是服务端渲染（Jinja2 + 原生表单），**不引入前端构建步骤**

## 安全

- 站点默认绑 `127.0.0.1`，用 SSH 隧道访问。`WEB_PASSWORD` 为空时不启动，这是刻意的
- `index.json` 的 `content_warning` 是硬约束：**原始广告文案、涉及个人身份的信息
  （照片 / 化名 / 价目）、落地域名，不得在任何 UI 或对外内容中展示**。
  往频道发内容时这条同样适用，而且比网页更要紧——网页是自己看，频道是公开发
- 页面带 `noindex, nofollow`，不要让这些报告被搜索引擎收录
