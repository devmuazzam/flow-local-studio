#!/usr/bin/env python3
"""Start/stop workspace-local services; never changes system services."""
from __future__ import annotations
import json
import os
from pathlib import Path
import secrets
import signal
import subprocess
import sys
import time
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parent.parent
STATE = ROOT / ".local"
STATE.mkdir(exist_ok=True, mode=0o700)


def env():
    value = os.environ.copy()
    node = ROOT / ".runtime/node/node_modules/.bin"
    value["PATH"] = str(node) + os.pathsep + value["PATH"]
    key = STATE / "n8n-encryption-key"
    if not key.exists():
        key.write_text(secrets.token_hex(32))
        key.chmod(0o600)
    value.update({
        "N8N_USER_FOLDER": str(STATE / "n8n"), "N8N_ENCRYPTION_KEY": key.read_text().strip(),
        "N8N_HOST": "127.0.0.1", "N8N_LISTEN_ADDRESS": "127.0.0.1", "N8N_PORT": "5678",
        "N8N_PROTOCOL": "http", "N8N_SECURE_COOKIE": "false",
        "N8N_EDITOR_BASE_URL": "http://127.0.0.1:5678", "N8N_WEBHOOK_URL": "http://127.0.0.1:5678/",
        "GENERIC_TIMEZONE": "Asia/Karachi", "TZ": "Asia/Karachi",
        "N8N_DIAGNOSTICS_ENABLED": "false", "N8N_VERSION_NOTIFICATIONS_ENABLED": "false",
        "N8N_TEMPLATES_ENABLED": "false", "N8N_PERSONALIZATION_ENABLED": "false",
        "N8N_BLOCK_ENV_ACCESS_IN_NODE": "true", "EXECUTIONS_DATA_PRUNE": "true",
        "N8N_PYTHON_ENABLED": "false", "N8N_UNVERIFIED_PACKAGES_ENABLED": "false",
        "EXECUTIONS_DATA_MAX_AGE": "168", "N8N_LOG_LEVEL": "warn",
    })
    return value


def alive(pid):
    try:
        os.kill(pid, 0)
        command = Path(f"/proc/{pid}/cmdline").read_bytes()
        cwd = Path(f"/proc/{pid}/cwd").resolve()
        worker = str(ROOT).encode() in command and b"flow_local.app:app" in command
        n8n = cwd == ROOT and (b"n8n/bin/n8n" in command or command.strip(b"\x00 ").startswith(b"n8n start"))
        return worker or n8n
    except (ProcessLookupError, FileNotFoundError):
        return False


def start(name, command, url):
    pidfile = STATE / (name + ".pid")
    if pidfile.exists() and alive(int(pidfile.read_text())):
        print(f"{name} already running: {url}")
        return
    with (STATE / (name + ".log")).open("a") as log:
        process = subprocess.Popen(command, cwd=ROOT, env=env(), stdout=log, stderr=log, start_new_session=True)
    pidfile.write_text(str(process.pid))
    for _ in range(60):
        if process.poll() is not None:
            raise SystemExit(f"{name} stopped. See {STATE / (name + '.log')}")
        try:
            with urlopen(url, timeout=1):
                print(f"{name} ready: {url}")
                return
        except Exception:
            time.sleep(0.5)
    print(f"{name} starting; see {STATE / (name + '.log')}")


def stop(name):
    pidfile = STATE / (name + ".pid")
    if pidfile.exists():
        pid = int(pidfile.read_text())
        if alive(pid):
            os.killpg(pid, signal.SIGTERM)
            for _ in range(150):
                if not alive(pid):
                    break
                time.sleep(.1)
            else:
                raise SystemExit(f"{name} is still finishing its current task. Wait, then retry.")
            print(f"Stopped {name}.")
        pidfile.unlink()


def main():
    action = sys.argv[1] if len(sys.argv) > 1 else "start"
    n8n = ROOT / ".runtime/n8n/node_modules/n8n/bin/n8n"
    node = ROOT / ".runtime/node/node_modules/node/bin/node"
    if action == "start":
        start("worker", [str(ROOT / ".venv/bin/python"), "-m", "uvicorn", "flow_local.app:app", "--host", "127.0.0.1", "--port", "8787"], "http://127.0.0.1:8787/health")
        if not n8n.exists():
            raise SystemExit("Worker ready, but n8n is not installed. Run scripts/setup.sh")
        start("n8n", [str(node), str(n8n), "start"], "http://127.0.0.1:5678/healthz")
        print("Open http://127.0.0.1:8787 for Flow Local Studio.")
    elif action == "stop":
        for name in ["n8n", "worker"]:
            stop(name)
    elif action == "import":
        n8n_pid = STATE / "n8n.pid"
        was_running = n8n_pid.exists() and alive(int(n8n_pid.read_text()))
        if was_running:
            stop("n8n")
        subprocess.run([str(ROOT / ".venv/bin/python"), "scripts/n8n_workflows.py"], cwd=ROOT, check=True)
        for kind in ["credentials", "workflow"]:
            subprocess.run([str(node), str(n8n), "import:" + kind, "--input", str(STATE / ("n8n-" + kind + ".json"))], cwd=ROOT, env=env(), check=True)
        for ident in ["flowLocalPlan", "flowLocalRender"]:
            subprocess.run([str(node), str(n8n), "publish:workflow", "--id=" + ident], cwd=ROOT, env=env(), check=True)
        if was_running:
            start("n8n", [str(node), str(n8n), "start"], "http://127.0.0.1:5678/healthz")
        print("Imported and published the two local workflows and their local credential.")
    else:
        raise SystemExit("Usage: runtime.py start|stop|import")


if __name__ == "__main__":
    main()
