#!/usr/bin/env python3
"""Make a separate local continuity preview; never submit a Flow generation.

Match exposure only where adjacent frames are similar, taper the correction
away from the join, and optionally interpolate motion. Audio is stream-copied.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import math
from pathlib import Path
import statistics
import subprocess
import tempfile


def run(args):
    return subprocess.check_output(args, stderr=subprocess.PIPE)


def info(path):
    return json.loads(run(["ffprobe", "-v", "error", "-show_format", "-show_streams",
                           "-of", "json", str(path)]))


def gray(path, time):
    return run(["ffmpeg", "-v", "error", "-ss", str(time), "-i", str(path),
                "-frames:v", "1", "-vf", "scale=160:90,format=gray",
                "-f", "rawvideo", "-"])


def match(previous, following):
    # A bounded fit rejects moving highlights and deep, already clipped shadows.
    pairs = [(x, y) for x, y in zip(following, previous)
             if 20 < x < 200 and abs(x - y) < 65]
    before = statistics.mean(abs(x - y) for x, y in zip(following, previous))
    if len(pairs) < len(previous) * .25:
        return {"accepted": False, "reason": "insufficient matching pixels", "before_mae": before}
    mx = statistics.mean(x for x, _ in pairs)
    my = statistics.mean(y for _, y in pairs)
    variance = sum((x - mx) ** 2 for x, _ in pairs)
    if variance == 0:
        return {"accepted": False, "reason": "flat frame", "before_mae": before}
    slope = sum((x - mx) * (y - my) for x, y in pairs) / variance
    offset = my - slope * mx
    residual = statistics.median(abs(slope * x + offset - y) for x, y in pairs)
    accepted = .8 <= slope <= 1.2 and abs(offset) <= 30 and residual <= 8
    return {"accepted": accepted, "slope": slope, "offset": offset,
            "fit_median_error": residual, "before_mae": before}


def eq_edge(multiplier, offset):
    # Measurement uses full-range gray; FFmpeg eq acts on limited-range Y.
    raw_offset = 16 * (1 - multiplier) + offset * 219 / 255
    return multiplier - 1, raw_offset / 255 + (multiplier - 1) / 2


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("input", type=Path)
    p.add_argument("output", type=Path)
    p.add_argument("--clip-seconds", type=float, default=10)
    p.add_argument("--ramp-seconds", type=float, default=1.25)
    p.add_argument("--motion-fps", type=int, choices=[0, 48], default=0,
                   help="48 creates interpolated frames; 0 retains source frame rate")
    p.add_argument("--workers", type=int, default=3)
    args = p.parse_args()
    source, output = args.input.resolve(), args.output.resolve()
    if source == output or output.exists():
        p.error("Choose a new output path; existing videos are never overwritten.")
    metadata = info(source)
    video = next(s for s in metadata["streams"] if s["codec_type"] == "video")
    fps_num, fps_den = map(int, video["avg_frame_rate"].split("/"))
    fps = fps_num / fps_den
    duration = float(metadata["format"]["duration"])
    if args.clip_seconds <= 0 or not 0 < args.ramp_seconds < args.clip_seconds / 2:
        p.error("Ramp must be positive and shorter than half a clip.")
    if video.get("color_range") not in (None, "unknown", "tv"):
        p.error("This exposure preview currently supports limited-range sources only.")
    total = math.ceil(duration / args.clip_seconds)
    # Per segment: affine adjustment at the opening and ending frame.
    edges = [[[1., 0.], [1., 0.]] for _ in range(total)]
    report = {"input": str(source), "output": str(output), "duration_seconds": duration,
              "source_fps": fps, "output_fps": args.motion_fps or fps,
              "audio": "stream copy", "flow_generations": 0, "joins": []}
    for i in range(1, total):
        t = i * args.clip_seconds
        result = match(gray(source, t - 1 / fps), gray(source, t))
        result["time_seconds"] = t
        if result["accepted"]:
            a, b = result["slope"], result["offset"]
            # Meet halfway rather than changing the grade of an entire shot.
            edges[i - 1][1] = [.5 + .5 / a, -b / (2 * a)]
            edges[i][0] = [.5 + .5 * a, b / 2]
        report["joins"].append(result)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="flow-finish-", dir=output.parent) as work:
        work = Path(work)

        def render(i):
            length = min(args.clip_seconds, duration - i * args.clip_seconds)
            end_time = length - 1 / fps
            r = args.ramp_seconds
            start_weight = f"(1-clip(t/{r},0,1))^2*(1+2*clip(t/{r},0,1))"
            x = f"clip((t-({end_time}-{r}))/{r},0,1)"
            end_weight = f"({x})^2*(3-2*({x}))"
            sc, sb = eq_edge(*edges[i][0])
            ec, eb = eq_edge(*edges[i][1])
            contrast = f"1+({sc})*({start_weight})+({ec})*({end_weight})"
            brightness = f"({sb})*({start_weight})+({eb})*({end_weight})"
            filters = [f"eq=contrast='{contrast}':brightness='{brightness}':eval=frame"]
            if args.motion_fps:
                # Extra end frames provide lookahead; trim restores exact timing.
                filters += ["tpad=stop_mode=clone:stop_duration=0.25",
                            f"minterpolate=fps={args.motion_fps}:mi_mode=mci:mc_mode=aobmc:"
                            "me_mode=bidir:me=epzs:search_param=16:vsbmc=1:scd_threshold=10"]
            filters += [f"trim=duration={length}", "setpts=PTS-STARTPTS"]
            path = work / f"part-{i:03}.mp4"
            run(["ffmpeg", "-v", "error", "-ss", str(i * args.clip_seconds),
                 "-t", str(length), "-i", str(source), "-an", "-vf", ",".join(filters),
                 "-r", str(args.motion_fps or fps), "-fps_mode", "cfr",
                 "-frames:v", str(round(length * (args.motion_fps or fps))),
                 "-c:v", "libx264", "-preset", "slow", "-crf", "17", "-threads", "2",
                 "-pix_fmt", "yuv420p", "-color_range", "tv", "-colorspace", "bt709",
                 "-color_trc", "bt709", "-color_primaries", "bt709", str(path)])
            print(f"Finished segment {i + 1}/{total}", flush=True)
            return path

        parts = [None] * total
        with ThreadPoolExecutor(max_workers=max(1, min(args.workers, total))) as pool:
            futures = {pool.submit(render, i): i for i in range(total)}
            for future in as_completed(futures):
                parts[futures[future]] = future.result()
        playlist = work / "parts.txt"
        playlist.write_text("".join(f"file '{part}'\n" for part in parts))
        partial = work / "finished.mp4"
        run(["ffmpeg", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(playlist),
             "-i", str(source), "-map", "0:v:0", "-map", "1:a?", "-c", "copy",
             "-bsf:v", "setts=pts=PTS-STARTPTS:dts=DTS-STARTPTS",
             "-t", str(duration), "-movflags", "+faststart", str(partial)])
        result = info(partial)
        actual_duration = float(result["format"]["duration"])
        if abs(actual_duration - duration) > .03:
            details = [{k: s.get(k) for k in ("codec_type", "start_time", "duration", "nb_frames", "avg_frame_rate")}
                       for s in result["streams"]]
            raise ValueError(f"Duration changed: {duration} -> {actual_duration}; {details}")
        partial.replace(output)
    for join in report["joins"]:
        t = join["time_seconds"]
        first, second = gray(output, t - 1 / fps), gray(output, t)
        join["after_mae"] = statistics.mean(abs(a - b) for a, b in zip(first, second))
    output.with_suffix(".json").write_text(json.dumps(report, indent=2) + "\n")
    print(output, flush=True)


if __name__ == "__main__":
    main()
