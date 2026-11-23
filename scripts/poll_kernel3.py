import os, sys, time
from pathlib import Path
os.environ["KAGGLE_API_TOKEN"] = Path(r"C:\Users\Lenovo\.kaggle\access_token").read_text().strip()
from kagglesdk.kaggle_client import KaggleClient
from kagglesdk.kernels.types.kernels_api_service import ApiGetKernelSessionStatusRequest

slug, interval, hours = sys.argv[1], int(sys.argv[2]), float(sys.argv[3])
k = KaggleClient().kernels.kernels_api_client
last, t0 = None, time.time()
while time.time() - t0 < hours * 3600:
    r = ApiGetKernelSessionStatusRequest(); r.user_name = "jonothanwu"; r.kernel_slug = slug
    try:
        st = str(getattr(k.get_kernel_session_status(r), "status", "?"))
    except Exception as e:
        st = "QUERY_ERR:" + str(e)[:80]
    if st != last:
        print(time.strftime("%H:%M:%S"), st, flush=True); last = st
    if st.split(".")[-1] in ("COMPLETE", "ERROR", "FAILED", "CANCELED"):
        print("DONE", st, flush=True); break
    time.sleep(interval)
