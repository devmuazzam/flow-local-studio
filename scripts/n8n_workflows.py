#!/usr/bin/env python3
"""Build portable workflows; put the local credential only in ignored .local files."""
import json
from pathlib import Path
import secrets
import uuid

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / ".local"
STATE.mkdir(exist_ok=True, mode=0o700)
token_path = STATE / "api-token"
if not token_path.exists():
    token_path.write_text(secrets.token_hex(32))
    token_path.chmod(0o600)
token = token_path.read_text().strip()
CREDENTIAL = {"id": "flowLocalHeaderAuth", "name": "Flow Local token"}


def node(name, kind, version, parameters, x, y=0, **extra):
    return {"id": str(uuid.uuid5(uuid.NAMESPACE_DNS, "flow-local:" + name)), "name": name,
            "type": "n8n-nodes-base." + kind, "typeVersion": version,
            "position": [x, y], "parameters": parameters, **extra}


def workflow(action):
    title = "Flow Local — " + ("Plan story locally" if action == "plan" else "Render episodes")
    hook = node("Receive " + action, "webhook", 2, {
        "httpMethod": "POST", "path": "flow-local-" + action,
        "authentication": "headerAuth", "responseMode": "responseNode", "options": {},
    }, 0, webhookId="flow-local-" + action, credentials={"httpHeaderAuth": CREDENTIAL})
    start = node("Start " + action, "httpRequest", 4.2, {
        "method": "POST", "url": "=http://127.0.0.1:8787/api/stories/{{ $json.body.story_id }}/" + ("plan" if action == "plan" else "run"),
        "authentication": "genericCredentialType", "genericAuthType": "httpHeaderAuth",
        "sendBody": True, "specifyBody": "json",
        "jsonBody": "={{ " + ("{}" if action == "plan" else "{profile: $json.body.profile, budget: Number($json.body.budget), simulate: $json.body.simulate === true, max_new_clips: Number($json.body.max_new_clips || 0)}") + " }}",
        "options": {"timeout": 30000},
    }, 260, credentials={"httpHeaderAuth": CREDENTIAL})
    respond = node("Acknowledge started job", "respondToWebhook", 1.4,
        {"respondWith": "json", "responseBody": "={{ $json }}", "options": {}}, 500)
    wait = node("Wait 20 seconds", "wait", 1.1, {"amount": 20, "unit": "seconds"}, 740, webhookId="flow-local-wait-" + action)
    status = node("Check progress", "httpRequest", 4.2, {
        "url": "=http://127.0.0.1:8787/api/stories/{{ $('" + start["name"] + "').first().json.id }}",
        "authentication": "genericCredentialType", "genericAuthType": "httpHeaderAuth", "options": {"timeout": 30000},
    }, 980, credentials={"httpHeaderAuth": CREDENTIAL})
    condition = node("Still working?", "if", 2.2, {
        "conditions": {"options": {"caseSensitive": True, "leftValue": "", "typeValidation": "strict", "version": 2},
            "conditions": [{"id": "active", "leftValue": "={{ ['planning','queued','generating','assembling'].includes($json.status) }}", "rightValue": True,
                "operator": {"type": "boolean", "operation": "true", "singleValue": True}}], "combinator": "and"}, "options": {},
    }, 1220)
    done = node("Finished or needs review", "noOp", 1, {}, 1460, 120)
    nodes = [hook, start, respond, wait, status, condition, done]
    connections = {}
    for a, b in [(hook, start), (start, respond), (respond, wait), (wait, status), (status, condition)]:
        connections[a["name"]] = {"main": [[{"node": b["name"], "type": "main", "index": 0}]]}
    connections[condition["name"]] = {"main": [[{"node": wait["name"], "type": "main", "index": 0}], [{"node": done["name"], "type": "main", "index": 0}]]}
    return {"id": "flowLocal" + action.capitalize(), "name": title, "active": False, "nodes": nodes, "connections": connections,
            "settings": {"executionOrder": "v1", "timezone": "Asia/Karachi", "saveDataSuccessExecution": "all", "executionTimeout": 86400},
            "versionId": str(uuid.uuid4()), "tags": []}


credentials = [{"id": CREDENTIAL["id"], "name": CREDENTIAL["name"], "type": "httpHeaderAuth", "data": {"name": "X-Local-Token", "value": token}}]
for name, value in [("credentials", credentials), ("workflow", [workflow("plan"), workflow("render")])]:
    target = STATE / ("n8n-" + name + ".json")
    target.write_text(json.dumps(value, indent=2))
    target.chmod(0o600)
print("Created two local n8n workflows and their local header credential.")
