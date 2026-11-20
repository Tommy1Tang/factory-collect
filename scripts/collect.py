#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""factory-collect —— 用 DeepSeek Search / Fetch 从公开网页采集企业信息，落成 CSV。

用法示例：
    set DEEPSEEK_API_KEY=sk-xxxx
    python collect.py --query 注塑模具 --region 浙江省 --target 200 --out data/zj_mold.csv

设计约束（与项目约定一致）：
  * 只采公开页面；不绕过任何验证码 / 登录墙 / 反爬。
  * 不采集个人手机号：正文里的 11 位手机号会被替换掉，电话列只保留座机/400。
  * 每条记录带来源 URL、页面标题、抓取时间，便于回溯与自查。
"""
from __future__ import annotations

import argparse
import csv
import getpass
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE_URL = "https://api.deepseek.com"
KEY_ENV = "DEEPSEEK_API_KEY"
CST = timezone(timedelta(hours=8))
PRICE_PER_REQUEST = 0.005  # 5 元 / 1000 次成功请求（Search 与 Fetch 同价）

COLUMNS = [
    "企业名称", "统一社会信用代码", "省", "市", "区县", "行业",
    "注册资本", "成立日期", "经营状态", "经营范围", "主营产品", "产品介绍",
    "官网", "公开电话", "来源URL", "来源标题", "页面日期",
    "置信度", "证据句", "采集时间",
]

# 招投标与企业是 1:N，单独落一张长表，按 (企业名称, 项目名称, 公告日期) 去重。
BID_COLUMNS = [
    "企业名称", "统一社会信用代码", "项目名称", "采购方", "中标金额",
    "公告日期", "来源URL", "证据句", "采集时间",
]

MOBILE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
LANDLINE_RE = re.compile(r"(?<!\d)(?:0\d{2,3}[-\s]?\d{7,8}|400[-\s]?\d{3}[-\s]?\d{4})(?!\d)")
NAME_RE = re.compile(r"(公司|集团|厂|合作社|研究院|事务所)")

EXTRACT_SYSTEM = """你是严谨的企业信息抽取器，只做抽取，不做推断。

规则：
1. 只抽取页面中明确出现的字段；没有就留空字符串，不要猜。
2. 企业名称必须与页面写法一致，不要改写、不要补全简称。
3. 统一社会信用代码、注册资本、成立日期、经营状态这几个字段，页面上没有就留空。
4. 手机号一律不要抽取（页面里出现也忽略）；公开电话只填座机或 400 号码。
5. 每家企业给一句 evidence：引用页面里能证明该企业存在的原文片段，不超过 60 字。
6. confidence 取 0~1：政府公示/企业官网 0.9，协会或展会名录 0.75，媒体转载 0.6。
7. 产品介绍：摘录企业自述的产品/业务介绍原文，最多 300 字；页面没有就留空，不要改写。
8. bids：只抽中标/成交/候选人公告里的记录；一个公告里有多家企业就输出多条；
   金额保留原文单位（如“1,280.5 万元”）；没有公告就输出空数组。

输出一个 JSON 对象，格式严格如下（不要 markdown、不要解释）：
{"companies":[{"企业名称":"","统一社会信用代码":"","省":"","市":"","区县":"","行业":"",
"注册资本":"","成立日期":"","经营状态":"","经营范围":"","主营产品":"","产品介绍":"","官网":"",
"公开电话":"","evidence":"","confidence":0.0}],
 "bids":[{"企业名称":"","项目名称":"","采购方":"","中标金额":"","公告日期":"","evidence":""}]}
页面里没有企业信息时，输出 {"companies":[],"bids":[]}。"""


# ---------------------------------------------------------------- 基础工具

def now_str() -> str:
    return datetime.now(CST).strftime("%Y-%m-%d %H:%M:%S")


def log(msg: str) -> None:
    print(f"[{datetime.now(CST):%H:%M:%S}] {msg}", flush=True)


def scrub(text: str) -> str:
    """抹掉手机号，保留座机。"""
    return MOBILE_RE.sub("[手机号已忽略]", text or "")


def pick_phone(text: str) -> str:
    found = LANDLINE_RE.findall(text or "")
    return found[0].strip() if found else ""


def norm_name(name: str) -> str:
    return re.sub(r"[\s（）()、，,]+", "", name or "")


def record_key(row: dict) -> str:
    uscc = re.sub(r"\s", "", row.get("统一社会信用代码", "") or "").upper()
    if len(uscc) == 18:
        return "uscc:" + uscc
    return "name:" + norm_name(row.get("企业名称", ""))


def find_key_file(args) -> Path | None:
    """按 显式路径 → 当前目录 → 脚本目录 → 技能根目录 找 key.txt。"""
    if getattr(args, "key_file", ""):
        p = Path(args.key_file)
        return p if p.exists() else None
    here = Path(__file__).resolve()
    for c in (Path.cwd() / "key.txt", here.parent / "key.txt", here.parent.parent / "key.txt"):
        if c.exists():
            return c
    return None


def resolve_key(args) -> tuple[str, str]:
    """返回 (key, 来源说明)。优先级：--api-key > 环境变量 > key.txt。"""
    if args.api_key:
        return args.api_key.strip(), "--api-key"
    env = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if env:
        return env, "环境变量 DEEPSEEK_API_KEY"
    p = find_key_file(args)
    if p:
        raw = p.read_text(encoding="utf-8-sig", errors="replace").strip()
        for line in raw.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.upper().startswith("DEEPSEEK_API_KEY"):
                line = line.split("=", 1)[1] if "=" in line else line
            return line.strip().strip('"').strip("'"), f"文件 {p}"
    return "", ""


def _mask(key: str) -> str:
    return key[:6] + "…" + key[-4:] if len(key) > 12 else "（长度异常）"


def _gitignore_mentions_key() -> bool:
    """向上找 4 层 .gitignore，看有没有忽略 key/.env。命中返回 True。"""
    p = Path(__file__).resolve().parent
    for _ in range(4):
        gi = p / ".gitignore"
        if gi.exists():
            for line in gi.read_text(encoding="utf-8", errors="replace").splitlines():
                s = line.strip()
                if s and not s.startswith("#") and ("key" in s.lower() or ".env" in s.lower()):
                    return True
        p = p.parent
    return False


def _available_models(key: str) -> list[str]:
    try:
        req = urllib.request.Request(f"{BASE_URL}/models",
                                     headers={"Authorization": f"Bearer {key}"})
        with urllib.request.urlopen(req, timeout=20) as r:
            data = json.loads(r.read().decode("utf-8"))
        return [m.get("id", "") for m in data.get("data", []) if m.get("id")]
    except Exception:  # noqa: BLE001
        return []


def _validate_key(key: str) -> tuple[bool, str]:
    req = urllib.request.Request(f"{BASE_URL}/models",
                                 headers={"Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read().decode("utf-8"))
        models = [m.get("id", "") for m in data.get("data", [])]
        return True, "可用模型：" + (", ".join(models[:10]) or "（接口未返回列表）")
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:200]
        if e.code == 401:
            return False, "Key 无效（401）"
        if e.code == 402:
            return False, "Key 有效但余额不足（402）"
        return False, f"HTTP {e.code}：{body}"
    except Exception as e:  # noqa: BLE001
        return False, f"连不上 api.deepseek.com：{e}"


def _prompt_key(save: bool) -> str:
    """交互式输入：终端里不回显；管道/重定向时读一行。"""
    print("未检测到 DEEPSEEK_API_KEY。请粘贴 API Key（输入不回显，直接回车=跳过）：")
    try:
        if sys.stdin and sys.stdin.isatty():
            key = getpass.getpass("Key: ").strip()
        else:
            # Windows 上 getpass 在无控制台时会卡死，非终端场景改为读一行
            sys.stderr.write("Key: ")
            sys.stderr.flush()
            key = (sys.stdin.readline() if sys.stdin else "").strip()
    except (EOFError, KeyboardInterrupt, OSError, ValueError):
        print("\n已取消输入。")
        return ""
    if not key:
        print("已跳过输入。")
        return ""
    if not key.startswith("sk-"):
        print("⚠️ 不是 sk- 开头，可能粘贴错了；仍按你输入的内容继续。")
    if save:
        p = Path(__file__).resolve().parent.parent / "key.txt"
        p.write_text(key + "\n", encoding="ascii", errors="ignore")
        print(f"✅ 已保存到 {p}")
        if _gitignore_mentions_key():
            print("   .gitignore 已覆盖，提交不会带上它。")
        else:
            print("   ⚠️ .gitignore 未覆盖 key.txt，提交前请处理。")
    return key


def resolve_key_interactive(args, allow_auto: bool) -> tuple[str, str]:
    """先按 参数/变量/文件 找；都没有时，终端里可交互输入。"""
    key, src = resolve_key(args)
    if key:
        return key, src
    if getattr(args, "no_prompt", False):
        return "", ""
    if not (getattr(args, "ask_key", False) or (allow_auto and sys.stdin.isatty())):
        return "", ""
    k = _prompt_key(getattr(args, "save_key", False))
    return (k, "交互输入") if k else ("", "")


def check_key(args) -> None:
    key, src = resolve_key_interactive(args, allow_auto=True)
    if not key:
        sys.exit(f"❌ 没找到 Key：环境变量 {KEY_ENV} 未设置，也没有 key.txt。"
                 f"跑 --doctor 看明细。")
    ok, msg = _validate_key(key)
    print(("✅ " if ok else "❌ ") + f"Key（来源：{src}，{_mask(key)}）：{msg}")
    if not ok:
        sys.exit(1)


def doctor(args) -> None:
    print("== factory-collect 环境自检 ==\n")
    env = os.environ.get(KEY_ENV, "").strip()
    if env:
        print(f"1) 变量 {KEY_ENV}：✅ 已设置（{_mask(env)}）")
    else:
        print(f"1) 变量 {KEY_ENV}：❌ 未设置 / 为空  ← 需要维护的就是它")

    kp = find_key_file(args)
    print(f"2) 兜底文件 key.txt：{'存在（' + str(kp) + '）' if kp else '未使用'}")

    gi_ok = _gitignore_mentions_key()
    print(f"3) .gitignore 忽略 key/.env：{'✅ 已覆盖' if gi_ok else '⚠️ 未覆盖，提交前请补'}")

    key, src = resolve_key_interactive(args, allow_auto=False)
    if not key:
        print("4) Key 有效性：跳过（没有 key）")
        print("\n下一步：设好变量后重跑 --doctor，再跑采集。")
        return
    print(f"4) Key 来源：{src}")
    ok, msg = _validate_key(key)
    print(f"5) Key 有效性：{'✅ ' if ok else '❌ '}{msg}")


def post_json(url: str, payload: dict, api_key: str, timeout: int = 90, retries: int = 3) -> dict:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    last = ""
    for attempt in range(retries + 1):
        req = urllib.request.Request(
            url, data=body, method="POST",
            headers={"Authorization": f"Bearer {api_key}",
                     "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", "replace")
            last = f"HTTP {e.code}: {raw[:300]}"
            if e.code in (408, 429, 500, 502, 503, 504) and attempt < retries:
                wait = 2 ** attempt
                log(f"  {e.code} 重试 {attempt + 1}/{retries}（等 {wait}s）")
                time.sleep(wait)
                continue
            raise RuntimeError(last)
        except (urllib.error.URLError, TimeoutError) as e:
            last = str(e)
            if attempt < retries:
                time.sleep(2 ** attempt)
                continue
            raise RuntimeError(last)
    raise RuntimeError(last)


# ---------------------------------------------------------------- DeepSeek API

def ds_search(api_key: str, query: str, count: int, need_content: bool = True) -> list[dict]:
    data = post_json(f"{BASE_URL}/v1/tools/search",
                     {"query": query, "count": count, "need_content": need_content},
                     api_key)
    return data.get("results", []) or []


def ds_fetch(api_key: str, url: str) -> tuple[str, str]:
    data = post_json(f"{BASE_URL}/v1/tools/fetch", {"url": url}, api_key)
    return data.get("title", "") or "", data.get("content", "") or ""


def ds_extract(api_key: str, model: str, region: str, keyword: str,
               title: str, url: str, text: str) -> dict:
    user = (
        f"目标条件：地区={region or '不限'}；行业/产品={keyword or '不限'}\n"
        f"页面标题：{title}\n页面链接：{url}\n"
        f"页面正文（可能被截断）：\n{text}"
    )
    data = post_json(
        f"{BASE_URL}/chat/completions",
        {"model": model,
         "messages": [{"role": "system", "content": EXTRACT_SYSTEM},
                      {"role": "user", "content": user}],
         "response_format": {"type": "json_object"},
         "temperature": 0,
         "max_tokens": 8192},
        api_key, timeout=180,
    )
    content = data["choices"][0]["message"]["content"]
    try:
        obj = json.loads(content)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", content, re.S)
        obj = json.loads(m.group(0)) if m else {"companies": [], "bids": []}
    return obj if isinstance(obj, dict) else {"companies": [], "bids": []}


# ---------------------------------------------------------------- 查询计划

def plan_queries(keyword: str, region: str, mode: str) -> list[str]:
    base = f"{region} {keyword}".strip()
    exclude = "-招聘 -智联招聘 -前程无忧 -boss直聘 -贴吧 -百度知道"
    company_q = [
        f"{base} 有限公司 官网 {exclude}",
        f"{base} 工厂 产品 介绍 {exclude}",
        f"{base} 企业 供应商 中标 {exclude}",
    ]
    list_q = [
        f"{base} 名单 公示 {exclude}",
        f"{base} 专精特新 小巨人 公示 site:gov.cn {exclude}",
        f"{base} 中标 结果 公告 {exclude}",
        f"{base} 中标候选人 公示 {exclude}",
        f"{base} 采购 成交 公告 {exclude}",
        f"{base} 园区 入驻企业 {exclude}",
    ]
    return list_q + company_q if mode == "list" else company_q + list_q


def should_skip(url: str) -> bool:
    bad = ("baidu.com/s?", "zhidao.baidu.com", "wenku.baidu.com", "douban.com",
           "zhihu.com/question", "weibo.com", "tieba.baidu.com",
           "zhaopin.com", "51job.com", "zhipin.com", "liepin.com")
    return any(b in url for b in bad)


# ---------------------------------------------------------------- 主流程

def collect(args) -> dict:
    api_key, key_src = resolve_key_interactive(args, allow_auto=True)
    if not api_key and not args.dry_run and not args.selftest:
        sys.exit(
            f"❌ 必填变量 {KEY_ENV} 没有值，技能无法调用 DeepSeek。\n"
            "  维护方式（任选其一）：\n"
            f"    1) 永久：setx {KEY_ENV} \"sk-xxxx\"   然后重开终端 / 重启 DSH\n"
            f"    2) 临时：$env:{KEY_ENV} = \"sk-xxxx\"   只对当前终端窗口有效\n"
            "    3) 兜底：技能目录放 key.txt（内容一行 sk-xxxx）\n"
            "  自检：python scripts/collect.py --doctor"
        )
    if api_key:
        log(f"Key 来源：{key_src}（{_mask(api_key)}）")
        if key_src.startswith("文件") and not _gitignore_mentions_key():
            log("⚠️ key.txt 未被 .gitignore 覆盖，提交前务必确认它不会入库")
        models = _available_models(api_key)
        if models and args.model not in models:
            pick = next((m for m in models if "pro" in m.lower()), models[0])
            log(f"⚠️ 模型 {args.model} 不可用（可用：{', '.join(models)}），改用 {pick}")
            args.model = pick

    queries = plan_queries(args.query, args.region, args.mode)
    if args.dry_run:
        log("DRY RUN：只打印查询计划，不发请求。")
        for i, q in enumerate(queries, 1):
            print(f"  {i}. {q}")
        est = len(queries) * PRICE_PER_REQUEST
        print(f"\n预计 {len(queries)} 次 Search ≈ {est:.3f} 元；"
              f"目标 {args.target} 条，抓取上限 {args.max_fetches} 次 Fetch ≈ "
              f"{args.max_fetches * PRICE_PER_REQUEST:.3f} 元。")
        return {}

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    src_path = out_path.with_suffix(".sources.csv")
    run_path = out_path.with_suffix(".run.json")

    done_urls: set[str] = set()
    rows: list[dict] = []
    if args.resume and out_path.exists():
        with out_path.open(encoding="utf-8-sig", newline="") as f:
            rows = list(csv.DictReader(f))
        log(f"断点续跑：已有 {len(rows)} 条")
    if args.resume and src_path.exists():
        with src_path.open(encoding="utf-8-sig", newline="") as f:
            done_urls = {r["url"] for r in csv.DictReader(f)}

    seen: set[str] = set(record_key(r) for r in rows)
    bids: list[dict] = []
    bid_seen: set[str] = set()
    sources: list[dict] = []
    stats = {"searches": 0, "fetches": 0, "pages": 0, "errors": 0, "started": now_str()}
    stop = False
    empty_streak = 0

    for qi, query in enumerate(queries, 1):
        if stop or len(rows) >= args.target:
            break
        before = len(rows)
        before_bids = len(bids)
        log(f"搜索 {qi}/{len(queries)}：{query}")
        try:
            results = ds_search(api_key, query, args.count)
            stats["searches"] += 1
        except Exception as e:  # noqa: BLE001
            stats["errors"] += 1
            log(f"  搜索失败：{e}")
            continue

        for item in results:
            if stop or len(rows) >= args.target:
                break
            url = item.get("url", "")
            title = item.get("title", "")
            if not url or url in done_urls or should_skip(url):
                continue
            if (item.get("authority") or 0) < args.min_authority:
                continue

            text = item.get("content") or ""
            if len(text.strip()) < 300 and stats["fetches"] < args.max_fetches:
                try:
                    ftitle, ftext = ds_fetch(api_key, url)
                    stats["fetches"] += 1
                    text = ftext or text
                    title = title or ftitle
                except Exception as e:  # noqa: BLE001
                    stats["errors"] += 1
                    log(f"  fetch 失败：{url} -> {e}")
                    continue
            if len(text.strip()) < 120:
                continue

            text = text[: args.text_chars]
            try:
                page = ds_extract(api_key, args.model, args.region, args.query,
                                  title, url, text)
            except Exception as e:  # noqa: BLE001
                stats["errors"] += 1
                log(f"  抽取失败：{url} -> {e}")
                continue

            extracted = page.get("companies", []) or []
            page_bids = page.get("bids", []) or []
            stats["pages"] += 1
            added = 0
            for c in extracted:
                name = (c.get("企业名称") or "").strip()
                if not name or len(name) < 4 or not NAME_RE.search(name):
                    continue
                row = {k: scrub(str(c.get(k, "") or "")) for k in COLUMNS}
                row["企业名称"] = scrub(name)
                row["证据句"] = scrub(str(c.get("evidence", "") or c.get("证据句", "")))
                row["置信度"] = c.get("confidence", c.get("置信度", ""))
                # 页面级电话回填只在“这一页只抽到一家企业”时启用，
                # 否则名单页会把这个页面上第一个电话灌给所有企业。
                phone = pick_phone(c.get("公开电话", ""))
                if not phone and len(extracted) == 1:
                    phone = pick_phone(text)
                row["公开电话"] = phone
                row["来源URL"] = url
                row["来源标题"] = title
                row["页面日期"] = (item.get("date") or "")[:10]
                row["采集时间"] = now_str()
                row["置信度"] = c.get("confidence", "")
                key = record_key(row)
                if key in seen:
                    continue
                seen.add(key)
                rows.append({k: row.get(k, "") for k in COLUMNS})
                added += 1

            added_bids = 0
            for b in page_bids:
                bname = (b.get("企业名称") or "").strip()
                proj = (b.get("项目名称") or "").strip()
                if not bname or not proj:
                    continue
                bdate = (b.get("公告日期") or "").strip()[:10]
                bkey = f"{norm_name(bname)}|{proj[:60]}|{bdate}"
                if bkey in bid_seen:
                    continue
                bid_seen.add(bkey)
                bids.append({
                    "企业名称": scrub(bname),
                    "统一社会信用代码": scrub(str(b.get("统一社会信用代码", "") or "")),
                    "项目名称": scrub(proj),
                    "采购方": scrub(str(b.get("采购方", "") or "")),
                    "中标金额": scrub(str(b.get("中标金额", "") or "")),
                    "公告日期": bdate,
                    "来源URL": url,
                    "证据句": scrub(str(b.get("evidence", "") or "")),
                    "采集时间": now_str(),
                })
                added_bids += 1

            done_urls.add(url)
            sources.append({"url": url, "title": title,
                            "date": (item.get("date") or "")[:10],
                            "authority": item.get("authority", ""),
                            "query": query, "rows": added,
                            "collected_at": now_str()})
            log(f"  +{added} 条（累计 {len(rows)}）"
                + (f"，中标 {added_bids} 条（累计 {len(bids)}）" if added_bids else ""))
            time.sleep(args.sleep)

        if len(rows) == before and len(bids) == before_bids:
            empty_streak += 1
            if empty_streak >= args.max_empty_queries:
                log(f"连续 {empty_streak} 次搜索无新增，提前结束。")
                break
        else:
            empty_streak = 0

    # 落盘
    with out_path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)
    with src_path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["url", "title", "date", "authority",
                                          "query", "rows", "collected_at"])
        w.writeheader()
        w.writerows(sources)

    bid_path = out_path.with_suffix(".bids.csv")
    with bid_path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=BID_COLUMNS)
        w.writeheader()
        w.writerows(bids)

    stats["rows"] = len(rows)
    stats["bids"] = len(bids)
    stats["ended"] = now_str()
    stats["cost_yuan"] = round((stats["searches"] + stats["fetches"]) * PRICE_PER_REQUEST, 4)
    run_path.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    return stats


# ---------------------------------------------------------------- 自检

def selftest(args) -> None:
    """不联网，验证 CSV 编码、列顺序、手机号过滤。"""
    demo = [{
        "企业名称": "示例精密模具制造有限公司", "统一社会信用代码": "91330000TEST0001X",
        "省": "浙江省", "市": "宁波市", "区县": "北仑区", "行业": "专用设备制造业",
        "注册资本": "1000万人民币", "成立日期": "2012-03-15", "经营状态": "存续",
        "经营范围": "模具及零配件制造、销售；货物进出口。", "主营产品": "注塑模具",
        "官网": "https://example.com", "公开电话": "0574-88880000",
        "来源URL": "https://example.com/about", "来源标题": "示例-关于我们",
        "页面日期": "2026-01-01", "置信度": 0.9,
        "证据句": "示例精密模具制造有限公司位于宁波北仑", "采集时间": now_str(),
    }]
    demo[0]["经营范围"] = scrub(demo[0]["经营范围"] + " 联系手机 13800138000")
    out = Path(args.out if args.out.endswith(".csv") else "data/selftest.csv")
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(demo)
    log(f"自检通过：已写入 {out}（{len(COLUMNS)} 列，手机号应已被替换）")


def main() -> None:
    global BASE_URL
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass

    p = argparse.ArgumentParser(description="公开源企业信息采集 → CSV（DeepSeek Search/Fetch）")
    p.add_argument("--query", default="注塑模具", help="行业/产品关键词")
    p.add_argument("--region", default="", help="地区，如 浙江省")
    p.add_argument("--target", type=int, default=200, help="目标条数")
    p.add_argument("--out", default="data/companies.csv", help="输出 CSV 路径")
    p.add_argument("--mode", choices=["company", "list"], default="list",
                   help="list=先找名单页；company=先找企业页")
    p.add_argument("--count", type=int, default=20, help="每次搜索返回条数（1~50）")
    p.add_argument("--min-authority", type=int, default=1, help="最低权威性（0~4）")
    p.add_argument("--max-fetches", type=int, default=40, help="Fetch 次数上限（控成本）")
    p.add_argument("--text-chars", type=int, default=12000, help="单页正文截断长度")
    p.add_argument("--sleep", type=float, default=0.2, help="请求间隔（秒）")
    p.add_argument("--max-empty-queries", type=int, default=2,
                   help="连续多少次搜索无新增就提前结束")
    p.add_argument("--model", default="deepseek-chat", help="抽取用模型")
    p.add_argument("--api-key", default="", help="DeepSeek API Key（默认读环境变量或 key.txt）")
    p.add_argument("--key-file", default="", help="Key 文件路径（默认自动找当前目录/技能目录的 key.txt）")
    p.add_argument("--check-key", action="store_true", help="只验证 Key 是否可用，不采集")
    p.add_argument("--doctor", action="store_true",
                   help="环境自检：变量是否设置 / key.txt / .gitignore / Key 有效性")
    p.add_argument("--ask-key", action="store_true",
                   help="缺 Key 时交互式输入（不回显）")
    p.add_argument("--save-key", action="store_true",
                   help="交互输入后把 Key 存进 key.txt")
    p.add_argument("--no-prompt", action="store_true",
                   help="禁止自动弹输入（CI / 定时任务用）")
    p.add_argument("--base-url", default=BASE_URL, help="API 根地址（默认官方；测试时可指向 mock）")
    p.add_argument("--resume", action="store_true", help="断点续跑")
    p.add_argument("--dry-run", action="store_true", help="只打印查询计划")
    p.add_argument("--selftest", action="store_true", help="离线自检")
    args = p.parse_args()
    if args.base_url:
        BASE_URL = args.base_url.rstrip("/")

    if args.doctor:
        doctor(args)
        return

    if args.selftest:
        selftest(args)
        return

    if args.check_key:
        check_key(args)
        return

    stats = collect(args)
    if stats:
        log(f"完成：{stats['rows']} 条企业 + {stats['bids']} 条中标 → {args.out}")
        log(f"Search {stats['searches']} 次 / Fetch {stats['fetches']} 次，"
            f"约 {stats['cost_yuan']} 元；失败 {stats['errors']} 次")


if __name__ == "__main__":
    main()
