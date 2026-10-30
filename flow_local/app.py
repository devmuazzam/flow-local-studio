from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
import hmac
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import threading
from urllib.error import URLError, HTTPError
from urllib.request import Request, urlopen

from fastapi import FastAPI, HTTPException, Request as WebRequest
from fastapi.responses import FileResponse, HTMLResponse
from . import core
from .planner import demo_plan, plan_story

TOKEN_PATH = core.DATA / "api-token"
if not TOKEN_PATH.exists():
    TOKEN_PATH.write_text(secrets.token_hex(32))
TOKEN_PATH.chmod(0o600)
TOKEN = TOKEN_PATH.read_text().strip()
POOL = ThreadPoolExecutor(max_workers=4)
FUTURES = {}
TASK_LOCK = threading.Lock()


@asynccontextmanager
async def lifespan(app):
    core.initialize()
    yield


app = FastAPI(title="Flow Local Studio", lifespan=lifespan)


@app.middleware("http")
async def local_auth(request: WebRequest, call_next):
    # Browsers receive the local token in the app; arbitrary websites cannot read it.
    if request.url.path.startswith("/api/"):
        supplied = request.headers.get("x-local-token", "")
        if request.url.path.startswith("/api/files/"):
            supplied = supplied or request.cookies.get("flow_local_token", "")
        if not hmac.compare_digest(supplied, TOKEN):
            return HTMLResponse("Local authentication required", status_code=401)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Frame-Options"] = "DENY"
    return response


@app.exception_handler(ValueError)
async def value_error(request, exc):
    from fastapi.responses import JSONResponse
    return JSONResponse({"detail": str(exc)}, status_code=400)


@app.get("/", response_class=HTMLResponse)
def index():
    text = (core.ROOT / "flow_local/index.html").read_text().replace("__LOCAL_TOKEN__", TOKEN)
    response = HTMLResponse(text)
    response.set_cookie("flow_local_token", TOKEN, httponly=True, samesite="strict")
    return response


@app.get("/health")
def health():
    return {"ok": True, "service": "flow-local", "generation": "Google Flow over your browser", "storage": "local"}


@app.get("/api/system")
def system():
    models = []
    model_error = None
    try:
        with urlopen("http://127.0.0.1:11434/api/tags", timeout=5) as response:
            models = [m["name"] for m in json.load(response)["models"]]
    except Exception as exc:
        model_error = str(exc)
    n8n = False
    try:
        with urlopen("http://127.0.0.1:5678/healthz", timeout=3) as response:
            n8n = response.status == 200
    except Exception:
        pass
    return {"gflow": core.GFLOW.exists(), "ffmpeg": bool(shutil.which("ffmpeg")),
            "ffprobe": bool(shutil.which("ffprobe")), "ollama_models": models,
            "ollama_error": model_error, "n8n": n8n}


@app.get("/api/stories")
def stories():
    return core.list_stories()


@app.post("/api/stories")
def create(spec: dict):
    story = core.create_story(spec)
    return {**story, "estimate": core.estimate(story["spec"])}


@app.get("/api/stories/{story_id}")
def story(story_id: str):
    value = core.get_story(story_id)
    value["estimate"] = core.estimate(value["spec"])
    return value


def submit(key, callback, *args):
    with TASK_LOCK:
        if key in FUTURES and not FUTURES[key].done():
            raise ValueError("This task is still running.")
        FUTURES[key] = POOL.submit(callback, *args)


@app.post("/api/stories/{story_id}/plan")
def plan(story_id: str, options: dict):
    value = core.get_story(story_id)
    if value["status"] not in {"draft", "ready", "plan_failed"}:
        raise ValueError("Story cannot be planned while another stage is active.")
    if options.get("demo"):
        return core.save_plan(story_id, demo_plan(value["spec"]["minutes"]))
    core.update_story(story_id, status="planning", error=None)
    submit(story_id, plan_story, story_id)
    return core.get_story(story_id)


@app.put("/api/stories/{story_id}/plan")
def import_plan(story_id: str, plan: dict):
    with TASK_LOCK:
        if story_id in FUTURES and not FUTURES[story_id].done():
            raise ValueError("Wait for the current task to finish before editing.")
    return core.save_plan(story_id, plan)


@app.post("/api/stories/{story_id}/run")
def run(story_id: str, options: dict):
    profile = options.get("profile", "pro")
    simulate = options.get("simulate", False)
    if type(simulate) is not bool:
        raise ValueError("simulate must be true or false")
    max_new = options.get("max_new_clips", 0)
    if type(max_new) is not int or not 0 <= max_new <= 180:
        raise ValueError("max_new_clips must be 0–180 (0 means the complete remaining story)")
    if not simulate:
        with core.connect() as db:
            profile_state = db.execute("SELECT status FROM profiles WHERE name=?", (profile,)).fetchone()
        if not profile_state or profile_state[0] != "ready":
            raise ValueError("Connect the selected Google account before real generation.")
    with TASK_LOCK:
        if story_id in FUTURES and not FUTURES[story_id].done():
            raise ValueError("This story is already active.")
        value = core.reserve_run(story_id, profile, options.get("budget", 0), simulate, max_new)
        FUTURES[story_id] = POOL.submit(core.run_story, story_id, max_new)
    return value


@app.post("/api/stories/{story_id}/pause")
def pause(story_id: str):
    value = core.get_story(story_id)
    if value["status"] not in {"queued", "generating"}:
        raise ValueError("Only queued or generating stories can be paused.")
    core.update_story(story_id, status="paused")
    return {"message": "Pause requested. Any current generation will finish and be saved first."}


@app.post("/api/stories/{story_id}/recover")
def recover(story_id: str, options: dict):
    return core.recover_scene(story_id, int(options["episode"]), int(options["scene"]),
        media_id=options.get("media_id"), confirm_not_submitted=options.get("confirm_not_submitted") is True)


@app.get("/api/profiles")
def profiles():
    with core.connect() as db:
        return [dict(r) for r in db.execute("SELECT * FROM profiles ORDER BY name")]


def login_worker(name, account):
    try:
        with core.browser_lock():
            args = ["auth", "login", "--profile", name, "--browser", "chrome"]
            if account:
                args += ["--account", account]
            result = core.call_gflow(args, timeout=660)
            if result.returncode:
                raise ValueError(f"Login stopped (exit {result.returncode}). Complete sign-in in the dedicated Chrome window and try again.")
        with core.connect() as db:
            db.execute("UPDATE profiles SET status='ready',error=NULL WHERE name=?", (name,))
    except Exception as exc:
        with core.connect() as db:
            db.execute("UPDATE profiles SET status='needs_login',error=? WHERE name=?", (str(exc), name))


@app.post("/api/profiles/login")
def login(options: dict):
    name = core.check_profile(options.get("name", "pro"))
    account = options.get("email", "").strip()
    if account and ("@" not in account or len(account) > 254):
        raise ValueError("Enter a valid account email or leave it blank.")
    with core.connect() as db:
        row = db.execute("SELECT status FROM profiles WHERE name=?", (name,)).fetchone()
        if row and row[0] == "logging_in":
            raise ValueError("Login is already running for this profile.")
        db.execute("INSERT INTO profiles(name,status) VALUES(?,'logging_in') ON CONFLICT(name) DO UPDATE SET status='logging_in',error=NULL", (name,))
    submit("login:" + name, login_worker, name, account)
    return {"name": name, "status": "logging_in", "message": "Complete Google sign-in in the dedicated Chrome window."}


@app.get("/api/profiles/{name}/credits")
def credits(name: str):
    core.check_profile(name)
    with core.browser_lock():
        result = core.call_gflow(["credits", "user", "--profile", name, "--json"], timeout=180)
    if result.returncode:
        raise ValueError(f"Credit check failed (exit {result.returncode}). Reconnect the profile if needed.")
    return core.json_stdout(result)


@app.get("/api/files/{filename:path}")
def files(filename: str):
    path = (core.DATA / filename).resolve()
    exports = (core.DATA / "stories").resolve()
    if not path.is_relative_to(exports) or not path.is_file() or path.suffix not in {".mp4", ".json"}:
        raise HTTPException(404)
    return FileResponse(path)


@app.post("/api/n8n/{action}")
def n8n(action: str, options: dict):
    if action not in {"plan", "render"}:
        raise ValueError("Unknown workflow")
    req = Request(f"http://127.0.0.1:5678/webhook/flow-local-{action}",
        json.dumps(options).encode(), {"Content-Type": "application/json", "X-Local-Token": TOKEN})
    try:
        with urlopen(req, timeout=30) as response:
            return {"accepted": response.status == 200, "workflow": action}
    except HTTPError as exc:
        raise ValueError(f"n8n webhook returned {exc.code}. Import and publish the local workflows first.") from None
    except URLError:
        raise ValueError("n8n is not reachable. Start it with ./scripts/start.sh") from None
