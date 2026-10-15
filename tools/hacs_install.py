"""Add leboff/ha-better-weather to HACS and download v0.1.0."""
import sys, time
sys.path.insert(0, "/root/.hermes/skills/smart-home/ha-api-management/scripts")
from ha_ws import call

REPO = "leboff/ha-better-weather"
r = call([{"type": "hacs/repositories/add", "repository": REPO, "category": "integration"}])[0]
print("add:", r.get("success"), r.get("error"))
rid = None
for _ in range(10):
    repos = call([{"type": "hacs/repositories/list"}])[0]["result"]
    hit = [x for x in repos if x.get("full_name", "").lower() == REPO]
    if hit:
        rid = hit[0]["id"]; print("repo:", {k: hit[0].get(k) for k in ("id", "installed", "available_version", "status")}); break
    time.sleep(3)
if not rid:
    sys.exit("repo not found in HACS list")
r = call([{"type": "hacs/repository/download", "repository": rid, "version": "v0.1.0"}])[0]
print("download:", r.get("success"), r.get("error"))
for _ in range(20):
    repos = call([{"type": "hacs/repositories/list"}])[0]["result"]
    x = [y for y in repos if y["id"] == rid][0]
    if x.get("installed"):
        print("installed:", {k: x.get(k) for k in ("installed_version", "status", "pending_restart")}); break
    time.sleep(3)
issues = call([{"type": "repairs/list_issues"}])[0]["result"]["issues"]
print("restart repairs:", [i["issue_id"] for i in issues if "restart" in i["issue_id"]])
