# factory-collect

> 用 DeepSeek 的 Search / Fetch 接口，把**公开网页**采成一张能直接分析的 CSV。
> 给 B2B 销售、采购、产业研究用：按「行业 × 地区」找工厂、找供应商，顺手把招投标记录也带走。

[![Python](https://img.shields.io/badge/python-3.9%2B-blue)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)
[![No deps](https://img.shields.io/badge/dependencies-none-brightgreen)](#)
[![Agent Skill](https://img.shields.io/badge/agent%20skill-SKILL.md-purple)](SKILL.md)

不绕过验证码、不登录抓取、不采集个人手机号——只做公开数据的结构化。

---

## 它解决什么

找工厂、找客户这件事，卡在三个地方：

1. **工厂和非工厂混在一起**——工商库里贸易商、空壳、个体户和真厂并排；
2. **筛选维度太粗**——二级行业一个大类装几百种产品，等于没筛；
3. **拿到名单没法直接用**——没有产品、没有来源、没有可追溯的证据。

这个技能的做法是：**从公开信息里找线索，用模型抽字段，每条都留来源**。

## 它怎么工作

```
查询计划 → Search → Fetch(按需) → 模型抽取 → 去重 → 4 个文件
```

1. **拼查询计划**：把「地区 + 行业」展开成 9 条搜索词（名单公示 / 专精特新 / 中标公告 / 官网 / 产品介绍…），自动排除招聘站和问答站
2. **Search**：`POST /v1/tools/search`，`need_content=true` 直接带回正文（比逐页 Fetch 便宜约 20 倍）
3. **Fetch**：正文不足 300 字时才补抓，有次数上限
4. **抽取**：JSON 模式，只抽页面**明写**的字段，缺就留空，禁止推断
5. **去重落盘**：按统一社会信用代码（无则按规范化企业名）去重

## 安装

克隆到 Agent Skills 的发现路径：

```bash
# 项目级（只对当前项目生效）
git clone https://github.com/<you>/factory-collect.git .agents/skills/factory-collect

# 用户级（DSH，所有项目可用）
git clone https://github.com/<you>/factory-collect.git ~/.dsh/skills/factory-collect
```

要求：Python 3.9+，**仅标准库**，无第三方依赖。

## 配置 Key

必填变量 `DEEPSEEK_API_KEY`（在 <https://platform.deepseek.com/api_keys> 创建）：

```bash
# 永久（Windows，重开终端生效）
setx DEEPSEEK_API_KEY "sk-xxxx"

# 永久（macOS / Linux）
echo 'export DEEPSEEK_API_KEY="sk-xxxx"' >> ~/.bashrc

# 临时
export DEEPSEEK_API_KEY="sk-xxxx"
```

没配会直接报错并给出维护方式；也可以交互输入（不回显）：

```bash
python scripts/collect.py --query 注塑模具 --ask-key --save-key
```

自检：

```bash
python scripts/collect.py --doctor
```

```
1) 变量 DEEPSEEK_API_KEY：❌ 未设置 / 为空  ← 需要维护的就是它
2) 兜底文件 key.txt：未使用
3) .gitignore 忽略 key/.env：✅ 已覆盖
4) Key 有效性：跳过（没有 key）
```

## 快速开始

```bash
# 看查询计划，不花钱
python scripts/collect.py --query 注塑模具 --region 浙江省 --dry-run

# 采 200 条
python scripts/collect.py --query 注塑模具 --region 浙江省 --target 200 --out data/zj_mold.csv

# 断点续跑
python scripts/collect.py --query 注塑模具 --region 浙江省 --target 200 --out data/zj_mold.csv --resume

# 离线自检（不联网，验证编码/列/手机号过滤）
python scripts/collect.py --selftest
```

## 输出

| 文件 | 内容 |
|---|---|
| `xxx.csv` | 企业主表，20 列，UTF-8-SIG（Excel 直开） |
| `xxx.bids.csv` | 招投标长表，一家企业可对应多条中标记录 |
| `xxx.sources.csv` | 来源页：url / 标题 / 日期 / 权威性 / 命中条数 |
| `xxx.run.json` | 查询次数、抓取次数、企业数、中标数、失败数、耗时、估算费用 |

**主表列**

```
企业名称, 统一社会信用代码, 省, 市, 区县, 行业, 注册资本, 成立日期,
经营状态, 经营范围, 主营产品, 产品介绍, 官网, 公开电话, 来源URL,
来源标题, 页面日期, 置信度, 证据句, 采集时间
```

**招投标列**

```
企业名称, 统一社会信用代码, 项目名称, 采购方, 中标金额,
公告日期, 来源URL, 证据句, 采集时间
```

设计上做了两件事：

- **招投标单独一张表**：企业和中标是 1:N，塞进主表会产生重复行
- **每行带 `置信度` + `证据句` + `来源URL`**：低置信度的可以人工复核，不用盲信

## 实战数据

一次真实运行（2026-11，`--query 工业交换机`，全国）：

| 指标 | 结果 |
|---|---|
| 用时 | 10 分钟 |
| 费用 | 0.05 元（9 次 Search + 1 次 Fetch） |
| 产出 | 73 家企业 + 51 条中标记录 |
| 去重后采购方 | 17 个（电网 / 轨交 / 矿业 / 石化 / 集成商） |

采购方包括广东电网广州供电局、通号城市轨道交通、山东黄金焦家金矿、九江石化、玉门油田等；中标方集中在南瑞信通、广东信通、三旺通信、烽火、东土、新华三。

## 成本

DeepSeek Search / Fetch 均为 **5 元 / 1000 次成功请求**。

- 一次 Search 带正文 ≈ 0.005 元，可覆盖 20 条结果
- 200 条目标约 10~20 次搜索，**0.1 元上下**

## 参数

| 参数 | 默认 | 说明 |
|---|---|---|
| `--query` | 注塑模具 | 行业 / 产品词 |
| `--region` | 空（全国） | 地区，如 `浙江省` |
| `--target` | 200 | 目标企业数 |
| `--out` | `data/companies.csv` | 输出路径 |
| `--mode` | `list` | `list` 先找名单页（快）/ `company` 先找企业页（全） |
| `--model` | `deepseek-chat` | 抽取模型；账号不支持时自动切可用模型 |
| `--min-authority` | 1 | 只要权威性 ≥ N 的来源（0~4） |
| `--max-fetches` | 40 | 补抓页面上限 |
| `--resume` | 关 | 断点续跑 |
| `--dry-run` | — | 只打印查询计划 |
| `--doctor` | — | 环境自检 |
| `--ask-key` / `--save-key` | — | 交互输入 / 保存 Key |
| `--no-prompt` | — | 禁止提示（CI / 定时任务） |

## 合规边界

- 只采公开页面，不绕过验证码 / 登录墙 / 反爬
- 正文中的 11 位手机号自动替换为 `[手机号已忽略]`，电话列只保留座机 / 400
- 抓取的文案、图片版权归原站，不要整段搬运或用于训练
- 采集结果仅供内部决策，不转售、不做电销名单
- 采集前确认目标站 `robots.txt`；批量任务把 `--sleep` 调大

## 安全：Key 不会进仓库

| 层 | 文件 | 作用 |
|---|---|---|
| 忽略 | `.gitignore` | `key.txt` / `.env` / `*.key` 不进 `git status` |
| 拦截 | `.githooks/pre-commit` | `git commit` 前跑扫描，命中就失败 |
| 复扫 | `.github/workflows/secret-scan.yml` | push / PR 后 CI 再扫一遍 |

启用钩子（clone 后执行一次）：

```bash
git config core.hooksPath .githooks
```

手动扫描：

```bash
python scripts/check_secrets.py
```

如果 Key 曾经提交过：**先去平台吊销**，再用 `git filter-repo` 清历史。

## 目录结构

```
factory-collect/
├── SKILL.md                    # Agent Skill 定义（name / description / 用法）
├── README.md
├── LICENSE
├── scripts/
│   ├── collect.py              # 主脚本
│   └── check_secrets.py        # 提交前密钥扫描
├── tests/
│   └── mock_api.py             # 本地 mock，离线跑通全链路
├── .githooks/pre-commit        # commit 时自动扫密钥
├── .github/workflows/          # CI 里再扫一遍
└── data/
    └── mock-demo.*             # 样例输出（真实采集结果已被 .gitignore 排除）
```

## 开发

```bash
# 起 mock（不花钱、不联网），跑通全链路
python tests/mock_api.py &
python scripts/collect.py --base-url http://127.0.0.1:8765 --api-key sk-test \
  --query 注塑模具 --region 浙江省 --target 10 --out data/e2e.csv
```

## 适合 / 不适合

**适合**

- B2B 销售按「产业带 + 行业」拉客户名单
- 采购按品类找供应商，先看有没有工厂、有没有认证
- 产业研究做区域 / 行业的样本盘点

**不适合**

- 需要权威工商背调（用国家企业信用信息公示系统 / 爱企查）
- 需要老板手机号做电销（这是红线，别做）
- 需要实时、100% 准确的企业档案（公开网页抽出来的，必然有误差）

## License

MIT
