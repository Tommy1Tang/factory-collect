---
name: factory-collect
description: 用 DeepSeek Search / Fetch 从公开网页（政府公示、协会名录、展会、企业官网、中标公告）采集企业/工厂信息，抽取成结构化 CSV（UTF-8-SIG，Excel 直开），附来源表便于回溯与 AI 分析。当用户要"按行业+地区找工厂/找客户/找供应商""把某个名单/公示/中标公告扒成表格""批量采集企业信息做分析"时使用。默认丢弃个人手机号，只保留座机/400；不绕过任何验证码或登录墙。
---

# factory-collect —— 公开源企业采集

把"某行业 × 某地区"的公开企业信息，从网页变成一张可分析的 CSV。

## 它怎么工作

1. **拼查询计划**：把 `地区 + 行业` 组合成 6~8 条搜索词（名单页、专精特新公示、中标公告、企业官网……），自动排除招聘站和问答站。
2. **Search**：`POST /v1/tools/search`，一次拿回 20~50 条结果，`need_content=true` 直接带正文（比逐页 Fetch 便宜得多）。
3. **Fetch**：只在正文不足 300 字时才补抓，受 `--max-fetches` 限制。
4. **抽取**：`deepseek-chat` JSON 模式，只抽页面**明写**的字段，缺就留空，禁止推断。
5. **去重落盘**：按统一社会信用代码（无则按规范化企业名）去重，输出 CSV + 来源表 + 运行日志。

## 必填变量：DEEPSEEK_API_KEY

技能每次启动都会解析这个变量。**没值会直接报错并告诉你怎么配**，不会静默失败：

**开工前先跑一次 `--doctor`**；变量为空时先提醒用户维护，不要绕过、不要悄悄换成其他数据源。

```
❌ 必填变量 DEEPSEEK_API_KEY 没有值，技能无法调用 DeepSeek。
  维护方式（任选其一）：
    1) 永久：setx DEEPSEEK_API_KEY "sk-xxxx"   然后重开终端 / 重启 DSH
    2) 临时：$env:DEEPSEEK_API_KEY = "sk-xxxx"   只对当前终端窗口有效
    3) 兜底：技能目录放 key.txt（内容一行 sk-xxxx）
  自检：python scripts/collect.py --doctor
```

| 方式 | 命令 / 位置 | 生效范围 |
|---|---|---|
| 永久变量（推荐） | `setx DEEPSEEK_API_KEY "sk-xxxx"` | 重开终端 + 重启 DSH 后一直有效 |
| 临时变量 | `$env:DEEPSEEK_API_KEY = "sk-xxxx"` | 当前终端窗口 |
| 兜底文件 | 技能根目录 `key.txt`，一行 `sk-xxxx` | 本机，文件在即生效 |

> 为什么保留 `key.txt`：DSH 里每条命令都是新进程，继承的是主进程环境。你在自己终端 `setx` 的变量，agent 那边看不到；文件落在磁盘上，随时能读。

### 自检

```powershell
python scripts/collect.py --doctor
```

五行输出：变量是否设置 / `key.txt` 是否存在 / `.gitignore` 是否覆盖 / Key 来源 / Key 是否有效。

### 交互式输入（可选）

```powershell
python scripts/collect.py --query 注塑模具 --ask-key --save-key
```

| 参数 | 作用 |
|---|---|
| `--ask-key` | 缺 Key 时提示输入（终端里不回显；管道/重定向时读一行） |
| `--save-key` | 输入后存进 `key.txt` |
| `--no-prompt` | 禁止自动提示，CI / agent 场景用 |

标准终端（TTY）里缺 Key 会自动询问；agent / CI 里不会，直接报错退出，避免卡住。

### 别把 Key 提交上去

技能根目录已有 `.gitignore`，忽略 `key.txt`、`.env`、`*.key`、`*.pem`、`__pycache__/`。
如果你用 `key.txt` 兜底、而 `.gitignore` 没覆盖，脚本启动时会多打一行 ⚠️ 警告。

Key 在 https://platform.deepseek.com/api_keys 创建。

## 快速开始

```powershell
# 先设 key（只设一次，当前终端有效）
$env:DEEPSEEK_API_KEY = "sk-xxxx"

# 看查询计划，不花钱
python collect.py --query 注塑模具 --region 浙江省 --dry-run

# 正式跑：目标 200 条
python collect.py --query 注塑模具 --region 浙江省 --target 200 --out data/zj_mold.csv

# 断点续跑
python collect.py --query 注塑模具 --region 浙江省 --target 200 --out data/zj_mold.csv --resume

# 不联网自检（验证编码/列/手机号过滤）
python collect.py --selftest
```

## 产出

| 文件 | 内容 |
|---|---|
| `data/xxx.csv` | 主表，20 列，UTF-8-SIG |
| `data/xxx.bids.csv` | 招投标长表：一家企业可有多条中标记录 |
| `data/xxx.sources.csv` | 每个来源页：url / 标题 / 日期 / 权威性 / 命中条数 |
| `data/xxx.run.json` | 查询次数、抓取次数、企业数、中标数、失败数、耗时、估算费用 |

主表列：

```
企业名称, 统一社会信用代码, 省, 市, 区县, 行业, 注册资本, 成立日期,
经营状态, 经营范围, 主营产品, 产品介绍, 官网, 公开电话, 来源URL,
来源标题, 页面日期, 置信度, 证据句, 采集时间
```

招投标表列：

```
企业名称, 统一社会信用代码, 项目名称, 采购方, 中标金额,
公告日期, 来源URL, 证据句, 采集时间
```

### 招投标与产品介绍怎么来的

- **产品介绍**：抽企业自述的产品/业务原文（≤300 字），来自官网"关于我们/产品中心"、1688 店铺、企业简介页；没有就留空，不做改写。
- **招投标**：来自中标/成交/候选人公告。同一个公告里有多家企业就出多行；按 `(企业名称, 项目名称, 公告日期)` 去重。查询计划里专门有 3 条公告类搜索词。
- 两者都只是**线索**：中标金额、项目名称可能被模型简化，重要项目回到 `sources.csv` 里的原始 URL 复核。

## 成本

Search / Fetch 都是 5 元 / 1000 次成功请求。

- 一次 Search 带正文 ≈ 0.005 元，可覆盖 20 条结果
- 200 条目标 ≈ 10~20 次 Search + 少量 Fetch ≈ **0.1 元上下**

## 两条模式

- `--mode list`（默认）：先找"名单/公示/中标公告"页，一页可出 5~30 家，快。
- `--mode company`：先找企业官网逐家抽，字段更全，但慢、条数少。

## 硬约束（脚本已内置 + 你必须守住）

- **不碰个人手机号**：正文里的 11 位手机号会被替换为 `[手机号已忽略]`，电话列只收座机/400。
- **不绕过验证码、登录墙、反爬**：脚本只请求公开 URL，遇到 403/404 直接跳过。
- **只做自用/内部决策**：不转售、不对外提供、不做电销名单。
- **保留来源**：`sources.csv` 是你日后自查"这条数据哪来的"的唯一凭证，别删。
- 采集前先看目标站 `robots.txt`；批量任务把 `--sleep` 调到 0.5 以上。

## 常见问题

| 现象 | 原因 | 处理 |
|---|---|---|
| 报「必填变量 DEEPSEEK_API_KEY 没有值」 | 变量没维护 | 跑 `--doctor`，按提示 `setx` / 临时变量 / `key.txt` |
| 条数一直上不去 | 关键词太窄 / 权威性过滤太严 | 换更宽的行业词；`--min-authority 0` |
| 抽出来一堆非企业 | 列表页混了政府机构 | 正常，脚本按"公司/集团/厂/合作社"过滤，仍可能漏 |
| `HTTP 402` | 余额不足 | 充值后 `--resume` 继续 |
| `HTTP 429` | 超过 QPS（Search 100 / Fetch 25） | 脚本已退避重试；加大 `--sleep` |

## 别用它做的事

- 采集手机号、做"决策人"画像
- 抓需要登录的页面、绕过验证码
- 把抓来的整段文案喂给模型训练
- 生成对外售卖的名录包
