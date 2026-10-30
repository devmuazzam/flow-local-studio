import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

_temp = tempfile.TemporaryDirectory(prefix="flow-local-tests-")
os.environ["FLOW_LOCAL_DATA"] = _temp.name
from flow_local import core
from flow_local.planner import demo_plan
from flow_local.agent_flow import quoted_cost


class PipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        core.initialize()

    def story(self, minutes=1, aspect="9:16"):
        s = core.create_story({"idea": "A robot rescues a glowing seed.", "minutes": minutes, "aspect": aspect, "resolution": "360p"})
        return core.save_plan(s["id"], demo_plan(minutes))

    def test_ten_minute_shape_and_budget(self):
        s = self.story(10)
        self.assertEqual(len(s["scenes"]), 60)
        self.assertEqual(core.estimate({**s["spec"], "resolution": "720p"})["estimated_credits"], 900)
        self.assertEqual(core.estimate(s["spec"])["estimated_credits"], 420)
        with self.assertRaisesRegex(ValueError, "below"):
            core.reserve_run(s["id"], "pro", 419, False)

    def test_thirty_second_story_has_full_previous_context(self):
        s=core.create_story({'idea':'A robot rescues the final seed.','seconds':30,'resolution':'720p'})
        plan=demo_plan();plan['episodes'][0]['scenes']=plan['episodes'][0]['scenes'][:3]
        s=core.save_plan(s['id'],plan)
        self.assertEqual(core.estimate(s['spec'])['estimated_credits'],45)
        self.assertEqual(core.episode_seconds(s['spec'],1),30)
        prompt=core.scene_prompt(s,s['scenes'][2])
        self.assertIn(s['scenes'][0]['prompt'],prompt)
        self.assertIn(s['scenes'][1]['prompt'],prompt)
        self.assertIn('CURRENT SHOT 3 OF 3',prompt)
        core.reserve_run(s['id'],'demo',0,True)
        with patch.object(core,'dimensions',return_value=(90,160)):
            core.run_story(s['id'])
        s=core.get_story(s['id'])
        self.assertEqual(s['status'],'complete',s['error'])
        self.assertAlmostEqual(float(core.probe(core.DATA/s['full_video'])['format']['duration']),30,delta=.001)

    def test_uncertain_submission_is_not_retried(self):
        s = self.story()
        core.reserve_run(s["id"], "pro", 100, False)
        result = subprocess.CompletedProcess([], 9, json.dumps({"retryable": True}), "")
        with patch.object(core, "call_gflow", return_value=result) as call:
            core.run_story(s["id"])
            self.assertEqual(call.call_count, 1)
        s = core.get_story(s["id"])
        self.assertEqual(s["status"], "needs_review")
        self.assertEqual(s["scenes"][0]["status"], "uncertain")
        with self.assertRaisesRegex(ValueError, "Reconcile"):
            core.reserve_run(s["id"], "pro", 100, False)
        core.recover_scene(s["id"], 1, 1, confirm_not_submitted=True)
        with self.assertRaisesRegex(ValueError, "another account"):
            core.reserve_run(s["id"], "other", 100, False)

    def test_restart_retains_uncertain_state(self):
        s = self.story()
        core.reserve_run(s["id"], "pro", 100, False)
        core.scene_update(s["id"], 1, 1, status="submitting")
        core.initialize()
        s = core.get_story(s["id"])
        self.assertEqual(s["status"], "needs_review")
        self.assertEqual(s["scenes"][0]["status"], "uncertain")

    def test_browser_lock_prevents_two_drivers(self):
        with core.browser_lock():
            with self.assertRaisesRegex(ValueError, "busy"):
                with core.browser_lock():
                    pass

    def test_plan_and_profile_validation(self):
        s = self.story()
        p = demo_plan()
        p["episodes"][0]["scenes"].pop()
        with self.assertRaisesRegex(ValueError, "six"):
            core.save_plan(s["id"], p)
        with self.assertRaises(ValueError):
            core.check_profile("../outside")

    def test_paid_quote_must_be_single_clip_within_limit(self):
        self.assertEqual(quoted_cost('Approve 1 video generation costing 15 credits?',15),15)
        for title in ['Approve 2 videos for 15 credits?', 'Approve 1 video for 40 credits?', 'Approve video?']:
            with self.assertRaises(ValueError):quoted_cost(title,15)

    def test_pilot_stops_after_one_clip(self):
        s = self.story()
        core.reserve_run(s['id'],'demo',0,True,max_new_clips=1)
        with patch.object(core,'dimensions',return_value=(90,160)):
            core.run_story(s['id'],max_new_clips=1)
        s=core.get_story(s['id'])
        self.assertEqual(s['status'],'paused')
        self.assertEqual(sum(c['status']=='complete' for c in s['scenes']),1)
        self.assertEqual(sum(c['status']=='pending' for c in s['scenes']),5)

    def test_demo_exports_two_episodes_and_both_orientations(self):
        for aspect, size in [("9:16", (90,160)), ("16:9", (160,90))]:
            with self.subTest(aspect=aspect):
                s = self.story(2, aspect)
                core.reserve_run(s["id"], "demo", 0, True)
                with patch.object(core, "dimensions", return_value=size):
                    core.run_story(s["id"])
                s = core.get_story(s["id"])
                self.assertEqual(s["status"], "complete", s["error"])
                self.assertEqual(len(s["outputs"]), 2)
                self.assertEqual(s["reserved"], 0)
                self.assertLessEqual(float(core.probe(core.DATA / s["outputs"][0])["format"]["duration"]),60.001)
                info = core.probe(core.DATA / s["full_video"])
                self.assertAlmostEqual(float(info["format"]["duration"]), 120, delta=.5)
                video = next(v for v in info["streams"] if v["codec_type"] == "video")
                self.assertEqual((video["width"],video["height"]), size)
                self.assertTrue(any(v["codec_type"] == "audio" for v in info["streams"]))
                # First six shots must retain their expected order after normalization/concat.
                rgb = []
                for sec in [5,15,25,35,45,55]:
                    r = subprocess.run(["ffmpeg","-v","error","-ss",str(sec),"-i",str(core.DATA/s["outputs"][0]),
                        "-vf","crop=2:2:0:0,format=rgb24","-frames:v","1","-f","rawvideo","-"],capture_output=True,check=True)
                    rgb.append(tuple(r.stdout[:3]))
                self.assertGreater(rgb[0][1],rgb[0][0])  # teal opening
                self.assertGreater(rgb[2][2],rgb[2][0])  # blue third shot
                self.assertGreater(rgb[5][0],rgb[5][2])  # amber ending


if __name__ == "__main__":
    unittest.main()
