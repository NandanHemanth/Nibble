"""Run the demo path once so the stage run is served from cache.

Start the server first (./run.sh), then:

    .venv/bin/python scripts/prewarm.py 07302 "ground coffee 12 oz" "large eggs 12 count"

The first argument is the ZIP; the rest are the items you will click
"Why this price?" on. The sample shopping list is priced for that ZIP too.
"""
import sys
import time

import httpx

BASE = "http://127.0.0.1:8000"
zip_code = sys.argv[1] if len(sys.argv) > 1 else "10001"
why_items = sys.argv[2:] or ["ground coffee 12 oz"]


def wait(client: httpx.Client, job: str, label: str) -> dict:
    while True:
        j = client.get(f"{BASE}/api/jobs/{job}").json()
        if j["status"] != "running":
            print(f"{label}: {j['status']} in {j.get('seconds')} s {j.get('error') or ''}")
            return j
        print(f"  {label}: {j['phase']} ({j['elapsed']:.0f} s)")
        time.sleep(3)


with httpx.Client(timeout=30) as client:
    items = client.get(f"{BASE}/api/status").json()["sample_list"]
    job = client.post(f"{BASE}/api/basket", json={"items": items, "zip": zip_code}).json()["job"]
    wait(client, job, "basket")
    for item in why_items:
        job = client.post(f"{BASE}/api/why", json={"item": item, "zip": zip_code}).json()["job"]
        wait(client, job, f"why {item}")
    client.get(f"{BASE}/api/news")
    print("Warm. Do not change the ZIP or the list wording before the demo.")
