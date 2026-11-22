"""用 kagglesdk（Bearer token）推送/查询 Kaggle notebook，并可提交 code competition。

    python scripts/kaggle_push_kernel.py push  --nb notebooks/submit_qlora.ipynb [--run]
    python scripts/kaggle_push_kernel.py status --slug jonothanwu/ntllm-qwen3-8b-qlora
    python scripts/kaggle_push_kernel.py submit --slug ... --message "v1"
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

TOKEN_FILE = Path(r"C:\Users\Lenovo\.kaggle\access_token")
OWNER = "jonothanwu"
SLUG = "ntllm-qwen3-8b-qlora"
COMP = "name-that-llm"
DATASETS = [f"{OWNER}/ntllm-adapter"]
MODELS = ["qwen-lm/qwen-3/transformers/8b"]


def client():
    os.environ["KAGGLE_API_TOKEN"] = TOKEN_FILE.read_text(encoding="utf-8").strip()
    from kagglesdk.kaggle_client import KaggleClient
    return KaggleClient()


def do_push(args) -> None:
    from kagglesdk.kernels.types.kernels_api_service import ApiSaveKernelRequest
    nb = json.loads(Path(args.nb).read_text(encoding="utf-8"))
    req = ApiSaveKernelRequest()
    req.slug = f"{OWNER}/{args.slug}"
    req.new_title = args.title
    req.text = json.dumps(nb, ensure_ascii=False)
    req.language = "python"
    req.kernel_type = "notebook"
    req.dataset_data_sources = DATASETS
    req.competition_data_sources = [COMP]
    req.model_data_sources = MODELS
    req.enable_gpu = True
    req.enable_internet = bool(args.internet)
    req.is_private = True
    resp = client().kernels.kernels_api_client.save_kernel(req)
    print("saved:", getattr(resp, "url", ""), "| error:", getattr(resp, "error", ""),
          "| version:", getattr(resp, "version_number", ""))
    if getattr(resp, "error", ""):
        sys.exit(1)


def do_status(args) -> None:
    from kagglesdk.kernels.types.kernels_api_service import ApiGetKernelSessionStatusRequest
    req = ApiGetKernelSessionStatusRequest()
    req.user_name = OWNER
    req.kernel_slug = args.slug
    resp = client().kernels.kernels_api_client.get_kernel_session_status(req)
    print("status:", resp.status, "| failure:", getattr(resp, "failure_message", ""))


def do_submit(args) -> None:
    from kagglesdk.competitions.types.competition_api_service import \
        ApiCreateCodeSubmissionRequest
    req = ApiCreateCodeSubmissionRequest()
    req.competition_name = COMP
    req.kernel_slug = args.slug
    req.kernel_version = int(args.version)
    req.submission_description = args.message
    req.file_name = args.file or "submission.parquet"
    req.kernel_owner = OWNER
    try:
        resp = client().competitions.competition_api_client.create_code_submission(req)
    except Exception as e:
        body = getattr(getattr(e, "response", None), "text", "")
        print("EXC", type(e).__name__, e, "| body:", body[:800])
        raise SystemExit(1)
    print("submission:", resp.ref, "| error:", resp.error, "| message:", resp.message)


def do_list_files(args) -> None:
    from kagglesdk.kernels.types.kernels_api_service import ApiListKernelFilesRequest
    req = ApiListKernelFilesRequest()
    req.user_name = OWNER
    req.kernel_slug = args.slug
    resp = client().kernels.kernels_api_client.list_kernel_files(req)
    for f in resp.files:
        print(f.name, f.total_bytes)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["push", "status", "submit", "files"])
    ap.add_argument("--nb", default="notebooks/submit_qlora.ipynb")
    ap.add_argument("--slug", default=SLUG)
    ap.add_argument("--title", default="NTL Qwen3-8B QLoRA + group constraint")
    ap.add_argument("--run", action="store_true", help="push 后等待一次运行完成")
    ap.add_argument("--version", default="")
    ap.add_argument("--message", default="")
    ap.add_argument("--file", default="submission.parquet")
    ap.add_argument("--internet", action="store_true")
    args = ap.parse_args()

    {"push": do_push, "status": do_status, "submit": do_submit, "files": do_list_files}[args.cmd](args)

    if args.cmd == "push" and args.run:
        from kagglesdk.kernels.types.kernels_api_service import ApiGetKernelSessionStatusRequest
        c = client()
        t0 = time.time()
        while time.time() - t0 < 3600:
            req = ApiGetKernelSessionStatusRequest()
            req.user_name = OWNER
            req.kernel_slug = args.slug
            st = c.kernels.kernels_api_client.get_kernel_session_status(req).status
            print(f"[{time.strftime('%H:%M:%S')}] {st}", flush=True)
            if st not in ("queued", "running"):
                break
            time.sleep(30)


if __name__ == "__main__":
    main()
