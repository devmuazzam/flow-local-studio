# Local setup verification — 2026-10-30

Installed and running on this machine:

- Studio/worker: `http://127.0.0.1:8787`
- n8n Community: `http://127.0.0.1:5678`, version 2.45.7
- Workspace-local Node.js: 24.22.2
- Workspace-local gflow-cli: 0.92.0
- Existing local Ollama service with downloaded `qwen2.5:3b`
- Existing FFmpeg and ffprobe 7.0.2

Verified:

- Nine automated tests pass: long-story shape and budgets, profile/plan validation, exclusive browser lock, uncertain-submission behavior, restart recovery, a one-clip pilot, and multi-episode exports in both orientations.
- Two-episode synthetic stories contain 12 ten-second clips; each episode is 60 seconds and the complete story is 120 seconds. Shot order, audio presence and orientation are checked.
- Both authenticated n8n workflows are imported and published.
- n8n → local Ollama planning completed and produced an editable six-shot storyboard.
- n8n → local media worker → FFmpeg export completed without calling Google.
- The saved demonstration episode and full-story export both report `60.000000` seconds via ffprobe.
- API requests without the local token return HTTP 401.
- The UI was opened and inspected, including the pilot control, completed clip links, export links and video playback.

Live generation verified:

- Google sign-in to dedicated profile `pro` completed.
- Three Omni 1.1 Flash clips generated through the account's agent-only Flow UI. Each live approval quoted exactly one video and 15 credits; total approved: 45 credits.
- All source clips are native 720 × 1280 with audio. The finished vertical story has exactly 30.000000 seconds of video and audio.
- The second clip carries through the prior video as its visual ingredient. The final adapter revision explicitly selects the newly uploaded ending image and rejects video ingredients. The final shot used the preceding clip's actual ending image, with its uploaded asset ID recorded in the audit.
- The fixed continuity bible and all previous shot prompts are included in each subsequent prompt. Actual generated ending poses were reviewed and used to refine the next pending shot.
- Modern opaque Flow media URLs are handled without inventing a Google media UUID. The first completed clip was recovered without submitting it again.
- Failed preparation attempts did not submit or approve a generation, and their local reservations were released. Submitted or uncertain requests are never automatically regenerated.
- Deliverables: `outputs/one-last-light-30s-720p.mp4`, complete submitted prompts in `outputs/one-last-light-prompts.md`, and a JSON manifest.

Limits:

- Google Flow browser automation remains unofficial and UI changes can require adapter updates. Live verification covers this account, date and three-clip story.
- The Studio's generic story form still defaults to the stock gflow adapter. The tested agent adapter, project and last-frame continuity are specified in the story JSON/API.
- Generation runs on Google's servers using subscription credits. Planning and assembly run locally.
- Visual reference and full context improve continuity, but do not provide deterministic motion, voices or pixel-perfect seams.

## Last Kingdom live render — 2026-10-31

Six distinct native 1280 × 720 Omni Flash clips were generated on the second connected account, with full preceding context and actual ending-frame references. Each subsequent prompt was adjusted from its observed preceding ending. The completed landscape film has exactly 60.000 seconds, stereo 48 kHz audio and no packet timestamp gaps. Local finishing balances sound levels and adds quiet procedural wind; the encoded video stream is unchanged. No audio gap below −50 dB lasting 250 ms was detected in the finished export.

Flow’s standard uploader intermittently failed before opening a file chooser. Existing uploaded ending images were selected by verified asset ID and name instead. Download-link failures were recovered from existing completed tiles; completed clips were not regenerated. Live `prepare_only` frame uploads for shots 5 and 6 recorded no submission and no approval. Nine regression tests pass. Operator recovery files and browser sessions remain in ignored `.local/`.
