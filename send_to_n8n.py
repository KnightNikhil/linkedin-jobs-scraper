"""
send_to_n8n.py
Reads jobs_output.json and sends all jobs to n8n webhook
"""
import json, os, requests, sys, time

WEBHOOK_URL = os.environ.get("N8N_WEBHOOK_URL", "")

if not WEBHOOK_URL:
    print("ERROR: N8N_WEBHOOK_URL not set", file=sys.stderr)
    sys.exit(1)

# Load scraped jobs
try:
    with open("jobs_output.json", "r") as f:
        content = f.read().strip()
    jobs = json.loads(content)
except Exception as e:
    print(f"ERROR reading jobs_output.json: {e}", file=sys.stderr)
    sys.exit(1)

if not jobs:
    print("No jobs found — nothing to send")
    sys.exit(0)

print(f"Sending {len(jobs)} jobs to n8n...")

success = 0
failed  = 0

for i, job in enumerate(jobs):
    try:
        resp = requests.post(
            WEBHOOK_URL,
            json=job,
            headers={"Content-Type": "application/json"},
            timeout=30
        )
        if resp.status_code == 200:
            success += 1
            print(f"  ✅ [{i+1}/{len(jobs)}] {job.get('title','?')} @ {job.get('company','?')}")
        else:
            failed += 1
            print(f"  ❌ [{i+1}/{len(jobs)}] HTTP {resp.status_code} — {job.get('title','?')}")
    except Exception as e:
        failed += 1
        print(f"  ❌ [{i+1}/{len(jobs)}] Error: {e}")

    time.sleep(0.3)

print(f"\nDone! ✅ {success} sent | ❌ {failed} failed")
