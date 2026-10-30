from __future__ import annotations

import json
from urllib.request import Request, urlopen
from .core import DATA, estimate, episode_seconds, get_story, save_plan, update_story


def ollama_json(model, prompt, schema):
    body = {
        "model": model, "stream": False, "format": schema, "keep_alive": "2m",
        "messages": [
            {"role": "system", "content": "You are a visual storyteller writing in plain English. Return concise JSON matching the supplied schema. Always write full character and location names, never initials, abbreviations or timecodes. Preserve the traits specified by the user; do not invent clothing or faces for ordinary objects unless the story calls for it. Plan a coherent complete story, with recurring characters. Each shot shows one filmable action that advances the plot. Avoid repeating the same action in adjacent shots. No abstract instructions or placeholders."},
            {"role": "user", "content": prompt},
        ],
        "options": {"temperature": 0.65, "num_ctx": 8192, "num_predict": 2500},
    }
    req = Request("http://127.0.0.1:11434/api/chat", json.dumps(body).encode(), {"Content-Type": "application/json"})
    with urlopen(req, timeout=900) as response:
        result = json.load(response)
    return json.loads(result["message"]["content"])


def plan_story(story_id):
    story = get_story(story_id)
    spec = story["spec"]
    folder = DATA / "stories" / story_id
    folder.mkdir(parents=True, exist_ok=True)
    outline_schema = {
        "type": "object", "properties": {
            "title": {"type": "string", "maxLength": 100}, "bible": {"type": "string", "maxLength": 1800},
            "episodes": {"type": "array", "minItems": spec["minutes"], "maxItems": spec["minutes"],
                "items": {"type": "object", "properties": {"title": {"type": "string", "maxLength": 100}, "summary": {"type": "string", "maxLength": 500}}, "required": ["title", "summary"]}},
        }, "required": ["title", "bible", "episodes"],
    }
    scene_schema = {
        "type": "object", "properties": {"scenes": {"type": "array", "minItems": 6, "maxItems": 6,
            "items": {"type": "object", "properties": {
                "beat": {"type": "string", "maxLength": 60, "description": "Short action name, never a timecode"},
                "subject": {"type": "string", "maxLength": 160},
                "action": {"type": "string", "maxLength": 250},
                "camera": {"type": "string", "maxLength": 100},
                "lighting": {"type": "string", "maxLength": 100},
                "sound": {"type": "string", "maxLength": 120},
            }, "required": ["beat", "subject", "action", "camera", "lighting", "sound"]}}}, "required": ["scenes"],
    }
    try:
        update_story(story_id, status="planning", error=None)
        outline = ollama_json(spec["ollama_model"],
            f"Story idea: {spec['idea']}\nVisual style: {spec['style']}\n"
            f"Create a complete {spec.get('seconds',spec['minutes']*60)}-second story outline split into exactly {spec['minutes']} episodes of up to one minute. "
            "Give a detailed continuity bible (named characters with exact physical appearance, wardrobe, "
            "props, locations, palette). Every episode must advance the same story, ending in a hook "
            "except the final episode, which resolves the story. Keep each summary under 80 words.", outline_schema)
        if len(outline.get("episodes", [])) != spec["minutes"]:
            raise ValueError("Local model returned the wrong number of episodes. Try planning again or import a plan.")
        plan = {"title": outline["title"], "bible": outline["bible"], "episodes": []}
        previous = "The story begins."
        for i, episode in enumerate(outline["episodes"], 1):
            update_story(story_id, error=f"Planning episode {i} of {spec['minutes']} locally…")
            shot_count = episode_seconds(spec,i)//10
            scene_schema['properties']['scenes']['minItems'] = shot_count
            scene_schema['properties']['scenes']['maxItems'] = shot_count
            shots = ollama_json(spec["ollama_model"],
                f"Overall outline: {json.dumps(outline)}\nPrevious episode ending: {previous}\n"
                f"Write episode {i}: {json.dumps(episode)}. Format: {spec['aspect']}. "
                f"Exactly {shot_count} chronological shots, each exactly ten seconds. Each prompt must state "
                "the visible subject, exact wardrobe, location, one main action, camera framing or move, "
                "lighting, and sound. Use at most one short spoken sentence in a shot. "
                "Do not pack ten actions into ten seconds. End each shot in a state compatible with the next. "
                "Restate identifying visual details in the subject field. Use the structured fields to "
                "describe just one action in each shot, with a different story beat in every shot. "
                "The final episode must resolve the story. Do not write timecodes or transition instructions.", scene_schema)
            scenes = []
            for shot in shots["scenes"]:
                scenes.append({"beat": shot["beat"], "prompt": " ".join(
                    f"{field.capitalize()}: {shot[field]}." for field in ("subject", "action", "camera", "lighting", "sound"))})
            plan["episodes"].append({**episode, "scenes": scenes})
            previous = scenes[-1]["prompt"]
            (folder / "plan-in-progress.json").write_text(json.dumps(plan, indent=2), encoding="utf-8")
        save_plan(story_id, plan)
        (folder / "storyboard.json").write_text(json.dumps(plan, indent=2), encoding="utf-8")
    except Exception as exc:
        update_story(story_id, status="plan_failed", error=str(exc))


def demo_plan(minutes=1):
    beats = [
        ("Discovery", "A small copper robot with a teal scarf walks through a rain-soaked greenhouse and notices a single glowing seed beneath a cracked flowerpot. Wide tracking shot, soft blue dawn light, rain taps the glass."),
        ("Decision", "The same copper robot with a teal scarf kneels beside the cracked flowerpot and gently lifts the glowing seed into its open metal palm. Close-up, blue dawn light, quiet servo sounds and rain."),
        ("Obstacle", "The copper robot with the teal scarf carries the seed toward an empty planter, but a leaking pipe washes soil out of it. Medium shot, slow push-in, blue dawn light, water splashing."),
        ("Effort", "The copper robot with the teal scarf braces a fallen wooden board across the leaking pipe, diverting the water away from the empty planter. Low-angle medium shot, soft dawn light, creaking wood and running water."),
        ("Change", "The copper robot with the teal scarf plants the glowing seed in the rescued planter and cups fresh soil around it. Close-up on careful metal fingers, warm dawn light begins entering the greenhouse, rain softens."),
        ("Payoff", "The copper robot with the teal scarf watches a tiny luminous sprout unfold from the rescued planter, then sits beside it as sunlight fills the greenhouse. Wide slow pullback, warm dawn light, gentle greenhouse ambience."),
    ]
    return {"title": "The Last Seed — demo storyboard", "bible": "A small weathered copper robot, round amber eyes, teal knitted scarf, no clothing changes. An abandoned glass greenhouse at dawn after rain. One glowing seed, one cracked flowerpot and one empty planter. Cinematic gentle science fiction, blue shadows becoming warm gold.",
            "episodes": [{"title": f"Episode {ep}", "summary": "Demo storyboard; replace repeated episodes with a real local plan.",
                "scenes": [{"beat": beat, "prompt": prompt, "duration": 10} for beat, prompt in beats]} for ep in range(1, minutes + 1)]}
