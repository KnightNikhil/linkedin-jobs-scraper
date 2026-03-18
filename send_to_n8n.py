"""
send_to_n8n.py
Sends ALL jobs in a single POST request to n8n webhook
"""
import json, os, requests, sys

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

print(f"Sending {len(jobs)} jobs in one request to n8n...")

# ── Send ALL jobs in a single POST ────────────────────────────
try:
    resp = requests.post(
        WEBHOOK_URL,
        json={ "jobs": jobs, "count": len(jobs) },
        headers={"Content-Type": "application/json"},
        timeout=60
    )
    if resp.status_code == 200:
        print(f"✅ Successfully sent {len(jobs)} jobs to n8n")
    else:
        print(f"❌ Failed — HTTP {resp.status_code}: {resp.text[:200]}")
        sys.exit(1)
except Exception as e:
    print(f"❌ Error sending to n8n: {e}")
    sys.exit(1)
