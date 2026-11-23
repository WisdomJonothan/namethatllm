import os, sys
from pathlib import Path
import urllib.request

TOKEN = Path(r"C:\Users\Lenovo\.kaggle\access_token").read_text(encoding="utf-8").strip()
os.environ["KAGGLE_API_TOKEN"] = TOKEN
from kagglesdk.kaggle_client import KaggleClient
from kagglesdk.kernels.types.kernels_api_service import ApiListKernelSessionOutputRequest

slug = sys.argv[1]
dest = Path(sys.argv[2]); dest.mkdir(parents=True, exist_ok=True)
c = KaggleClient().kernels.kernels_api_client
req = ApiListKernelSessionOutputRequest(); req.user_name = "jonothanwu"; req.kernel_slug = slug
out = c.list_kernel_session_output(req)
files = out.files or []
print(f"{len(files)} files")
for f in files:
    name = f.file_name
    p = dest / name
    p.parent.mkdir(parents=True, exist_ok=True)
    if p.exists() and p.stat().st_size == f.total_bytes:
        print("skip", name); continue
    try:
        with urllib.request.urlopen(f.url, timeout=120) as r, open(p, "wb") as fh:
            fh.write(r.read())
        print(f"{name}\t{p.stat().st_size}")
    except Exception as e:
        print("ERR", name, e)
