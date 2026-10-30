# Flow Local Studio

A local n8n + Python workflow for your existing Google AI Pro account. Ollama plans a full story, Google Flow generates **Omni Flash 10-second shots**, and FFmpeg joins six shots into each one-minute episode. It also exports the complete story as one file.

The automation software has no subscription or per-call fee. **Video generation still happens on Google's servers and consumes your Flow credits.** Local planning, orchestration, account profiles, storage, and video assembly run on this computer. This is not offline video generation.

## Start here

This workspace has already been installed, and both local n8n workflows are published. Open the Studio, finish your local n8n owner login when you want to inspect workflows, and connect your Google Flow account. The steps below also explain a fresh setup.

```bash
cd /home/codingcops/Documents/ChatGPT/Random
./scripts/start.sh
```

- Studio: <http://127.0.0.1:8787>
- n8n: <http://127.0.0.1:5678>

Both bind to localhost. Keep the desktop awake while running; Chrome needs a graphical desktop session.

1. Open n8n and create your **local owner login**. This is a local account, not an n8n Cloud subscription. No paid license is required for this workflow.
2. Import the generated workflows and local credential:

   ```bash
   ./scripts/import-n8n.sh
   ```

3. The import script publishes both **Flow Local** workflows and restarts local n8n if it was running. To import through the UI instead, use `.local/n8n-workflow.json`, configure the `Flow Local token` HTTP Header Auth credential, and publish both workflows. The generated credential is in `.local/n8n-credentials.json`; do not share this file.
4. In Studio, enter an account profile name, such as `pro`, and select **Connect Google Flow**. Sign in yourself in the dedicated Chrome window. Account email is optional; when provided, gflow checks the signed-in identity against it. Passwords and verification codes belong only in Google's sign-in window.
5. Add other accounts under different names, then choose a profile before starting a new story. Profiles stay separate; account switching is explicit. A story stays assigned to its original account after it starts, so recovery always uses the right account.
6. Set the story idea, duration in seconds, aspect ratio, source resolution, visual style, and local Ollama model.
7. Select **Create draft**, then **Plan through n8n**. Review the complete storyboard and edit its JSON if needed.
8. Set a credit ceiling and select **Generate through n8n**. Download the episode files and full story when complete.

**Free assembly demo** creates labelled synthetic clips and assembles them without accessing Google. Use a separate story for demo and real generation. Demo storyboards repeat the same six beats across episodes; use Ollama or an imported plan for a real long story.

## Structure and timing

```text
Story idea + orientation + duration + account
  → local Ollama outline and continuity bible
  → N episode storyboards, six chronological shots each
  → one local browser worker, 10-second Omni Flash clips
  → validate each source clip's duration and aspect ratio
  → normalize resolution, 24 fps, stereo AAC and H.264
  → episode-01.mp4 … episode-N.mp4 (60 seconds each)
  → full-story.mp4 (N × 60 seconds)
```

A 10-minute story is 10 episodes and 60 shots. Both 9:16 vertical and 16:9 landscape are supported. The interface accepts 10–1800 seconds in ten-second increments. A final partial episode contains the remaining shots (for example, 30 seconds contains three). Assembly uses cuts, preserving the exact timing; crossfades would shorten it.

The continuity bible and complete prior shot prompts are included in every new shot prompt. The `last-frame` continuity mode also uploads the actual preceding clip’s ending frame. Independent text-to-video clips do **not** guarantee identical faces, voices, or seamless camera motion. Review each episode. This version deliberately does not use gflow's `video chain`, which currently rejects Omni Flash and 10-second links.

## Free components and credit estimates

Installed into the workspace:

- `gflow-cli==0.92.0` and its pinned Python dependencies in `.venv`.
- Node.js 24 and `n8n==2.45.7` in `.runtime`.
- The Python worker, SQLite queue, local Studio, and FFmpeg assembly.
- `qwen2.5:3b` downloaded to the existing Ollama service for local story planning.

Your existing Google AI Pro subscription remains required. There is no useapi.net account, paid text API, n8n Cloud account, cloud bucket, or external cookie-sharing service.

Google's published 10-second Omni Flash costs checked on 2026-10-30:

| Source | Per shot | One-minute episode | Ten-minute story |
|---|---:|---:|---:|
| Native 720p | 15 credits | 90 credits | 900 credits |
| Native 360p | 7 credits | 42 credits | 420 credits |

These are **estimates**, not a live price quote or guarantee of actual billing. The ceiling limits local reservations using those rates. Failed/uncertain attempts retain their reservation, and explicitly retrying them reserves more. Current Flow pricing and your actual credit balance remain authoritative. Count is fixed at one output per request. The agent adapter checks the live approval quote and rejects unclear or excessive charges before approving. Proven preparation failures before submission release their local reservation.

**Flow default** skips an explicit resolution control for compatibility with agent-only account interfaces and reserves at the 720p rate. Its exports are 720p, but if Flow returned 360p the source was upscaled locally; this does not create native 720p detail. **Require native 720p / 360p** requests the setting explicitly and stops when gflow cannot verify it in your account's UI. Source files are retained.

## Recovery and account handling

- SQLite records the submission before the generation command starts.
- A file lock allows only one browser action at a time, including login and credit checks.
- On restart, in-flight submissions become **uncertain**. They are never automatically resubmitted, regardless of a CLI `retryable` flag.
- A successful clip is validated and skipped on resume.
- After a failure, use **Reconcile clip**. Recover the saved local clip, or provide the existing Flow media ID to download it without generating again. Enter `NOT_SUBMITTED` only after verifying in Flow that no generation was submitted. The earlier credit reservation is retained conservatively.
- If Google asks you to sign in again or presents a CAPTCHA, complete it yourself in Chrome. The pipeline does not add CAPTCHA-solving services or rotate accounts to evade a limit.
- **Pause after current clip** finishes and saves an in-flight clip before pausing. It does not cancel a paid generation.

Google Flow automation is unofficial and its UI can change. Installed CLI smoke checks and synthetic assembly tests do not prove your Google account's live generation works. Use **Generate 1 test clip** first: it saves one shot and automatically pauses, so you can review it using **View clip** before resuming the full story.

## Files, services, and backups

```text
flow_local/             Worker, storyboard planner, assembly and local UI
scripts/                Setup, start/stop, n8n workflow generator/import
tests/                  Timing, orientation, restart, lock and budget checks
requirements.lock       Exact installed Python dependency versions
.venv/                  Local Python environment (ignored by Git)
.runtime/               Local Node.js and n8n installation (ignored)
.local/stories.sqlite3  Story and scene state
.local/stories/<id>/    Original clips, per-scene results, plans, episode exports
.local/flow-profiles/   Dedicated signed-in Google profiles (sensitive)
.local/n8n/             Local n8n database and workflows
.local/*.log            Worker and n8n service logs
```

`.local`, `.runtime`, and `.venv` are ignored by Git. Protect `.local`: it contains browser sessions and local credentials. For backup, stop services and back up `.local` including the n8n encryption key. Do not send browser profiles or credential JSON files to other people.

```bash
./scripts/stop.sh
./scripts/start.sh
.venv/bin/python -m unittest discover -s tests -v
```

Stopping during a submitted shot makes its outcome uncertain on restart. Wait for generation to stop or use Pause first when possible. The desktop services do not automatically start at system boot.

Fresh installation on this Linux machine:

```bash
./scripts/setup.sh
ollama pull qwen2.5:3b
./scripts/start.sh
```

Prerequisites: Python 3.11+, uv, npm, FFmpeg/ffprobe, Google Chrome, an active desktop display, and Ollama listening locally on port 11434. Setup downloads free packages; the first model download is approximately 2 GB. System Python and Node installations are not replaced.

## n8n workflows

`scripts/n8n_workflows.py` generates two authenticated webhook workflows with HTTP Request, Wait, IF and No Operation nodes:

- `Flow Local — Plan story locally`: receives a story ID, starts local Ollama planning, and polls until ready or failed.
- `Flow Local — Render episodes`: receives a story ID, profile, budget and demo flag; starts the worker and polls until complete, paused, or needing review.

The Studio submits to n8n through a local proxy. The worker API requires the generated `X-Local-Token` credential. Tokens are stored only in ignored local files and n8n's local database. The credential is not a Google token. Importing again keeps stable workflow and credential IDs, publishes the imported versions, and restarts local n8n if it was running.

## Primary references

- [Google Flow credits, lengths and model costs](https://support.google.com/flow/answer/16526234?hl=en)
- [gflow-cli repository](https://github.com/ffroliva/gflow-cli)
- [gflow usage and Omni Flash limitations](https://github.com/ffroliva/gflow-cli/blob/develop/docs/USAGE.md)
- [Current account-interface compatibility issues](https://github.com/ffroliva/gflow-cli/blob/develop/KNOWN_ISSUES.md)
- [n8n npm installation](https://docs.n8n.io/hosting/installation/npm/)

## Live agent-only Flow compatibility

`flow_local/agent_flow.py` supports the account’s new agent composer using the signed-in local browser. The live story uses `adapter: "flow-agent"`, its existing `flow_project` ID, and `continuity: "last-frame"`. It requests one native 720p, ten-second Omni Flash clip, checks the live 15-credit approval, attaches the prior ending frame for subsequent shots, and downloads the resulting playable tile even when Flow uses an opaque media URL. The generic gflow adapter remains available for accounts with its supported composer. These spec options are currently set through the local API/story JSON; the Studio form does not yet select the adapter or project.

A seed frame is a visual starting reference, not a deterministic numeric random seed. Flow may still vary motion or character details. The original clips, exact prompt text, attachment frames, and approval audit remain in `.local/stories/<id>/`.

The agent adapter accepts `prepare_only: true` in its internal request JSON to upload and select an ending image without submitting or approving a generation. Its audit records `submitted: false` and `phase: prepared`. On accounts exposing a standard composer, it can open the built-in Agent mode and dismiss its onboarding notice.
