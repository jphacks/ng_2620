"""End-to-end smoke test against a running server: upload -> analyze -> render -> export (with one correction).

  python scripts/smoke_e2e.py --url http://127.0.0.1:8765 --token $VVLOG_API_TOKEN --video clip.mp4 --vrm a.vrm
      [--out smoke_out] [--person 1]

Prints each stage's time from the SSE events and saves every artifact under --out (designdocs/features/
analysis-pipeline テスト: 実 GPU での通し).
"""
import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

import httpx


def main(a):
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    c = httpx.Client(base_url=a.url, headers={"Authorization": f"Bearer {a.token}"}, timeout=120)
    print("health", c.get("/v1/health").json(), "caps", c.get("/v1/capabilities").json())
    pid = c.post("/v1/projects", json={"name": "smoke"}).raise_for_status().json()["projectId"]
    data = Path(a.video).read_bytes()
    up = c.post("/v1/uploads", json={"projectId": pid, "kind": "source", "filename": Path(a.video).name,
                                     "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}).raise_for_status().json()
    for i in range(0, len(data), up["chunkSize"]):
        c.put(f"/v1/uploads/{up['uploadId']}/chunks/{i // up['chunkSize']}", content=data[i:i + up["chunkSize"]]).raise_for_status()
    media = c.post(f"/v1/uploads/{up['uploadId']}/complete").raise_for_status().json()
    print("media", media)

    def run(job_type, params):
        t0 = time.time()
        job = c.post(f"/v1/projects/{pid}/jobs", json={"type": job_type, "params": params}).raise_for_status().json()
        started = {}
        with c.stream("GET", f"/v1/jobs/{job['jobId']}/events", timeout=None) as r:
            ev = {}
            for line in r.iter_lines():
                if not line:
                    if ev.get("event") in ("stage_started", "stage_finished", "artifact_ready", "job_failed",
                                           "job_finished"):
                        d = json.loads(ev["data"])
                        t = time.time() - t0
                        if ev["event"] == "stage_started":
                            started[d["stage"]] = t
                        elif ev["event"] == "stage_finished":
                            print(f"  {job_type}.{d['stage']:10s} {d['durationSec']:7.1f} s   (at {t:6.1f} s)")
                        elif ev["event"] == "artifact_ready":
                            print(f"  artifact {d['name']:14s} {d['size'] / 1e6:8.2f} MB (at {t:6.1f} s)")
                        else:
                            print(f"  {ev['event']} {d}")
                    ev = {}
                    continue
                k, _, v = line.partition(":")
                ev[k] = v.strip()
        j = c.get(f"/v1/jobs/{job['jobId']}").json()
        print(f"{job_type}: {j['status']} in {time.time() - t0:.1f} s")
        if j["status"] != "succeeded":
            print(c.get(f"/v1/jobs/{job['jobId']}/logs", params={"tail": 60}).text)
            sys.exit(1)
        for art in c.get(f"/v1/jobs/{job['jobId']}/artifacts").json():
            (out / job_type).mkdir(exist_ok=True)
            (out / job_type / art["url"].rsplit("/", 1)[1]).write_bytes(c.get(art["url"]).content)
        return j

    an = run("analyze", {"mediaId": media["mediaId"]})
    persons = json.loads((out / "analyze" / "persons").read_bytes())
    units = json.loads((out / "analyze" / "units_index").read_bytes())["units"]
    print("persons", [(p["personId"], p["visibleRatio"]) for p in persons], "units", len(units))
    vrm = Path(a.vrm)
    avatar = c.post("/v1/avatars", files={"file": (vrm.name, vrm.read_bytes(), "application/octet-stream")}).raise_for_status().json()
    person = a.person or persons[0]["personId"]
    rj = run("render", {"analyzeJobId": an["jobId"], "cast": [{"personId": person, "action": "replace",
                                                                 "avatarId": avatar["avatarId"]}]})
    ovr = []
    if units:
        u = units[0]
        ovr = [{"id": "ovr_smoke", "kind": "unit", "personId": person, "unitId": u["unitId"],
                "range": u["frames"], "mode": "front"}]
    run("export", {"renderJobId": rj["jobId"], "overrides": ovr, "settings": {"format": "mp4_h264"}})
    print("artifacts in", out.resolve())


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8765")
    ap.add_argument("--token", required=True)
    ap.add_argument("--video", required=True)
    ap.add_argument("--vrm", required=True)
    ap.add_argument("--out", default="smoke_out")
    ap.add_argument("--person", type=int, default=None)
    main(ap.parse_args())
