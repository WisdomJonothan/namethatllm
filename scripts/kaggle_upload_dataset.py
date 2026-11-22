"""把本地目录上传成（私有）Kaggle Dataset，走 kagglesdk 的 Bearer token 流程。

    python scripts/kaggle_upload_dataset.py --dir ./runs/adapter_upload \
        --slug ntllm-adapter --title "NTL Adapter" --notes "fold0" [--version]

流程：upload_dataset_file 拿 token+create_url → PUT 字节 → create_dataset / create_dataset_version。
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

import requests

TOKEN_FILE = Path(r"C:\Users\Lenovo\.kaggle\access_token")
OWNER = "jonothanwu"


def put_bytes(url: str, data: bytes, name: str) -> None:
    """上传到签名 URL：先试 PUT 原始字节，失败再试 multipart POST。"""
    r = requests.put(url, data=data, headers={"Content-Type": "application/octet-stream"},
                     timeout=900)
    if r.status_code in (200, 201, 204):
        return
    r2 = requests.post(url, files={"file": (name, data, "application/octet-stream")}, timeout=900)
    if r2.status_code in (200, 201, 204):
        return
    raise RuntimeError(f"upload failed PUT={r.status_code} {r.text[:200]!r} "
                       f"POST={r2.status_code} {r2.text[:200]!r}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--slug", default="ntllm-adapter")
    ap.add_argument("--title", default="NTL QLoRA adapter")
    ap.add_argument("--notes", default="")
    ap.add_argument("--version", action="store_true", help="已存在则新建版本")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--dummy", action="store_true", help="只上传一个测试文件")
    args = ap.parse_args()

    os.environ["KAGGLE_API_TOKEN"] = TOKEN_FILE.read_text(encoding="utf-8").strip()
    from kagglesdk.datasets.types.dataset_api_service import (
        ApiCreateDatasetRequest, ApiCreateDatasetVersionRequest,
        ApiCreateDatasetVersionRequestBody, ApiDatasetNewFile,
        ApiUploadDatasetFileRequest)
    from kagglesdk.kaggle_client import KaggleClient

    files = sorted(p for p in Path(args.dir).rglob("*") if p.is_file())
    if args.dummy:
        files = files[:1]
    print(f"files={[(f.name, round(f.stat().st_size / 1e6, 2)) for f in files]}")
    if args.dry_run:
        return

    client = KaggleClient()

    tokens = []
    for f in files:
        size = f.stat().st_size
        req = ApiUploadDatasetFileRequest()
        req.file_name = f.name
        req.content_length = size
        req.last_modified_epoch_seconds = int(f.stat().st_mtime)
        resp = client.datasets.dataset_api_client.upload_dataset_file(req)
        print(f"  blob {f.name}: token={resp.token[:12]}... url={resp.create_url[:60]}...")
        put_bytes(resp.create_url, f.read_bytes(), f.name)
        tokens.append(resp.token)

    new_files = []
    for t in tokens:
        nf = ApiDatasetNewFile()
        nf.token = t
        new_files.append(nf)

    if args.version:
        body = ApiCreateDatasetVersionRequestBody()
        body.version_notes = args.notes or f"update {time.strftime('%Y-%m-%d %H:%M')}"
        body.delete_old_versions = False
        body.files = new_files
        req = ApiCreateDatasetVersionRequest()
        req.owner_slug = OWNER
        req.dataset_slug = args.slug
        req.body = body
        resp = client.datasets.dataset_api_client.create_dataset_version(req)
    else:
        req = ApiCreateDatasetRequest()
        req.owner_slug = OWNER
        req.slug = args.slug
        req.title = args.title
        req.subtitle = args.notes[:80] if args.notes else "Qwen3-8B QLoRA adapter"
        req.description = args.notes or "adapter for Name That LLM"
        req.license_name = "CC0-1.0"
        req.is_private = True
        req.files = new_files
        resp = client.datasets.dataset_api_client.create_dataset(req)

    print("result:", resp.ref, resp.status, resp.error if hasattr(resp, "error") else "", resp.url)
    if resp.error:
        sys.exit(1)


if __name__ == "__main__":
    main()
