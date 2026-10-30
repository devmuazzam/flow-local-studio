from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import time
import uuid

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("FLOW_LOCAL_DATA", ROOT / ".local")).resolve()
DATA.mkdir(parents=True, exist_ok=True)
DATA.chmod(0o700)
DB = DATA / "stories.sqlite3"
GFLOW = ROOT / ".venv/bin/gflow"


def connect():
    db = sqlite3.connect(DB, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    return db


def initialize():
    with connect() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS stories (
          id TEXT PRIMARY KEY, created REAL NOT NULL, status TEXT NOT NULL,
          spec TEXT NOT NULL, plan TEXT, error TEXT, run_profile TEXT,
          budget INTEGER, reserved INTEGER NOT NULL DEFAULT 0,
          simulate INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE IF NOT EXISTS scenes (
          story_id TEXT NOT NULL, episode INTEGER NOT NULL, scene INTEGER NOT NULL,
          status TEXT NOT NULL, prompt TEXT NOT NULL, path TEXT, media_id TEXT,
          error TEXT, PRIMARY KEY(story_id,episode,scene));
        CREATE TABLE IF NOT EXISTS profiles (
          name TEXT PRIMARY KEY, status TEXT NOT NULL, error TEXT);
        """)
        # A process restart is not evidence that a submitted generation failed.
        db.execute("UPDATE scenes SET status='uncertain', error='Worker restarted during submission; inspect Flow before retrying.' WHERE status='submitting'")
        db.execute("UPDATE stories SET status='needs_review', error='Worker restarted. Reconcile uncertain scenes before resuming.' WHERE status IN ('queued','generating','assembling')")
        db.execute("UPDATE stories SET status='plan_failed', error='Planning interrupted; retry planning.' WHERE status='planning'")
        db.execute("UPDATE profiles SET status='needs_login', error='Login interrupted.' WHERE status='logging_in'")


def check_profile(name: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,39}", name):
        raise ValueError("Account profile must be 1–40 letters, numbers, underscores or hyphens.")
    return name


def get_story(story_id: str):
    with connect() as db:
        row = db.execute("SELECT * FROM stories WHERE id=?", (story_id,)).fetchone()
        if not row:
            raise ValueError("Story not found")
        value = dict(row)
        value["spec"] = json.loads(value["spec"])
        value["plan"] = json.loads(value["plan"]) if value["plan"] else None
        value["scenes"] = [dict(r) for r in db.execute("SELECT * FROM scenes WHERE story_id=? ORDER BY episode,scene", (story_id,))]
    folder = DATA / "stories" / story_id
    value["outputs"] = [str(p.relative_to(DATA)) for p in sorted(folder.glob("episode-*.mp4"))]
    value["full_video"] = str((folder / "full-story.mp4").relative_to(DATA)) if (folder / "full-story.mp4").exists() else None
    return value


def list_stories():
    with connect() as db:
        ids = [r[0] for r in db.execute("SELECT id FROM stories ORDER BY created DESC")]
    return [get_story(i) for i in ids]


def update_story(story_id, **fields):
    allowed = {"status", "plan", "error", "run_profile", "budget", "reserved", "simulate"}
    if not fields or not fields.keys() <= allowed:
        raise ValueError("Invalid update")
    with connect() as db:
        db.execute(f"UPDATE stories SET {','.join(k+'=?' for k in fields)} WHERE id=?", (*fields.values(), story_id))


def create_story(spec: dict):
    minutes = spec.get("minutes", 1)
    if type(minutes) is not int or not 1 <= minutes <= 30:
        raise ValueError("Story length must be 1–30 whole minutes.")
    seconds = spec.get("seconds", minutes * 60)
    if type(seconds) is not int or not 10 <= seconds <= 1800 or seconds % 10:
        raise ValueError("Story duration must be 10–1800 seconds in ten-second increments.")
    minutes = math.ceil(seconds / 60)
    if spec.get("aspect", "9:16") not in {"9:16", "16:9"}:
        raise ValueError("Aspect must be 9:16 or 16:9")
    if spec.get("resolution", "flow-default") not in {"720p", "360p", "flow-default"}:
        raise ValueError("Resolution must be flow-default, 720p or 360p")
    idea = str(spec.get("idea", "")).strip()
    if not 5 <= len(idea) <= 12000:
        raise ValueError("Enter a story idea of 5–12000 characters.")
    normalized = {
        "idea": idea, "minutes": minutes, "seconds": seconds, "aspect": spec.get("aspect", "9:16"),
        "resolution": spec.get("resolution", "flow-default"), "model": "omni-flash",
        "ollama_model": str(spec.get("ollama_model", "qwen2.5:3b")),
        "style": str(spec.get("style", "Cinematic, coherent lighting and character design.")),
        "continuity": str(spec.get("continuity", "text")),
        "flow_project": spec.get("flow_project"),
        "adapter": str(spec.get("adapter", "gflow")),
    }
    ident = uuid.uuid4().hex[:16]
    with connect() as db:
        db.execute("INSERT INTO stories(id,created,status,spec) VALUES(?,?,?,?)", (ident, time.time(), "draft", json.dumps(normalized)))
    return get_story(ident)


def estimate(spec):
    per_clip = 7 if spec["resolution"] == "360p" else 15
    seconds = spec.get("seconds", spec["minutes"] * 60)
    return {"clips": seconds // 10, "seconds": seconds,
            "credits_per_clip": per_clip, "estimated_credits": seconds // 10 * per_clip,
            "note": "Planning estimate from Google's published rates. Verify current Flow pricing; retries cost extra."}


def episode_seconds(spec, episode):
    return min(60, spec.get("seconds", spec["minutes"] * 60) - (episode - 1) * 60)


def scene_prompt(story, scene):
    index = story["scenes"].index(scene)
    history = "\n\n".join(
        f"COMPLETED SHOT {i+1} (context only, do not replay): {s['prompt']}"
        for i, s in enumerate(story["scenes"][:index])) or "No previous clip; this is the opening shot."
    return (f"Generate exactly ONE 10-second {story['spec']['aspect']} video using Omni Flash.\n"
            f"STORY TITLE: {story['plan'].get('title', '')}\n"
            f"FIXED CONTINUITY BIBLE: {story['plan']['bible']}\n"
            f"VISUAL STYLE: {story['spec']['style']}\n"
            f"FULL PREVIOUS STORY CONTEXT:\n{history}\n\n"
            f"CURRENT SHOT {index+1} OF {len(story['scenes'])} — GENERATE ONLY THIS ACTION:\n{scene['prompt']}\n"
            "Begin at the exact ending state of the preceding shot. If an initial image is supplied, "
            "it is the actual preceding clip's ending frame: preserve its subject identity, pose, wardrobe, "
            "prop positions, composition and lighting before continuing the motion. Do not recap or repeat prior events. "
            "Maintain camera-axis and screen direction. One uninterrupted shot, no montage or internal cuts. "
            "No titles, captions, logos, end cards or fade to black. Keep sound natural and continuous; "
            "no generated music or dialogue unless this shot explicitly requests it.")


def save_plan(story_id, plan):
    story = get_story(story_id)
    if story["status"] not in {"draft", "planning", "ready", "plan_failed"}:
        raise ValueError("A story with submitted clips cannot be edited. Create a new story instead.")
    spec = story["spec"]
    if not isinstance(plan, dict) or not isinstance(plan.get("bible"), str) or not plan["bible"].strip():
        raise ValueError("Plan needs a nonempty story bible describing consistent characters and style.")
    episodes = plan.get("episodes", [])
    if len(episodes) != spec["minutes"]:
        raise ValueError(f"Plan must have {spec['minutes']} episodes.")
    for number, ep in enumerate(episodes, 1):
        expected = episode_seconds(spec, number) // 10
        if len(ep.get("scenes", [])) != expected:
            raise ValueError(f"Episode {number} must have exactly {expected} scenes (six for a full minute).")
        for index, scene in enumerate(ep["scenes"]):
            if not isinstance(scene.get("prompt"), str) or len(scene["prompt"].strip()) < 10:
                raise ValueError("Every scene needs a visual prompt of at least ten characters.")
            scene["duration"] = 10
            scene["start_second"] = index * 10
            scene["end_second"] = (index + 1) * 10
            # Timing comes from the manifest, never an LLM's inferred timecode.
            if re.search(r"\d{1,2}:\d{2}", str(scene.get("beat", ""))):
                scene["beat"] = f"Shot {index + 1}"
    with connect() as db:
        # Editing a ready plan must never erase existing generation provenance.
        if db.execute("SELECT count(*) FROM scenes WHERE story_id=? AND status!='pending'", (story_id,)).fetchone()[0]:
            raise ValueError("Submitted scenes cannot be replaced.")
        db.execute("DELETE FROM scenes WHERE story_id=?", (story_id,))
        for ei, ep in enumerate(episodes, 1):
            for si, scene in enumerate(ep["scenes"], 1):
                db.execute("INSERT INTO scenes(story_id,episode,scene,status,prompt) VALUES(?,?,?,'pending',?)", (story_id, ei, si, scene["prompt"]))
        db.execute("UPDATE stories SET plan=?,status='ready',error=NULL WHERE id=?", (json.dumps(plan), story_id))
    return get_story(story_id)


@contextlib.contextmanager
def browser_lock():
    with (DATA / "browser.lock").open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("The Flow browser is busy. Wait for generation, credits check, or login to finish.") from None
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def gflow_env():
    env = os.environ.copy()
    env.update(GFLOW_CLI_HOME=str(DATA / "flow-profiles"),
               GFLOW_CLI_DB_PATH=str(DATA / "flow-profiles/gflow.db"),
               GFLOW_CLI_OUTPUT_DIR=str(DATA / "downloads"),
               GFLOW_CLI_AGENT_CONFIRM="always", GFLOW_CLI_HISTORY_PROMPTS="store",
               GFLOW_CLI_INCIDENT_CAPTURE="false")
    # This installation stores outputs locally even if the shell has a cloud setting.
    env.pop("GFLOW_CLI_STORAGE_URI", None)
    return env


def call_gflow(args, timeout=1200):
    if not GFLOW.exists():
        raise ValueError("gflow-cli is missing. Run ./scripts/setup.sh")
    return subprocess.run([str(GFLOW), *map(str, args)], env=gflow_env(), cwd=ROOT,
                          text=True, capture_output=True, timeout=timeout, check=False)


def json_stdout(result):
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        return {"error": "CLI returned no valid JSON", "exit_code": result.returncode}


def scene_update(story_id, ep, scene, **fields):
    if not fields.keys() <= {"status", "path", "media_id", "error"}:
        raise ValueError("Invalid scene update")
    with connect() as db:
        db.execute(f"UPDATE scenes SET {','.join(k+'=?' for k in fields)} WHERE story_id=? AND episode=? AND scene=?", (*fields.values(), story_id, ep, scene))


def reserve_run(story_id, profile, budget, simulate, max_new_clips=0):
    check_profile(profile)
    story = get_story(story_id)
    if not story["plan"]:
        raise ValueError("Plan the story first.")
    if any(s["status"] in {"uncertain", "submitting"} for s in story["scenes"]):
        raise ValueError("Reconcile uncertain clips before resuming; no automatic resubmission.")
    if story["run_profile"] and bool(story["simulate"]) != simulate:
        raise ValueError("Demo and real generation cannot be mixed in one story. Create a new story.")
    # Preserve account ownership for the whole story, including recovery/downloads.
    if story["run_profile"] and story["run_profile"] != profile:
        raise ValueError("This story already belongs to another account. Create a new story to switch accounts.")
    if type(budget) is not int or budget < 0:
        raise ValueError("Credit budget must be a nonnegative whole number.")
    remaining = sum(s["status"] == "pending" for s in story["scenes"])
    if max_new_clips:
        remaining = min(remaining, max_new_clips)
    required = story["reserved"] + (0 if simulate else remaining * estimate(story["spec"])["credits_per_clip"])
    if not simulate and required > budget:
        raise ValueError(f"Budget {budget} is below the conservative run total {required} credits.")
    with connect() as db:
        changed = db.execute("UPDATE stories SET status='queued',run_profile=?,budget=?,simulate=?,error=NULL WHERE id=? AND status IN ('ready','needs_review','paused','complete')", (profile, budget, int(simulate), story_id)).rowcount
        if not changed:
            raise ValueError("Story already running, or not ready.")
    return get_story(story_id)


def probe(path: Path):
    r = subprocess.run(["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)], capture_output=True, text=True, timeout=60)
    if r.returncode:
        raise ValueError(f"Cannot decode video: {path.name}")
    return json.loads(r.stdout)


def validate_clip(path: Path, aspect: str, resolution=None):
    info = probe(path)
    video = next((s for s in info["streams"] if s["codec_type"] == "video"), None)
    duration = float(video.get("duration", info["format"].get("duration", 0))) if video else 0
    if not video or abs(duration - 10) > 0.25:
        raise ValueError(f"Expected a 10-second clip, got {duration:.3f}s: {path.name}")
    target = 9/16 if aspect == "9:16" else 16/9
    if abs(video["width"] / video["height"] - target) > 0.03:
        raise ValueError(f"Clip aspect ratio does not match {aspect}: {path.name}")
    if resolution in {"720p", "360p"} and min(video["width"], video["height"]) < int(resolution[:-1]):
        raise ValueError(f"Clip source is lower than the required {resolution}: {path.name}")
    return info


def ffmpeg(args):
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *map(str, args)], check=True, timeout=1200)


def dimensions(spec):
    width, height = (360, 640) if spec["resolution"] == "360p" else (720, 1280)
    return (width, height) if spec["aspect"] == "9:16" else (height, width)


def demo_clip(path, spec, episode, scene):
    width, height = dimensions(spec)
    colors = ["0x0f766e", "0x155e75", "0x1e40af", "0x6d28d9", "0x9d174d", "0x92400e"]
    # A tiny bitmap label keeps the demo independent of optional FFmpeg font filters.
    font = {
        "D": [30,17,17,17,17,17,30], "E": [31,16,16,30,16,16,31],
        "M": [17,27,21,21,17,17,17], "O": [14,17,17,17,17,17,14],
        "S": [15,16,16,14,1,1,30], " ": [0]*7,
        "0": [14,17,19,21,25,17,14], "1": [4,12,4,4,4,4,14],
        "2": [14,17,1,2,4,8,31], "3": [30,1,1,14,1,1,30],
        "4": [2,6,10,18,31,2,2], "5": [31,16,16,30,1,1,30],
        "6": [14,16,16,30,17,17,14], "7": [31,1,2,4,8,8,8],
        "8": [14,17,17,14,17,17,14], "9": [14,17,17,15,1,1,14],
    }
    text = f"DEMO E{episode} S{scene}"
    scale = max(1, min(width // (len(text)*6+2), height // 20))
    pixels = bytearray(bytes.fromhex(colors[scene-1][2:]) * (width*height))
    left, top = (width-len(text)*6*scale)//2, height//2-4*scale
    for index, char in enumerate(text):
        for y, bits in enumerate(font[char]):
            for x in range(5):
                if bits & (1 << (4-x)):
                    for dy in range(scale):
                        for dx in range(scale):
                            offset = ((top+y*scale+dy)*width+left+(index*6+x)*scale+dx)*3
                            pixels[offset:offset+3] = b"\xff\xff\xff"
    frame = path.with_suffix(".ppm")
    frame.write_bytes(f"P6\n{width} {height}\n255\n".encode() + pixels)
    ffmpeg(["-loop", "1", "-framerate", "24", "-i", frame,
            "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
            "-t", "10", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-movflags", "+faststart", path])
    frame.unlink()


def assemble(story_id):
    story = get_story(story_id)
    folder = DATA / "stories" / story_id
    folder.mkdir(parents=True, exist_ok=True)
    width, height = dimensions(story["spec"])
    episodes = []
    for ep in range(1, story["spec"]["minutes"] + 1):
        clips = [s for s in story["scenes"] if s["episode"] == ep]
        seconds = episode_seconds(story["spec"], ep)
        if len(clips) != seconds // 10 or any(s["status"] != "complete" for s in clips):
            raise ValueError(f"Episode {ep} has incomplete clips; assembly stopped.")
        normalized = []
        for s in clips:
            source = Path(s["path"])
            info = validate_clip(source, story["spec"]["aspect"], None if story["simulate"] else story["spec"]["resolution"])
            dest = folder / f"normalized-e{ep:02}-s{s['scene']:02}.mp4"
            args = ["-i", source]
            has_audio = any(v["codec_type"] == "audio" for v in info["streams"])
            if not has_audio:
                args += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
            args += ["-map", "0:v:0", "-map", "0:a:0" if has_audio else "1:a:0",
                     "-vf", f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=24,tpad=stop_mode=clone:stop_duration=0.25",
                     "-af", "aresample=48000,apad", "-t", "10", "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
                     "-pix_fmt", "yuv420p", "-c:a", "aac", "-ac", "2", "-ar", "48000", "-movflags", "+faststart", dest]
            ffmpeg(args)
            normalized.append(dest)
        output = folder / f"episode-{ep:02}.mp4"
        concat(normalized, output, seconds)
        if not seconds - .05 <= float(probe(output)["format"]["duration"]) <= seconds + .001:
            raise ValueError("Episode duration validation failed.")
        episodes.append(output)
    total_seconds = story["spec"].get("seconds", story["spec"]["minutes"] * 60)
    concat(episodes, folder / "full-story.mp4", total_seconds)
    if abs(float(probe(folder / "full-story.mp4")["format"]["duration"]) - total_seconds) > 0.5:
        raise ValueError("Full story duration validation failed.")
    (folder / "storyboard.json").write_text(json.dumps(story["plan"], indent=2), encoding="utf-8")
    # Keep original clips and final exports; normalized intermediates are reproducible.
    for p in folder.glob("normalized-*.mp4"):
        p.unlink()


def concat(paths, output, duration):
    # Use known internal file names; no shell or ffmpeg concat escaping of user paths.
    playlist = output.with_suffix(".concat.txt")
    if any("'" in str(p) or "\n" in str(p) for p in paths):
        raise ValueError("Export paths cannot contain a quote or newline.")
    playlist.write_text("".join(f"file '{p.resolve()}'\n" for p in paths), encoding="utf-8")
    temp = output.with_name(output.stem + ".partial.mp4")
    # Reset both streams before trimming; AAC priming otherwise shifts video by 21ms.
    ffmpeg(["-f", "concat", "-safe", "0", "-i", playlist, "-t", str(duration),
            "-c:v", "copy", "-bsf:v", "setts=pts=PTS-STARTPTS:dts=DTS-STARTPTS",
            "-af", f"asetpts=N/SR/TB,atrim=duration={duration}",
            "-c:a", "aac", "-ar", "48000", "-movflags", "+faststart", temp])
    temp.replace(output)


def run_story(story_id, max_new_clips=0):
    story = get_story(story_id)
    folder = DATA / "stories" / story_id
    folder.mkdir(parents=True, exist_ok=True)
    try:
        with browser_lock():
            if get_story(story_id)["status"] == "paused":
                return
            update_story(story_id, status="generating")
            new_clips = 0
            for scene in story["scenes"]:
                if get_story(story_id)["status"] == "paused":
                    return
                if scene["status"] == "complete":
                    validate_clip(Path(scene["path"]), story["spec"]["aspect"], None if story["simulate"] else story["spec"]["resolution"])
                    continue
                if scene["status"] != "pending":
                    raise ValueError("A clip needs reconciliation before generation can continue.")
                ep, si = scene["episode"], scene["scene"]
                output = folder / f"clip-e{ep:02}-s{si:02}.mp4"
                prompt = scene_prompt(story, scene)
                (folder / f"prompt-e{ep:02}-s{si:02}.txt").write_text(prompt, encoding="utf-8")
                charge = 0 if story["simulate"] else estimate(story["spec"])["credits_per_clip"]
                with connect() as db:
                    db.execute("BEGIN IMMEDIATE")
                    current = db.execute("SELECT reserved,budget FROM stories WHERE id=?", (story_id,)).fetchone()
                    if not story["simulate"] and current["reserved"] + charge > current["budget"]:
                        raise ValueError("Credit budget reached.")
                    db.execute("UPDATE stories SET reserved=reserved+? WHERE id=?", (charge, story_id))
                    db.execute("UPDATE scenes SET status='submitting',error=NULL WHERE story_id=? AND episode=? AND scene=?", (story_id, ep, si))
                pre_submit_refused = False
                try:
                    if story["simulate"]:
                        demo_clip(output, story["spec"], ep, si)
                    else:
                        seed_frame = None
                        index = story["scenes"].index(scene)
                        if index and story["spec"].get("continuity") == "last-frame":
                            previous = get_story(story_id)["scenes"][index-1]
                            seed_frame = folder / f"seed-before-e{ep:02}-s{si:02}.png"
                            ffmpeg(["-sseof", "-0.1", "-i", previous["path"], "-frames:v", "1", "-update", "1", seed_frame])
                            if not seed_frame.exists():
                                raise ValueError("Previous clip's ending frame could not be extracted.")
                        args = ["video", "i2v" if seed_frame else "t2v", prompt, "--model", "omni-flash", "--duration", "10",
                                "--aspect", story["spec"]["aspect"], "--count", "1", "--profile", story["run_profile"],
                                "--output", str(output), "--json"]
                        if seed_frame:
                            args += ["--initial-frame", str(seed_frame)]
                        if story["spec"].get("flow_project"):
                            args += ["--project", story["spec"]["flow_project"]]
                        # Agent-only accounts do not expose a resolution control to gflow.
                        # We request the exact resolution and stop if it cannot be verified.
                        if story["spec"]["resolution"] != "flow-default":
                            args += ["--resolution", story["spec"]["resolution"]]
                        if story['spec'].get('adapter') == 'flow-agent':
                            request_file = folder / f"request-e{ep:02}-s{si:02}.json"
                            result_file = folder / f"agent-e{ep:02}-s{si:02}.json"
                            request_file.write_text(json.dumps({
                                'prompt':prompt, 'profile':story['run_profile'],
                                'project_id':story['spec']['flow_project'], 'aspect':story['spec']['aspect'],
                                'resolution':story['spec']['resolution'], 'max_credits':charge,
                                'initial_frame':str(seed_frame) if seed_frame else None, 'output':str(output),
                            }, indent=2),encoding='utf-8')
                            log_file = folder / f"agent-e{ep:02}-s{si:02}.log"
                            with log_file.open('w') as log:
                                raw = subprocess.run([str(ROOT/'.venv/bin/python'),'-m','flow_local.agent_flow',str(request_file),str(result_file)],cwd=ROOT,env=gflow_env(),stdout=log,stderr=log,timeout=1200)
                            result = subprocess.CompletedProcess(raw.args,raw.returncode,result_file.read_text() if result_file.exists() else '{}','')
                        else:
                            result = call_gflow(args)
                        payload = json_stdout(result)
                        (folder / f"result-e{ep:02}-s{si:02}.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
                        media_id = payload.get("media_id")
                        if media_id:
                            scene_update(story_id, ep, si, media_id=media_id)
                        if result.returncode or not payload.get("succeeded"):
                            if story['spec'].get('adapter') == 'flow-agent' and payload.get('submitted') is False and payload.get('approved_credits') is None and payload.get('phase') in {'preparing','prepared'}:
                                with connect() as db:
                                    db.execute('UPDATE stories SET reserved=reserved-? WHERE id=?',(charge,story_id))
                                pre_submit_refused = True
                            raise ValueError(f"Flow stopped (exit {result.returncode}). Inspect Flow and result-e{ep:02}-s{si:02}.json before resuming.")
                        source = Path(payload.get("local_path") or output)
                        if source.resolve() != output.resolve():
                            shutil.copyfile(source, output)
                    info = validate_clip(output, story["spec"]["aspect"])
                    if not story["simulate"] and story["spec"]["resolution"] != "flow-default":
                        v = next(s for s in info["streams"] if s["codec_type"] == "video")
                        if min(v["width"], v["height"]) < (720 if story["spec"]["resolution"] == "720p" else 360):
                            raise ValueError("Generated resolution is lower than requested. Clip retained for review.")
                    scene_update(story_id, ep, si, status="complete", path=str(output), error=None)
                    new_clips += 1
                    if max_new_clips and new_clips >= max_new_clips and any(s["status"] == "pending" for s in get_story(story_id)["scenes"]):
                        update_story(story_id, status="paused", error="Test clip saved. Review it before resuming the story.")
                        return
                except BaseException:
                    scene_update(story_id, ep, si, status="pending" if pre_submit_refused else "uncertain", error="Pre-submit preparation failed; nothing sent or approved." if pre_submit_refused else "Submission outcome requires review; never automatically regenerated.")
                    raise
            update_story(story_id, status="assembling")
            assemble(story_id)
            update_story(story_id, status="complete", error=None)
    except Exception as exc:
        update_story(story_id, status="needs_review", error=str(exc))


def recover_scene(story_id, ep, si, media_id=None, confirm_not_submitted=False):
    story = get_story(story_id)
    if story["status"] in {"queued", "generating", "assembling", "planning"}:
        raise ValueError("Wait for the active run to stop.")
    scene = next((s for s in story["scenes"] if s["episode"] == ep and s["scene"] == si), None)
    if not scene or scene["status"] != "uncertain":
        raise ValueError("Only uncertain clips can be reconciled.")
    folder = DATA / "stories" / story_id
    output = folder / f"clip-e{ep:02}-s{si:02}.mp4"
    with browser_lock():
        valid_local = False
        if output.exists():
            try:
                validate_clip(output, story["spec"]["aspect"], None if story["simulate"] else story["spec"]["resolution"])
                valid_local = True
            except ValueError:
                if not (media_id or scene["media_id"] or confirm_not_submitted):
                    raise
                output.replace(output.with_name(output.stem + f".rejected-{uuid.uuid4().hex[:6]}.mp4"))
        if valid_local:
            scene_update(story_id, ep, si, status="complete", path=str(output), error=None)
        elif media_id or scene["media_id"]:
            ident = media_id or scene["media_id"]
            if not re.fullmatch(r"[A-Za-z0-9_-]{6,100}", ident):
                raise ValueError("Invalid Flow media ID")
            r = call_gflow(["data", "download", ident, "--profile", story["run_profile"], "--out", str(folder), "--json"])
            payload = json_stdout(r)
            if r.returncode:
                raise ValueError("Existing clip download failed. No generation was attempted.")
            candidate = payload.get("local_path") or payload.get("path")
            if not candidate:
                matches = [p for p in folder.glob("*.mp4") if ident in p.name]
                candidate = str(matches[0]) if len(matches) == 1 else None
            if not candidate:
                raise ValueError("Download completed but file could not be matched. Inspect downloads manually.")
            source = Path(candidate)
            validate_clip(source, story["spec"]["aspect"], None if story["simulate"] else story["spec"]["resolution"])
            if source.resolve() != output.resolve():
                shutil.copyfile(source, output)
            scene_update(story_id, ep, si, status="complete", path=str(output), media_id=ident, error=None)
        elif confirm_not_submitted:
            # Keep the credit reservation: an operator assertion is not billing proof.
            scene_update(story_id, ep, si, status="pending", error=None)
        else:
            raise ValueError("Provide the existing media ID, or confirm in Flow that nothing was submitted.")
    return get_story(story_id)
