import sys
import time

import httpx

base = sys.argv[1].rstrip("/")
headers = {"X-Access-Code": sys.argv[2]} if len(sys.argv) > 2 else {}

print("health:", httpx.get(f"{base}/health", timeout=90).json())
r = httpx.post(f"{base}/runs", headers=headers, timeout=60,
               json={"question": "What are the main environmental impacts of lithium mining for batteries?"})
r.raise_for_status()
run_id = r.json()["run_id"]
print("started", run_id)

for _ in range(150):                       # up to about 10 minutes
    response = httpx.get(f"{base}/runs/{run_id}", timeout=30)

    print("HTTP status:", response.status_code)
    print("Content-Type:", response.headers.get("content-type"))
    print("Response body:", repr(response.text[:1000]))

    response.raise_for_status()
    d = response.json()
    print(f"  {d['status']:8} loops={d['loop_count']} claims={d['counts']}")
    if d["status"] in ("done", "failed"):
        print("error:", d["error"], "\nreport:", f"{base}/runs/{run_id}/report/view")
        break
    time.sleep(4)