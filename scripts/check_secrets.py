#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""提交前自检：确认仓库里没有 API Key / 密钥文件 / 硬编码凭据。

用法：
    python scripts/check_secrets.py          # 扫描工作区 + 已跟踪文件 + git 历史
退出码：
    0 = 通过；1 = 发现问题（不要提交/推送）
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

PATTERNS = [
    (re.compile(r"sk-[A-Za-z0-9]{16,}"), "疑似 API Key（sk- 开头 + 长串）"),
    (re.compile(r"DEEPSEEK_API_KEY\s*=\s*['\"]?sk-[A-Za-z0-9]{16,}", re.I), "变量被直接赋了 Key"),
    (re.compile(r"(api[_-]?key|secret|passwd|password|token)\s*[:=]\s*['\"][A-Za-z0-9_\-]{20,}['\"]", re.I),
     "疑似硬编码凭据"),
]

BAD_FILES = {"key.txt", ".env", "credentials.json"}
SKIP_DIRS = {".git", "__pycache__", "node_modules", ".venv", "venv", "dist", "build"}
SKIP_SUFFIX = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".zip", ".exe", ".pdf", ".pyc", ".whl"}


def _git_ignored(path: Path) -> bool:
    """已配置在 .gitignore 里的文件不参与扫描，也不报警。"""
    try:
        r = subprocess.run(["git", "check-ignore", "--quiet", str(path)],
                           cwd=ROOT, capture_output=True, timeout=15)
        return r.returncode == 0
    except Exception:  # noqa: BLE001
        return False


def iter_files():
    for p in ROOT.rglob("*"):
        if not p.is_file():
            continue
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        if p.suffix.lower() in SKIP_SUFFIX:
            continue
        if _git_ignored(p):
            continue
        yield p


def git(args, **kw):
    try:
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True,
                              text=True, encoding="utf-8", errors="replace",
                              timeout=60, **kw).stdout
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""


def main() -> int:
    problems: list[str] = []

    # 1) 敏感文件名（被 .gitignore 忽略的不算：那是本地正常配置）
    for name in BAD_FILES:
        for p in ROOT.rglob(name):
            if ".git" in p.parts:
                continue
            if _git_ignored(p):
                continue
            problems.append(f"存在敏感文件：{p.relative_to(ROOT)}")

    # 2) 工作区内容
    n_files = 0
    for p in iter_files():
        n_files += 1
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for i, line in enumerate(text.splitlines(), 1):
            for rx, why in PATTERNS:
                if rx.search(line):
                    problems.append(f"{p.relative_to(ROOT)}:{i}  {why}：{line.strip()[:60]}")

    # 3) git 已跟踪文件（防止 .gitignore 之前就 add 过）
    tracked = [t for t in git(["ls-files"]).splitlines() if t.strip()]
    for t in tracked:
        if Path(t).name in BAD_FILES:
            problems.append(f"git 已跟踪敏感文件：{t}（需 git rm --cached）")

    # 4) git 历史（只扫新增行，限制条数避免慢）
    if tracked:
        diff = git(["log", "-p", "--all", "--no-color", "-G", "sk-[A-Za-z0-9]{16,}"])
        for line in diff.splitlines():
            if line.startswith("+") and not line.startswith("+++"):
                if re.search(r"sk-[A-Za-z0-9]{16,}", line):
                    problems.append(f"git 历史里出现疑似 Key：{line.strip()[:70]}")
                    break

    if problems:
        print("❌ 自检未通过，先处理完再提交：\n")
        for item in problems:
            print("  - " + item)
        print("\n修复建议：")
        print("  · 删除 key.txt / .env，或确认它们在 .gitignore 里")
        print("  · 已跟踪的：git rm --cached <文件>")
        print("  · 已提交进历史：立刻去平台吊销该 Key，再用 git filter-repo 清历史并强推")
        return 1

    print(f"✅ 通过：扫描 {n_files} 个文件，未发现密钥或凭据。")
    if tracked:
        print(f"   已跟踪文件 {len(tracked)} 个，历史未命中。")
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass
    sys.exit(main())
