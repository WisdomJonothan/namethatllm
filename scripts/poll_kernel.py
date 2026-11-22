#!/usr/bin/env python
"""每 N 秒查一次 Kaggle 内核状态，写进日志文件；结束时退出。

    python scripts/poll_kernel.py --slug ntl-diag-stage1 --interval 300 --hours 10
"""
from __future__ import annotations

import argparse
import os
import time
from pathlib import Path

TOKEN_FILE = Path(r"C:\Users\Lenovo\.kaggle\access_token")
LOG = Path(r"D:\NameThatLLM\logs\diag1_status.log")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--owner", default="jonothanwu")
    ap.add_argument("--slug", default="ntl-diag-stage1")
    ap.add_argument("--interval", type=int, default=300)
    ap.add_argument("--hours", type=float, default=10.0)
    args = ap.parse_args()

    os.environ["KAGGLE_API_TOKEN"] = TOKEN_FILE.read_text(encoding="utf-8").strip()
    from kagglesdk.kaggle_client import KaggleClient
    from kagglesdk.kernels.types.kernels_api_service import (
        ApiGetKernelSessionStatusRequest, ApiListKernelSessionOutputRequest)

    c = KaggleClient().kernels.kernels_api_client
    req = ApiGetKernelSessionStatusRequest()
    req.user_name = args.owner
    req.kernel_slug = args.slug
    oreq = ApiListKernelSessionOutputRequest()
    oreq.user_name = args.owner
    oreq.kernel_slug = args.slug

    LOG.parent.mkdir(parents=True, exist_ok=True)
    t_end = time.time() + args.hours * 3600
    while time.time() < t_end:
        ts = time.strftime("%H:%M:%S")
        try:
            st = c.get_kernel_session_status(req)
            status = str(getattr(st, "status", ""))
            fail = (getattr(st, "failure_message", "") or "")[:200]
            files = []
            try:
                out = c.list_kernel_session_output(oreq)
                files = [(f.file_name, f.total_bytes) for f in (out.files or [])]
            except Exception:
                pass
            with LOG.open("a", encoding="utf-8") as f:
                f.write(f"{ts} {status} fail={fail!r} files={files}\n")
            print(f"{ts} {status} files={len(files)}", flush=True)
            if any(k in status.lower() for k in ("complete", "error", "cancel")):
                break
        except Exception as e:
            with LOG.open("a", encoding="utf-8") as f:
                f.write(f"{ts} POLL_ERR {e!r}\n")
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
