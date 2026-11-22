"""Fetch competition / dataset files through the Kaggle HTTP API.

The pip `kaggle` CLI on this machine is stuck at 1.7.4.5, which predates API
tokens (`KGAT_...`) and only understands the legacy kaggle.json. Rather than
fight the CLI, call the same REST endpoints with `Authorization: Bearer`.

Token is read from ~/.kaggle/access_token (or $KAGGLE_API_TOKEN).

Examples:
    python scripts/kaggle_fetch.py comp name-that-llm --list
    python scripts/kaggle_fetch.py comp name-that-llm --all
    python scripts/kaggle_fetch.py comp name-that-llm --files train.csv test.csv
    python scripts/kaggle_fetch.py dataset crtproduction/name-that-llm-open-pool --all
"""
from __future__ import annotations

import argparse
import io
import json
import os
import sys
import zipfile
from pathlib import Path

import requests

API = "https://www.kaggle.com/api/v1"
TOKEN_PATHS = [Path(os.environ.get("KAGGLE_CONFIG_DIR", Path.home() / ".kaggle")) / "access_token"]


def token() -> str:
    env = os.environ.get("KAGGLE_API_TOKEN")
    if env:
        return env.strip()
    for p in TOKEN_PATHS:
        if p.exists():
            return p.read_text(encoding="utf-8").strip()
    raise SystemExit("no KAGGLE_API_TOKEN and no ~/.kaggle/access_token")


def session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"Authorization": f"Bearer {token()}"})
    return s


def files(s: requests.Session, kind: str, ref: str) -> list[dict]:
    if kind == "comp":
        r = s.get(f"{API}/competitions/data/list/{ref}", timeout=30)
    else:
        r = s.get(f"{API}/datasets/list/{ref}", timeout=30)
    r.raise_for_status()
    data = r.json()
    return data.get("files", data if isinstance(data, list) else [])


def download(s: requests.Session, kind: str, ref: str, name: str, out: Path, quiet: bool = False) -> Path:
    if kind == "comp":
        url = f"{API}/competitions/data/download/{ref}/{name}"
    else:
        url = f"{API}/datasets/download/{ref}/{name}"
    out.parent.mkdir(parents=True, exist_ok=True)
    with s.get(url, stream=True, timeout=120) as r:
        r.raise_for_status()
        tmp = out.with_suffix(out.suffix + ".part")
        n = 0
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
                n += len(chunk)
        tmp.replace(out)
    if not quiet:
        print(f"  {name}  {n / 1e6:.2f} MB -> {out}")
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("kind", choices=["comp", "dataset"])
    ap.add_argument("ref")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--files", nargs="*", default=[])
    ap.add_argument("--out", default="data")
    args = ap.parse_args()

    s = session()
    fs = files(s, args.kind, args.ref)
    names = [f.get("name") or f.get("nameNullable") for f in fs]
    if args.list or not (args.all or args.files):
        print(json.dumps([{"name": f.get("name"), "bytes": f.get("totalBytes")} for f in fs], indent=1))
        return

    targets = names if args.all else args.files
    unknown = [t for t in targets if t not in names]
    if unknown:
        print(f"warning: not in listing: {unknown}", file=sys.stderr)
    outdir = Path(args.out)
    for t in targets:
        dest = outdir / t
        if dest.exists() and dest.stat().st_size > 0:
            print(f"  {t}  (already there)")
            continue
        download(s, args.kind, args.ref, t, dest)

    # If Kaggle handed us a single zip, unpack it in place.
    for z in outdir.glob("*.zip"):
        with zipfile.ZipFile(z) as zf:
            zf.extractall(outdir)
        print(f"  unzipped {z.name}")
        z.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
