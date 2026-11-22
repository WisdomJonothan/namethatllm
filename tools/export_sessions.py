"""Export DeepSeek Harness session transcripts that belong to the Name That LLM project.

Reads the compressed session logs (session.v4.jsonl.zstd) from the DSH data directory and
writes readable Markdown transcripts into docs/conversations/.

Harness-injected blocks (runtime context, memory snapshots, skill catalogs, compaction
summaries) are dropped so the transcripts contain what the human and assistant actually said.

Usage:
    python tools/export_sessions.py [--sessions DIR] [--out DIR]
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import sys
from pathlib import Path

import pyzstd

KEYWORDS = ("name that llm", "namethatllm", "name-that-llm")
DEFAULT_SESSIONS = Path(os.path.expanduser("~")) / ".dsh" / "sessions"
WORKSPACE_DIR = "--C-Users-Lenovo-Documents-deepseek-harness-default-workspace--"

CONTEXT_PREFIXES = (
    "current runtime context",
    "<system-reminder>",
    "[mnemon]",
    "mnemon runtime memory snapshot",
    "[compressed conversation section]",
    "<available_skills>",
    "<acp",
)


def _ts(ms) -> str:
    if not ms:
        return "?"
    return _dt.datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%d %H:%M:%S")


def _text_of(content) -> str:
    if isinstance(content, str):
        return content
    out = []
    for item in content or []:
        if isinstance(item, dict) and item.get("type") == "text":
            out.append(item.get("text") or "")
    return "\n".join(t for t in out if t)


def _is_injected(text: str) -> bool:
    head = text.strip()[:120].lower()
    return any(head.startswith(p) for p in CONTEXT_PREFIXES)


def _short(value, limit: int = 200) -> str:
    s = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    s = re.sub(r"\s+", " ", s).strip()
    return s if len(s) <= limit else s[:limit] + " …"


def _args_preview(raw: str) -> str:
    try:
        obj = json.loads(raw)
    except Exception:  # noqa: BLE001
        return _short(raw, 160)
    if isinstance(obj, dict):
        bits = []
        for k, v in obj.items():
            if k in ("modifiers", "space"):
                continue
            bits.append(f"{k}={_short(v, 60)}")
            if len(bits) == 3:
                break
        return ", ".join(bits)[:200]
    return _short(obj, 160)


def export_session(path: Path, out_dir: Path):
    try:
        raw = pyzstd.decompress(path.read_bytes()).decode("utf-8", errors="replace")
    except Exception as exc:  # noqa: BLE001
        print(f"skip {path}: {exc}", file=sys.stderr)
        return None

    recs = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            recs.append(json.loads(line))
        except json.JSONDecodeError:
            continue

    header = next((r for r in recs if r.get("type") == "session"), None)
    if not header:
        return None

    user_texts = []
    for r in recs:
        if r.get("type") != "user/message":
            continue
        body = _text_of((r.get("data") or {}).get("content")).strip()
        if body and not _is_injected(body):
            user_texts.append(body)

    if not user_texts:
        return None
    haystack = " ".join(user_texts).lower()
    if not any(k in haystack for k in KEYWORDS + ("areebbb", "0.716", "ntl-diag", "ntl-eval", "name that")):
        return None

    sid = header.get("id", path.parent.name)
    lines = [
        f"# 会话 {sid}",
        "",
        f"- 创建时间: {_ts(header.get('createdAt'))}",
        f"- 工作目录: `{header.get('cwd')}`",
        "",
        "---",
        "",
    ]

    turn = 0
    n = 0
    for r in recs:
        t = r.get("type")
        when = _ts(r.get("time"))
        d = r.get("data") or {}
        if t == "turn/start":
            turn += 1
            lines += ["", f"## 第 {turn} 轮 · {when}", ""]
        elif t == "user/message":
            body = _text_of(d.get("content")).strip()
            if not body or _is_injected(body):
                continue
            n += 1
            lines += [f"> **用户** ({when})", "", body, ""]
        elif t == "assistant/message":
            msg = d.get("message") or {}
            content = msg.get("content") or []
            body = _text_of(content).strip()
            if body:
                n += 1
                lines += ["**助手**", "", body, ""]
        elif t == "tool/call":
            name = d.get("name") or "?"
            lines.append(f"- ⚙ `{name}` {_args_preview(d.get('arguments') or '')}")
        elif t == "tool/result":
            msg = d.get("message") or {}
            txt = _text_of(msg.get("content"))
            if txt:
                lines.append(f"  - ↳ {_short(txt, 160)}")
            lines.append("")

    if n < 1:
        return None

    out_dir.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", sid)
    target = out_dir / f"{safe}.md"
    target.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return sid, target.stat().st_size


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sessions", default=str(DEFAULT_SESSIONS))
    ap.add_argument("--out", default="docs/conversations")
    ap.add_argument("--all-workspaces", action="store_true")
    args = ap.parse_args()

    root = Path(args.sessions)
    base = root if args.all_workspaces else root / WORKSPACE_DIR
    if not base.exists():
        sys.exit(f"sessions dir not found: {base}")

    files = sorted(base.glob("*/session.v4.jsonl.zstd"))
    print(f"scanning {len(files)} session files")

    out_dir = Path(args.out)
    exported = []
    for path in files:
        res = export_session(path, out_dir)
        if res:
            sid, size = res
            exported.append((sid, size))
            print(f"  + {sid} {size/1024:.0f} KB")

    index = ["# 对话记录索引", "", f"共 {len(exported)} 个会话（按会话 id 排序）。", "",
             "| # | 会话 | 大小 |", "|---|---|---|"]
    for i, (sid, size) in enumerate(sorted(exported), 1):
        index.append(f"| {i} | [{sid}]({sid}.md) | {size/1024:.0f} KB |")
    (out_dir / "index.md").write_text("\n".join(index) + "\n", encoding="utf-8")
    print(f"wrote {len(exported)} transcripts to {out_dir}")


if __name__ == "__main__":
    main()
