#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""频道拆解分析工具 —— 读 data/recon/*.jsonl，产出画像、口径核查与全语料扫描。

tg_recon.py 负责抓（dump），这个脚本负责算。两者的分工：
  tg_recon.py dump <slug>        → data/recon/<slug>.jsonl
  tg_analyze.py brief <slug>     → 一屏画像，写报告前先看这个

子命令：
  brief     单个样本的完整画像（频率/ERR/互动/文件/外链/标签/批次）
  gaps      id 缺口的形状（批量清理 vs 逐条删除 vs 用户自删）
  batches   发布批次（秒级间隔聚合，识别「一次倒一批」）
  words     词频，按预设词表（gambling/porn/pii/payment/airport/ad）
  tme       t.me 引用按路径前缀分类（账号 / 功能链接 / bot 深链）
  scan      全语料扫描（tme / reactions / files / domains）
  rank      全语料指标排名（err / reach / subs / files / survival）
  maturity  阅读半衰期：ERR 随帖龄的曲线，用来定这个品类的成熟门槛
  audit     口径核查（频率取整、ERR 成熟度）—— 找出报告里可能失真的数字
  verify    报告校验（md+json 齐全、JSON 合法、LF、dump 一致、内容纪律）
            内容纪律只对 high/criminal/critical 硬性执行；低风险样本里的
            @账号与站外域名多半是该账号自己的公开身份，只提示不阻断。

几条口径（都是踩过坑之后定下来的，别绕开）：
  * 频率用真实时间跨度算，不要把不足一天取整成一天；跨度 < 1 小时必须按分钟。
  * 批内间隔中位 < 60 秒时，「帖/天」测的是上传张数不是运营节奏，要按批聚合。
  * ERR 默认用帖龄 ≥14 天的帖子；成熟帖占比 < 10% 时该口径有选择偏差，
    自动降到 ≥10 天并标注 —— 不要直接引用带 (有偏) 标记的数字。
  * 14 天这个门槛是按资讯/资源类内容定的。短视频类往往 6 小时就见顶，
    先跑 maturity 看曲线，再决定该用多少小时，不要无脑套 14 天。
  * 群组的存活率必须和 id/天、缺口形状、bot 占比一起读，单独一个数没有意义。
  * 源分散度只在外链是「内容出处」时有意义；外链是广告落地页时它测的是别的东西。
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import json
import math
import re
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RECON = ROOT / "data" / "recon"
REPORT = ROOT / "report"

# ERR 的频率分档中位（202 个频道样本拟合，ERR ∝ 频率^-0.624, R²=0.697）
ERR_BANDS = [
    (0.0, 0.1, 117.56, 0.050),
    (0.1, 0.35, 53.58, 0.121),
    (0.35, 1.0, 28.57, 0.174),
    (1.0, 3.0, 13.80, 0.228),
    (3.0, 10.0, 6.48, 0.339),
    (10.0, math.inf, 2.46, 0.799),
]

# t.me 路径里不指向账号的那些前缀
TME_FUNC = {"setlanguage", "addstickers", "addemoji", "addtheme", "addlist",
            "joinchat", "share", "proxy", "socks", "boost", "iv", "bg", "c", "s", "m", "a"}

WORD_PRESETS = {
    "gambling": ["盘口", "博彩", "棋牌", "彩票", "娱乐城", "首存", "返水", "包赔",
                 "出款", "到账", "代理", "开户", "信用盘", "现金网", "BC"],
    "porn": ["成人", "裸", "无码", "自慰", "约炮", "萝莉", "幼", "学生", "校服",
             "未成年", "童", "乱伦", "母子", "强制", "调教"],
    "pii": ["身份证", "护照", "驾照", "实名", "认证", "KYC", "开房", "查档",
            "社工", "三要素", "地址证明", "手持", "人脸"],
    "payment": ["USDT", "跑分", "代收", "承兑", "四方", "洗", "U商", "代付", "备用"],
    "airport": ["机场", "节点", "订阅", "翻墙", "VPN", "线路", "流量", "套餐",
                "倍率", "中转", "原生IP", "不限流量"],
    "ad": ["广告", "推广", "赞助", "合作", "联系", "客服", "优惠", "返利", "佣金",
           "代理", "充值", "投稿", "商务", "咨询", "收款", "付费", "会员"],
}

NEG_EMOJI = {"👎", "💔", "🤬", "😢", "🤮", "🖕", "🤡", "💩", "😭", "🥱", "😐"}

DISCIPLINE = [
    (r"https?://(?!t\.me/)[a-z0-9.-]+\.(com|net|cn|io|me|org|xyz|top|cc|app|space|info|tv|work|pw|us|vip|life|icu|pro)\b",
     "站外域名原文"),
    (r"https?://\d+\.\d+\.\d+\.\d+", "裸 IP"),
    (r"[a-z0-9-]+\.(vip|top|xyz|cc|life|icu|pro):\d+", "域名:端口"),
    (r"@[A-Za-z0-9_]{5,32}", "@账号"),
    (r"t\.me/\+[A-Za-z0-9_-]+", "私有邀请链接"),
    (r"\(编号\s*[A-Z]{1,3}\d+\)", "商品编号原文"),
    (r"keyword=[^<&\s]", "搜索关键词原文"),
]

REQUIRED_KEYS = ["depth", "schema_version", "kind", "slug", "username", "url", "title",
                 "category", "one_liner", "analyzed_at", "source", "metrics",
                 "implementation", "growth", "cost", "revenue", "risk"]
RISK_LEVELS = ("low", "medium", "medium-high", "high", "criminal", "critical")


# ---------- 读取 ----------

def load(slug: str) -> tuple[dict, list[dict]]:
    p = RECON / f"{slug}.jsonl"
    if not p.exists():
        sys.exit(f"没有 {p}。先跑：python scripts/tg_recon.py dump {slug}")
    meta: dict = {}
    rows: list[dict] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        d = json.loads(line)
        if d.get("_meta"):
            meta = d
        else:
            rows.append(d)
    if not rows:
        sys.exit(f"{p} 里没有消息行。")
    return meta, rows


def each_dump():
    for p in sorted(RECON.glob("*.jsonl")):
        try:
            yield p.stem, *load(p.stem)
        except SystemExit:
            continue


def when(row: dict) -> dt.datetime:
    return dt.datetime.fromisoformat(row["date"])


def blob(rows: list[dict]) -> str:
    return "\n".join((r.get("text") or "") + " " + " ".join(r.get("urls") or []) for r in rows)


def band_of(freq: float):
    for lo, hi, err_med, reach_med in ERR_BANDS:
        if lo <= freq < hi:
            return err_med, reach_med
    return None, None


# ---------- 指标 ----------

def frequency(rows: list[dict]) -> tuple[float, float]:
    """返回 (真实跨度分钟, 帖/天)。不取整到天。"""
    ds = sorted(when(r) for r in rows)
    mins = (ds[-1] - ds[0]).total_seconds() / 60
    return mins, (len(rows) / (mins / 1440) if mins else 0.0)


def err_of(meta: dict, rows: list[dict]) -> dict:
    """ERR，带成熟度口径保护。成熟帖占比 <10% 时降到 >=10 天并标注。"""
    subs = meta.get("subscribers")
    have = [r for r in rows if r.get("views")]
    out = {"subs": subs, "n_views": len(have), "value": None, "basis": None, "biased": False}
    if not subs or not have:
        return out
    now = max(when(r) for r in rows)
    def med(days):
        v = [r["views"] for r in have if (now - when(r)).days >= days]
        return (statistics.median(v), len(v)) if v else (None, 0)
    m14, n14 = med(14)
    m10, n10 = med(10)
    ratio = n14 / len(have)
    out["mature_ratio"] = ratio
    if m14 is not None and ratio >= 0.10:
        out.update(value=m14 / subs * 100, basis=">=14d", n=n14, median_views=m14)
    elif m10 is not None:
        out.update(value=m10 / subs * 100, basis=">=10d (成熟帖占比 %.1f%%，已降档)" % (ratio * 100),
                   n=n10, median_views=m10, biased=True)
        if m14 is not None:
            out["value_14d_biased"] = m14 / subs * 100
    else:
        mv = statistics.median(r["views"] for r in have)
        out.update(value=mv / subs * 100, basis="全部（未成熟，下界）", n=len(have),
                   median_views=mv, biased=True)
    return out


def gap_shape(rows: list[dict]) -> dict:
    ids = sorted(r["id"] for r in rows)
    gaps = [ids[i + 1] - ids[i] - 1 for i in range(len(ids) - 1)]
    missing = sum(gaps)
    big = [g for g in gaps if g >= 100]
    return {
        "span": ids[-1] - ids[0] + 1, "visible": len(ids), "missing": missing,
        "median_gap": statistics.median(gaps) if gaps else 0,
        "big_blocks": len(big), "big_sum": sum(big),
        "big_share": (sum(big) / missing * 100) if missing else 0.0,
        "top": sorted(big, reverse=True)[:8],
    }


def batch_shape(rows: list[dict], cut: int = 60) -> dict:
    ds = sorted(when(r) for r in rows)
    gaps = [(ds[i + 1] - ds[i]).total_seconds() for i in range(len(ds) - 1)]
    if not gaps:
        return {}
    sizes, cur = [], 1
    for g in gaps:
        if g >= cut:
            sizes.append(cur); cur = 1
        else:
            cur += 1
    sizes.append(cur)
    span_days = (ds[-1] - ds[0]).total_seconds() / 86400
    inner = [g for g in gaps if g < cut]
    return {
        "batches": len(sizes),
        "per_day": len(sizes) / span_days if span_days else 0,
        "median_size": statistics.median(sizes), "max_size": max(sizes),
        "median_gap_s": statistics.median(gaps),
        "inner_median_s": statistics.median(inner) if inner else None,
        "under_cut_pct": len(inner) / len(gaps) * 100,
    }


def classify_tme(text: str) -> collections.Counter:
    c = collections.Counter()
    for m in re.finditer(r"t\.me/(\+?[A-Za-z0-9_]{1,40})(\?start(app)?=)?", text):
        seg, start = m.group(1), m.group(2)
        if seg.startswith("+"):
            c["+邀请（指向账号但不可命名）"] += 1
        elif seg.lower() in TME_FUNC:
            c[f"功能链接 {seg.lower()}"] += 1
        elif start:
            c["bot 深链（带启动参数）"] += 1
        else:
            c["账号引用"] += 1
    return c


# ---------- 子命令 ----------

def cmd_brief(a):
    meta, rows = load(a.slug)
    ds = sorted(when(r) for r in rows)
    mins, freq = frequency(rows)
    g = gap_shape(rows)
    b = batch_shape(rows)
    print("=" * 74)
    print(f"### {a.slug} | {meta.get('title')} | subs/members={meta.get('subscribers'):,}"
          if meta.get("subscribers") else f"### {a.slug} | {meta.get('title')}")
    print(f"n={len(rows)}  {ds[0].date()}~{ds[-1].date()}  跨度={mins:,.0f}分({mins/1440:.2f}天)  "
          f"频率={freq:,.1f}/天")
    if mins < 60:
        print("  ⚠ 跨度不足 1 小时，频率务必按分钟读，不要与其他样本的「帖/天」直接比")
    print(f"存活={g['visible']/g['span']*100:.1f}%  id跨度={g['span']}  缺{g['missing']}  "
          f"中位缺口={g['median_gap']:.0f}  大块={g['big_blocks']}个({g['big_share']:.1f}%)")

    speakers = len({r.get("sender_name") for r in rows if r.get("sender_name")})
    e = err_of(meta, rows)
    if e["value"] is not None and speakers > 1:
        print(f"ERR: 本样本有 {speakers} 个发言人（群组），而 {e['n_views']}/{len(rows)} 条带阅读数"
              f" —— 这些多半是关联频道的自动转发，不代表群本身。")
        print(f"  （若按它们算是 {e['value']:.2f}%，仅供参考，不要写进报告的 ERR 字段）")
    elif e["value"] is not None:
        em, rm = band_of(freq)
        extra = ""
        if em:
            extra = f"  档中位={em}%（{e['value']/em:.2f}×）"
        print(f"ERR={e['value']:.2f}%  口径={e['basis']}  n={e.get('n')}  "
              f"中位阅读={e.get('median_views'):.0f}{extra}")
        if e["biased"]:
            print(f"  ⚠ 口径已降档或未成熟，这个 ERR 是下界/近似，报告里要标注")
        if em:
            reach = e["value"] / 100 * freq
            print(f"日触达={reach:.3f}（档中位 {rm}，{reach/rm:.2f}×）")
    elif any(r.get("views") for r in rows):
        print("ERR: 有阅读数但无订阅数，无法计算")
    else:
        print("ERR: 无阅读数（群组，或频道被限制）—— 不计算 ERR 与日触达")

    for label, key in (("转发", "forwards"), ("评论", "replies")):
        v = [r[key] for r in rows if r.get(key) is not None and r.get(key) != 0]
        if v:
            print(f"{label} 帖={len(v)} 总={sum(v)} 中位={statistics.median(v):.0f} max={max(v)}")
    rx = [sum(r["reactions"].values()) for r in rows if r.get("reactions")]
    if rx:
        allr = collections.Counter(k for r in rows for k in (r.get("reactions") or {}))
        tot = sum(sum(r["reactions"].values()) for r in rows if r.get("reactions"))
        neg = sum(n for r in rows for k, n in (r.get("reactions") or {}).items() if k in NEG_EMOJI)
        print(f"反应 帖={len(rx)}({len(rx)/len(rows)*100:.0f}%) 中位={statistics.median(rx):.0f} "
              f"max={max(rx)} 负向占比={neg/tot*100:.1f}%  top={allr.most_common(6)}")

    tx = [len(r.get("text") or "") for r in rows]
    ne = [t for t in tx if t]
    print(f"正文 有={len(ne)}/{len(rows)} 中位={statistics.median(ne) if ne else 0:.0f} max={max(tx)}"
          f" | 媒体={collections.Counter(r.get('media') or '-' for r in rows).most_common()}")

    files = [r for r in rows if r.get("file_name")]
    if files:
        tot = sum(r.get("file_mb") or 0 for r in files)
        rate = tot / (mins * 60) if mins else 0
        print(f"文件 n={len(files)} {tot:,.1f}MB 均值={tot/len(files):.1f}MB 上传率下界={rate:.4f}MB/s"
              f" 扩展={collections.Counter((r['file_name'].rsplit('.',1)+[''])[1].lower() for r in files).most_common(5)}")
        if rate > 1:
            print("  ⚠ 上传率 >1MB/s 且持续：需要机房级上行 + 自动化上传，不是人工在发")

    hosts = collections.Counter()
    for r in rows:
        for u in r.get("urls") or []:
            m = re.match(r"https?://([^/]+)", u)
            if m:
                hosts[m.group(1)] += 1
    if hosts:
        ext = {h: n for h, n in hosts.items() if h != "t.me"}
        print(f"域名 {hosts.most_common(8)}")
        print(f"  链接密度={sum(hosts.values())/len(rows):.3f}  不同域名={len(hosts)}  "
              f"源分散度={len(ext)/len(rows):.3f}（仅在外链是内容出处时有意义）")
        with_link = sum(1 for r in rows if r.get("urls"))
        print(f"  带链接的帖={with_link}/{len(rows)}（{with_link/len(rows)*100:.1f}%）"
              + ("  ⚠ 链接高度集中，平均密度不反映版面功能" if with_link / len(rows) < 0.1 and sum(hosts.values()) / len(rows) > 0.5 else ""))

    bl = blob(rows)
    tme = classify_tme(bl)
    if tme:
        print(f"t.me 分类 {dict(tme)}")
        tgt = collections.Counter(re.findall(r"t\.me/(\+?[A-Za-z0-9_]{1,40})", bl))
        counts = collections.Counter(tgt.values())
        print(f"  不同目标={len(tgt)}  出现次数分布={counts.most_common(4)}  top={tgt.most_common(6)}")
        if len(tgt) >= 5 and len(counts) == 1:
            print("  ⚠ 所有目标出现次数完全一致 → 固定链接块被整体贴出，矩阵特征")

    par = collections.Counter(re.findall(r"[?&]([a-zA-Z_]{1,20})=", bl))
    if par:
        print(f"参数 {par.most_common(8)}")
    tags = re.findall(r"#[\w一-鿿]+", bl)
    if tags:
        c = collections.Counter(tags)
        cover = sum(1 for r in rows if re.search(r"#[\w一-鿿]+", r.get("text") or ""))
        print(f"标签 总{len(tags)} 唯一{len(c)} 每帖{len(tags)/len(rows):.2f} "
              f"覆盖{cover}/{len(rows)}({cover/len(rows)*100:.0f}%)")
        print(f"  top {c.most_common(10)}")
        top_cov = c.most_common(1)[0][1] / len(rows)
        if top_cov > 0.8:
            print(f"  ⚠ 最高频标签覆盖 {top_cov*100:.0f}% → 落款/品牌，信息量≈0")
        vals = list(c.values())
        if len(c) >= 5 and min(vals) >= 3 and statistics.pstdev(vals) < 1:
            print("  ⚠ 标签次数方差≈0 且每个都出现多次 → 固定字段组，不是分类")
        elif len(c) >= 5 and max(vals) <= 2:
            print("  · 标签几乎都只出现一两次 → 可变分面或排版强调，不是可复用的分类")

    sp = collections.Counter(r.get("sender_name") for r in rows if r.get("sender_name"))
    if sp:
        nb = sum(1 for r in rows if r.get("sender_bot"))
        print(f"发言人={len(sp)} top10占={sum(n for _, n in sp.most_common(10))/len(rows)*100:.0f}% "
              f"bot={nb}({nb/len(rows)*100:.0f}%) top6={sp.most_common(6)}")
        if len(sp) == 1:
            print("  ⚠ 只有一个发言人 —— 频道本身，或一个被单一来源独占的群")
    fwd = collections.Counter(r["fwd_from"] for r in rows if r.get("fwd_from"))
    if fwd:
        print(f"转自 {fwd.most_common(6)}")

    if b:
        print(f"批次 {b['batches']}批 {b['per_day']:.1f}批/天 每批中位={b['median_size']:.0f} "
              f"max={b['max_size']} 间隔中位={b['median_gap_s']:.0f}秒 <60秒占比={b['under_cut_pct']:.0f}%")
        if b["median_gap_s"] < 60:
            print(f"  ⚠ 间隔中位 <60 秒 → 「{freq:,.1f} 帖/天」测的是上传张数；"
                  f"按批算是 {b['per_day']:.1f} 批/天 × 每批 {b['median_size']:.0f}")

    ad = re.compile("|".join(WORD_PRESETS["ad"]))
    hit = sum(1 for r in rows if ad.search(r.get("text") or ""))
    print(f"广告粗筛={hit}/{len(rows)}（{hit/len(rows)*100:.0f}%）"
          "  ← 政经/黑产/影视/技术长文语境下误报或漏报都很高，务必逐条核对")
    print("开头:", [(r.get("text") or "")[:14] for r in rows[:4]])
    for r in sorted((r for r in rows if r.get("views")), key=lambda x: -x["views"])[:2]:
        print(f"  阅读{r['views']} f={r.get('forwards')} :: {(r.get('text') or '')[:110]!r}")


def cmd_gaps(a):
    for s in a.slugs:
        _, rows = load(s)
        g = gap_shape(rows)
        bot = sum(1 for r in rows if r.get("sender_bot")) / len(rows) * 100
        if g["missing"] == 0:
            read = "零缺口"
        elif g["big_share"] > 45 and g["median_gap"] == 0:
            read = "纯批量（日常不删，只在少数时刻集中删）"
        elif g["big_share"] > 45:
            read = "批量 + 逐条并存"
        elif g["big_blocks"] == 0 and g["median_gap"] <= 1 and g["missing"] < len(rows):
            read = "零散且量小 → 更可能是用户自删，不是治理"
        elif bot > 15 and g["median_gap"] > 10:
            read = "缺口主体可能是门禁 bot 的临时消息，与内容治理无关"
        else:
            read = "以逐条删除为主"
        print(f"{s}: span={g['span']} visible={g['visible']} missing={g['missing']} "
              f"medgap={g['median_gap']:.0f} big={g['big_blocks']}({g['big_share']:.1f}%) "
              f"bot={bot:.0f}%  → {read}")
        if g["top"]:
            print(f"   最大的几个块: {g['top']}")


def cmd_batches(a):
    for s in a.slugs:
        _, rows = load(s)
        mins, freq = frequency(rows)
        b = batch_shape(rows, a.cut)
        print(f"{s}: 表观 {freq:,.1f} 帖/天 | {b['batches']} 批 = {b['per_day']:.1f} 批/天, "
              f"每批中位 {b['median_size']:.0f} / max {b['max_size']}, "
              f"间隔中位 {b['median_gap_s']:.0f}秒, <{a.cut}秒占 {b['under_cut_pct']:.0f}%")
        if b["median_gap_s"] >= a.cut:
            print("   （间隔中位 ≥ 阈值，按帖算即可，不必按批）")


def cmd_words(a):
    """词频。同时报「总次数 / 命中帖数 / 去重正文后的次数」。

    三个数一起看才有意义：一条被反复贴出的模板，会让总次数虚高几十倍，
    而它其实只是一条信息。命中帖数与去重次数能把这种虚高压回去。
    """
    _, rows = load(a.slug)
    texts = [r.get("text") or "" for r in rows]
    uniq = list(dict.fromkeys(t for t in texts if t.strip()))
    text, utext = "\n".join(texts), "\n".join(uniq)
    print(f"{a.slug}: {len(rows)} 条，其中去重后 {len(uniq)} 条不同正文"
          + ("  ⚠ 重复正文很多，总次数会被模板放大" if len(uniq) < len(rows) * 0.6 else ""))
    groups = [(a.preset, WORD_PRESETS[a.preset])] if a.preset else list(WORD_PRESETS.items())
    if a.extra:
        groups.append(("自定义", a.extra))
    for name, words in groups:
        out = []
        for w in words:
            n = len(re.findall(w, text, re.I))
            if not n:
                continue
            posts = sum(1 for t in texts if re.search(w, t, re.I))
            un = len(re.findall(w, utext, re.I))
            flag = " ←模板重复" if n >= un * 3 and n >= 6 else ""
            out.append(f"{w}:{n}/{posts}帖/去重{un}{flag}")
        if out:
            print(f"  {name:9s} " + "  ".join(out))
    print("  （格式：词:总次数/命中帖数/去重正文后次数。三者差得多说明有模板重复，"
          "按帖数读。预设词表在不同语境下误报与漏报都很高，务必回正文核对。）")


def cmd_tme(a):
    _, rows = load(a.slug)
    bl = blob(rows)
    c = classify_tme(bl)
    total = sum(c.values())
    print(f"{a.slug}: t.me 引用 {total} 次")
    for k, n in c.most_common():
        print(f"   {k:28s} {n:5d}  {n/total*100:5.1f}%")
    tgt = collections.Counter(re.findall(r"t\.me/(\+?[A-Za-z0-9_]{1,40})", bl))
    print(f"   不同目标 {len(tgt)}: {tgt.most_common(20)}")


def cmd_scan(a):
    if a.what == "tme":
        tot = collections.Counter(); per = collections.Counter()
        for slug, _, rows in each_dump():
            c = classify_tme(blob(rows))
            tot.update(c)
            per[slug] += sum(n for k, n in c.items() if not k.startswith("账号"))
        n = sum(tot.values())
        print(f"全语料 t.me 引用 {n:,}")
        for k, v in tot.most_common():
            print(f"   {k:28s} {v:7,}  {v/n*100:5.2f}%")
        print(f"   非账号引用最多的样本: {per.most_common(10)}")
    elif a.what == "reactions":
        res = []
        for slug, _, rows in each_dump():
            c = collections.Counter()
            for r in rows:
                c.update(r.get("reactions") or {})
            tot = sum(c.values())
            if tot < 200:
                continue
            neg = sum(v for k, v in c.items() if k in NEG_EMOJI)
            res.append((neg / tot * 100, slug, tot, [k for k, _ in c.most_common(8) if k in NEG_EMOJI]))
        res.sort(reverse=True)
        withneg = sum(1 for r in res if r[3])
        print(f"反应总量 ≥200 的样本 {len(res)} 个；top8 里含负向表情的 {withneg} 个 "
              f"({withneg/len(res)*100:.0f}%) —— 负向是常态，不是异常信号")
        for pct, slug, tot, nt in res[:a.top]:
            print(f"   {pct:5.1f}%  {slug:26s} tot={tot:7,} neg-in-top8={nt}")
    elif a.what == "files":
        res = []
        for slug, _, rows in each_dump():
            f = [r for r in rows if r.get("file_name")]
            tot = sum(r.get("file_mb") or 0 for r in f)
            if tot:
                mins, _ = frequency(rows)
                res.append((tot, len(f), tot / len(f), tot / (mins * 60) if mins else 0, slug))
        res.sort(reverse=True)
        print(f"{'GB':>9s}{'文件':>7s}{'单文件MB':>10s}{'上传率MB/s':>12s}  样本")
        for tot, n, mean, rate, slug in res[:a.top]:
            print(f"{tot/1024:9.2f}{n:7d}{mean:10.1f}{rate:12.4f}  {slug}")
    elif a.what == "domains":
        tot = collections.Counter()
        for slug, _, rows in each_dump():
            for r in rows:
                for u in r.get("urls") or []:
                    m = re.match(r"https?://([^/]+)", u)
                    if m and m.group(1) != "t.me":
                        tot[m.group(1)] += 1
        for h, n in tot.most_common(a.top):
            print(f"   {n:7,}  {h}")


def cmd_rank(a):
    rows = []
    for p in REPORT.glob("*.json"):
        if p.name == "index.json":
            continue
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        m = d.get("metrics") or {}
        key = {"err": "err_pct", "reach": "daily_reach", "subs": "subscribers",
               "files": "files_mb", "survival": "survival_pct"}[a.metric]
        v = m.get(key) or (m.get("members") if a.metric == "subs" else None)
        if v:
            rows.append((v, d["slug"], m.get("posts_per_day_real") or m.get("posts_per_day"),
                         d.get("risk", {}).get("level")))
    rows.sort(reverse=not a.asc)
    print(f"{a.metric} 排名（共 {len(rows)} 个有该字段的样本）")
    for v, slug, f, risk in rows[:a.top]:
        print(f"   {v:>12,.2f}  {slug:28s} {f} 帖/天  risk={risk}")


def cmd_audit(a):
    print("== 频率口径：按天取整 vs 真实跨度")
    bad = []
    for slug, _, rows in each_dump():
        mins, real = frequency(rows)
        naive = len(rows) / max(1, round(mins / 1440))
        if mins and (real / naive > 1.5 or real / naive < 0.67):
            bad.append((real / naive, slug, mins, real, naive))
    bad.sort(reverse=True)
    if bad:
        for ratio, slug, mins, real, naive in bad:
            flag = "  ⚠ 跨度<1小时，必须按分钟" if mins < 60 else ""
            print(f"   {slug:26s} 跨度{mins:9,.0f}分  真实{real:10,.1f}  取整{naive:8,.1f}  "
                  f"偏差{ratio:5.2f}×{flag}")
    else:
        print("   无显著偏差（全部在 1.5 倍以内）")

    print("\n== ERR 成熟度：成熟帖占比 <10% 的样本")
    found = False
    for slug, meta, rows in each_dump():
        e = err_of(meta, rows)
        if e.get("mature_ratio") is not None and e["mature_ratio"] < 0.10:
            found = True
            rec = None
            p = REPORT / f"{slug}.json"
            if p.exists():
                rec = (json.loads(p.read_text(encoding="utf-8")).get("metrics") or {}).get("err_pct")
            print(f"   {slug:26s} 成熟{e['n_views'] and int(e['mature_ratio']*e['n_views'])}/{e['n_views']} "
                  f"({e['mature_ratio']*100:4.1f}%)  本脚本给出 {e['value']:.2f}% ({e['basis']})"
                  + (f"  报告记录 {rec}%" if rec else "  （尚无报告）"))
    if not found:
        print("   无")


def cmd_maturity(a):
    """测内容的阅读半衰期：ERR 随帖龄怎么变，什么时候见顶。

    本语料默认用帖龄 >=14 天算 ERR，那是按「资讯/资源类内容要几天才读完」定的。
    但短视频类内容往往 6 小时就见顶，拿 14 天做门槛会把样本砍到只剩几条（有偏）。
    正确的做法是先测这条曲线，再决定门槛。
    """
    meta, rows = load(a.slug)
    subs = meta.get("subscribers")
    have = [r for r in rows if r.get("views")]
    if not subs or not have:
        sys.exit("没有订阅数或没有阅读数，测不了")
    now = max(when(r) for r in rows)
    hours = [1, 3, 6, 12, 24, 48, 96, 168, 336]
    print(f"{a.slug}  subs={subs:,}  有阅读数的帖={len(have)}")
    print(f"{'帖龄≥':>8s}{'n':>7s}{'中位阅读':>10s}{'ERR':>9s}{'相对上一档':>12s}")
    prev = None
    plateau = None
    for h in hours:
        v = [r["views"] for r in have if (now - when(r)).total_seconds() / 3600 >= h]
        if len(v) < 10:
            print(f"{h:>7d}h{len(v):>7d}        —        —   （样本不足，窗口没覆盖到）")
            continue
        mv = statistics.median(v)
        err = mv / subs * 100
        delta = f"{(err/prev-1)*100:+7.1f}%" if prev else "      —"
        print(f"{h:>7d}h{len(v):>7d}{mv:>10,.0f}{err:>8.2f}%{delta:>12s}")
        if prev and abs(err / prev - 1) < 0.05 and plateau is None:
            plateau = h
        prev = err
    if plateau:
        print(f"\n→ 阅读量在帖龄约 {plateau} 小时处见顶（相邻档变化 <5%）。")
        print(f"  这个品类用 >= {plateau}h 做成熟门槛就够了；套用 14 天会白白砍掉大部分样本。")
    else:
        print("\n→ 曲线还在上升，样本窗口不足以看到见顶点；当前 ERR 是下界。")


CORPUS_SLUGS = {p.stem for p in REPORT.glob("*.json") if p.name != "index.json"}


def cmd_verify(a):
    slugs = a.slugs or [p.stem for p in REPORT.glob("*.json") if p.name != "index.json"]
    bad: list[str] = []
    note: list[str] = []
    for s in slugs:
        md, js = REPORT / f"{s}.md", REPORT / f"{s}.json"
        if not md.exists() or not js.exists():
            bad.append(f"{s}: md 或 json 缺失"); continue
        if b"\r" in md.read_bytes() or b"\r" in js.read_bytes():
            bad.append(f"{s}: 含 CRLF")
        try:
            d = json.loads(js.read_text(encoding="utf-8"))
        except Exception as e:
            bad.append(f"{s}: JSON 不合法 {e}"); continue
        miss = [k for k in REQUIRED_KEYS if k not in d]
        if miss:
            bad.append(f"{s}: 缺字段 {miss}")
        if d.get("depth") != "deep":
            bad.append(f"{s}: depth={d.get('depth')}")
        if d.get("risk", {}).get("level") not in RISK_LEVELS:
            bad.append(f"{s}: risk.level={d.get('risk', {}).get('level')}")
        txt = md.read_text(encoding="utf-8")
        risk = d.get("risk", {}).get("level")
        strict = risk in ("high", "criminal", "critical")
        # 篇幅只是质量启发，不是正确性或安全问题 —— 永远只提示，不阻断。
        if len(txt) < 3000:
            note.append(f"{s}: md 只有 {len(txt)} 字（早期样本，深度标准提高前写的）")
        # 内容纪律只对高风险样本硬性执行。低风险样本里的 @handle 与站外域名
        # 多半是该账号自己的公开身份或 github / 官网一类，不在纪律覆盖范围内。
        known = {c.lower() for c in CORPUS_SLUGS} | {s.lower()}
        for pat, label in DISCIPLINE:
            hit = sorted({m.group(0) for m in re.finditer(pat, txt)})
            if label == "@账号":
                # 自指、以及语料里已单独收录过的公开账号，不算泄露
                hit = [h for h in hit if h[1:].lower() not in known]
            hit = [h for h in hit if not re.search(r"(XXXX|xxxx|····|\.{3,})", h)]
            if hit:
                (bad if strict else note).append(
                    f"{s}: 内容纪律 [{label}] -> {hit[:4]}"
                    + ("" if strict else f"（risk={risk}，仅提示）"))
        dump = (d.get("source") or {}).get("dump")
        f = RECON / f"{s}.jsonl"
        if dump is None and f.exists():
            bad.append(f"{s}: source.dump=null 但 {f.name} 仍在磁盘上")
        if dump and not f.exists():
            bad.append(f"{s}: source.dump 指向不存在的文件")
    idx = REPORT / "index.json"
    if idx.exists():
        ch = json.loads(idx.read_text(encoding="utf-8"))["channels"]
        have = {c["slug"] for c in ch}
        missing = [s for s in slugs if s not in have]
        if missing:
            bad.append(f"index.json 里缺: {missing}")
        nd = [c["slug"] for c in ch if c.get("depth") != "deep"]
        if nd:
            bad.append(f"index.json 里非 deep: {nd}")
        print(f"校验 {len(slugs)} 篇；index 共 {len(ch)} 条")
    if note and not a.quiet:
        print(f"提示（{len(note)} 条，低风险样本，不阻断）：")
        for n in note[:a.max_notes]:
            print("  ·", n)
        if len(note) > a.max_notes:
            print(f"  ·（另有 {len(note)-a.max_notes} 条，--max-notes 调整）")
    if bad:
        print("发现问题：")
        for b in bad:
            print("  -", b)
        sys.exit(1)
    print("全部通过 —— md+json 齐全、JSON 合法、必需键完整、depth=deep、纯 LF、"
          "dump 一致、内容纪律扫描通过")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("brief", help="单个样本的完整画像")
    p.add_argument("slug"); p.set_defaults(fn=cmd_brief)

    p = sub.add_parser("gaps", help="id 缺口的形状")
    p.add_argument("slugs", nargs="+"); p.set_defaults(fn=cmd_gaps)

    p = sub.add_parser("batches", help="发布批次（秒级聚合）")
    p.add_argument("slugs", nargs="+")
    p.add_argument("--cut", type=int, default=60, help="批间隔阈值（秒），默认 60")
    p.set_defaults(fn=cmd_batches)

    p = sub.add_parser("words", help="按预设词表统计词频")
    p.add_argument("slug")
    p.add_argument("--preset", choices=sorted(WORD_PRESETS), help="不给就全跑一遍")
    p.add_argument("--extra", nargs="*", default=[], help="额外的自定义词")
    p.set_defaults(fn=cmd_words)

    p = sub.add_parser("tme", help="t.me 引用按路径前缀分类")
    p.add_argument("slug"); p.set_defaults(fn=cmd_tme)

    p = sub.add_parser("scan", help="全语料扫描")
    p.add_argument("what", choices=["tme", "reactions", "files", "domains"])
    p.add_argument("--top", type=int, default=15)
    p.set_defaults(fn=cmd_scan)

    p = sub.add_parser("rank", help="全语料指标排名（读 report/*.json）")
    p.add_argument("metric", choices=["err", "reach", "subs", "files", "survival"])
    p.add_argument("--top", type=int, default=15)
    p.add_argument("--asc", action="store_true", help="改成升序")
    p.set_defaults(fn=cmd_rank)

    p = sub.add_parser("maturity", help="阅读半衰期：ERR 随帖龄的曲线，定成熟门槛")
    p.add_argument("slug"); p.set_defaults(fn=cmd_maturity)

    p = sub.add_parser("audit", help="口径核查：频率取整与 ERR 成熟度")
    p.set_defaults(fn=cmd_audit)

    p = sub.add_parser("verify", help="报告校验；不给 slug 就全查")
    p.add_argument("slugs", nargs="*")
    p.add_argument("--quiet", action="store_true", help="只报硬性问题，不打印低风险提示")
    p.add_argument("--max-notes", type=int, default=10)
    p.set_defaults(fn=cmd_verify)

    a = ap.parse_args()
    a.fn(a)


if __name__ == "__main__":
    main()
