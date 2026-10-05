import sys
sys.dont_write_bytecode = True
import importlib.util, json, math, tempfile, unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'toolkit'))
from music_analysis import energy_candidates, map_music_cues
spec=importlib.util.spec_from_file_location('workflow_installer',ROOT/'安装与初始化.py')
installer=importlib.util.module_from_spec(spec);spec.loader.exec_module(installer)

class InstallTests(unittest.TestCase):
    def test_install_and_resume_without_overwriting(self):
        with tempfile.TemporaryDirectory() as d:
            home=Path(d)/'home'; home.mkdir()
            one=installer.install(home);two=installer.install(home)
            self.assertTrue(all(v=='installed' for v in one['destinations'].values()))
            self.assertTrue(all(v=='already_identical' for v in two['destinations'].values()))
            global_rules=home/'.codex'/'AGENTS.md'
            self.assertFalse(global_rules.exists())
            project=Path(d)/'project'
            result=installer.init_project(project,home)
            state=json.loads((project/'PROJECT_STATE.json').read_text(encoding='utf-8'))
            self.assertEqual(result['phase'],state['phase'])
            self.assertIsNone(state['clocks']['timeline']['fps'])
            snapshot=json.loads((project/state['constraints']['snapshot_path']).read_text(encoding='utf-8'))
            self.assertEqual(snapshot['profile_values'],installer.read(home/'.agents/video-workflow/user-profile.json'))
            self.assertEqual(snapshot['record_role'],'derived_constraint_snapshot_not_rule_authority')
            self.assertTrue((project/state['paths']['review_root']).is_dir())
            before=(project/'PROJECT_STATE.json').read_bytes()
            with self.assertRaises(ValueError):installer.init_project(project,home)
            self.assertEqual(before,(project/'PROJECT_STATE.json').read_bytes())

    def test_existing_skill_and_personal_rules_are_not_replaced(self):
        with tempfile.TemporaryDirectory() as d:
            home=Path(d);installer.install(home)
            skill=home/'.agents'/'skills'/'video-media-review'/'SKILL.md'
            skill.write_text('user local customization',encoding='utf-8')
            with self.assertRaises(ValueError):installer.install(home)
            self.assertEqual(skill.read_text(encoding='utf-8'),'user local customization')
        with tempfile.TemporaryDirectory() as d:
            home=Path(d);installer.install(home)
            profile=home/'.agents'/'video-workflow'/'user-profile.json'
            content=json.loads(profile.read_text(encoding='utf-8'));content['revision']='user-next'
            profile.write_text(json.dumps(content),encoding='utf-8')
            with self.assertRaises(ValueError):installer.install(home)
            self.assertEqual(json.loads(profile.read_text())['revision'],'user-next')

class MusicTests(unittest.TestCase):
    def test_impulses_are_candidates_not_exact_beats(self):
        samples=[0.0]*32000
        for t in (.5,1,1.5):
            i=round(t*16000)
            samples[i:i+320]=[.5]*320
        report=energy_candidates(samples,16000)
        times=[r['source_seconds'] for r in report['onset_candidates']]
        self.assertEqual(times,[.5,1,1.5])

    def test_music_edits_map_exactly_and_exclude_out_endpoint(self):
        cues=[{'source_seconds':10},{'source_seconds':10.5},{'source_seconds':11}]
        cuts=[{'id':'A','source_in_seconds':10,'source_out_seconds':11,'target_in_frame':100,'speed':2}]
        mapped=map_music_cues(cues,cuts,'24000/1001')
        self.assertEqual(len(mapped),2)
        self.assertEqual(mapped[0]['target_frame_exact'],'100')
        self.assertEqual(mapped[1]['target_frame_exact'],'106100/1001')
        self.assertTrue(all(r['status']=='candidate_not_listened' for r in mapped))
        with self.assertRaises(ValueError):map_music_cues(cues,cuts,None)

if __name__=='__main__':unittest.main(verbosity=2)
