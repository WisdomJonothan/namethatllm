import os, sys, time, json, datetime
from pathlib import Path
os.environ["KAGGLE_API_TOKEN"] = Path(r"C:\Users\Lenovo\.kaggle\access_token").read_text().strip()
from kagglesdk.kaggle_client import KaggleClient
from kagglesdk.competitions.types.competition_api_service import ApiListSubmissionsRequest

REF = int(sys.argv[1]); INTERVAL = int(sys.argv[2]); HOURS = float(sys.argv[3])
c = KaggleClient().competitions.competition_api_client
end = time.time() + HOURS * 3600
prev = None
while time.time() < end:
    req = ApiListSubmissionsRequest(); req.competition_name = "name-that-llm"
    try:
        subs = c.list_submissions(req).submissions or []
    except Exception as e:
        print(f"{datetime.datetime.now():%H:%M:%S} ERR {e!r}", flush=True); time.sleep(INTERVAL); continue
    hit = [s for s in subs if getattr(s, "ref", None) == REF]
    if not hit:
        print("ref missing", flush=True); break
    s = hit[0]
    line = (f"{datetime.datetime.now():%H:%M:%S} status={s.status} public={s.public_score!r} "
            f"private={s.private_score!r} err={(s.error_description or '')[:100]}")
    if line != prev:
        print(line, flush=True); prev = line
    if s.status in ("COMPLETE", "ERROR", "FAILED") or (s.public_score not in ("", None)):
        print("FINAL " + json.dumps({"ref": s.ref, "status": s.status, "public": s.public_score,
                                     "private": s.private_score, "err": s.error_description}), flush=True)
        break
    time.sleep(INTERVAL)
