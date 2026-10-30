# Minute 1 quality review — 2026-10-31

The user found this minute less smooth and lower quality than the earlier One Last Light video. The joins remained noticeable despite using preceding-frame image references for clips 2–6. The references did not prevent changes in lighting, character rendering and movement.

The five original boundaries show a large exposure drop. At 160×90 grayscale analysis resolution, their mean absolute frame differences were about 14.0, 14.1, 13.6, 13.6 and 11.5 levels out of 255. This measures the combined image difference, including pose changes; it is not a perceptual video-quality score.

## Local repair

`scripts/finish-video.py` makes a separate preview using bounded exposure matching around the joins and optional 24-to-48 fps motion interpolation. It keeps the exact shot lengths, copies the existing audio and preserves the original export. It does not generate new Flow clips, add native detail, or repair character/anatomy changes already present in the footage. The original six 720p source clips stay available for another edit.

The pipeline's reference extractor also had a small temporal mismatch: seeking to 0.1 seconds before the end and taking the first frame selected an earlier pose. It now decodes the final half-second and selects the actual final frame. A moving-image regression test verifies the decoded pixels against the video's final frame. This helps future generations; it does not alter the six completed source clips.

## Direction for future generation

Keep the image-guided method and review the actual motion before continuing an episode. Use stable character reference images alongside the preceding ending image when the chosen Flow mode supports them. Keep one dominant action per shot, consistent camera direction and lighting, and an explicit opening pose. Avoid combining a camera move, combat impact, dialogue and a major magical transformation in the same ten seconds. Full prior story context remains reference material, with only the current beat performed.

Check the reference and next opening for exposure/pose changes before generating later shots. Smoothness requires stable generated motion as well as good editing. Local exposure matching and interpolation are finishing tools, not substitutes for coherent source animation.
