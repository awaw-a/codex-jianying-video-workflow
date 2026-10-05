"""Regression contracts from real project failures; all fixtures live under tests."""
import sys
sys.dont_write_bytecode = True
import array
import contextlib
import copy
from fractions import Fraction
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parent
SCRIPT = HERE.parent / "toolkit/video_workflow.py"
spec = importlib.util.spec_from_file_location("video_workflow", SCRIPT)
vw = importlib.util.module_from_spec(spec); spec.loader.exec_module(vw)


class ProjectFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="workflow_test_", dir=HERE)
        self.base = Path(self.temp.name).resolve()
        self.native = self.base / "native"; self.native.mkdir()
        (self.native / "draft_content.json").write_bytes(b"\x00opaque encrypted draft\xff")
        (self.native / "cache").mkdir(); (self.native / "cache/mask.bin").write_bytes(b"native mask")
        (self.base / "export.mp4").write_bytes(b"export fixture")
        (self.base / "old.json").write_text('{"stale":"design"}', encoding="utf-8")
        (self.base / "media").mkdir(); (self.base / "media/A.mp4").write_bytes(b"frozen proxy")
        self.asset = {"id":"A", "path":"media/A.mp4", "kind":"video", "frozen":True,
                      "sha256":vw.sha256_file(self.base/"media/A.mp4"),
                      "source":{"path":"original.mkv", "clock":"episode1", "in":"20", "out":"24", "unit":"second",
                                "fps":"24000/1001", "canonical_scene_id":"camera_a", "aliases":[]},
                      "proxy":{"fps":"24", "frames":96, "safe_in_frame":0, "safe_out_frame_exclusive":96},
                      "boundary":{"status":"verified", "cuts":[]}}
        self.manifest = {"schema_version":"1.0", "assets":[self.asset], "dependency_status":"conservative_superset"}
        self.row = {"id":"V1", "asset_id":"A", "proxy_in_frame":0, "proxy_out_frame_exclusive":48,
                    "target_in_frame":0, "target_out_frame_exclusive":48, "occurrence_id":"first", "design_intent":"normal"}
        self.plan = {"schema_version":"1.0", "authority":"design_reference", "shots":[self.row]}
        self.profile = {"revision":"1", "defaults":{"reuse_policy":"allow_declared", "human_listening_required_for_final":True}}
        self.state = {"schema_version":"1.0", "project_id":"fixture", "phase":"review", "profile_path":"profile.json",
                      "constraints":{"revision":1,"profile_revision":"1","project_overrides":{}},
                      "clocks":{"timeline":{"fps":"24", "unit":"frame", "end_exclusive":True}},
                      "paths":{"media_manifest":"manifest.json", "edit_plan":"plan.json"},
                      "artifacts":[{"id":"native", "path":"native/draft_content.json", "project_directory":"native",
                                    "role":"native_project", "authority":"current_native", "encrypted":True,
                                    "sha256":vw.sha256_file(self.native/"draft_content.json")},
                                   {"id":"export", "path":"export.mp4", "role":"export_current",
                                    "sha256":vw.sha256_file(self.base/"export.mp4")},
                                   {"id":"legacy", "path":"old.json", "role":"legacy_plaintext", "authority":"design_reference",
                                    "sha256":vw.sha256_file(self.base/"old.json")}],
                      "latest_native_artifact_id":"native", "latest_export_artifact_id":"export", "evidence":[]}
        self.flush()

    def tearDown(self):
        self.assertTrue(vw.is_inside(self.base, HERE))
        self.temp.cleanup()

    def flush(self):
        for name, value in [("state.json",self.state),("manifest.json",self.manifest),("plan.json",self.plan),("profile.json",self.profile)]:
            (self.base/name).write_text(json.dumps(value), encoding="utf-8")

    def project(self):
        self.flush(); return vw.Project(self.base/"state.json")

    def evidence(self, kind, **extra):
        fingerprints = {a["id"]:a["sha256"] for a in self.state["artifacts"] if a["id"] in {"native","export"}}
        return {"id":kind, "kind":kind, "status":"verified", "constraints_revision":1, "fingerprints":fingerprints, **extra}

    def make_final(self):
        self.state["phase"] = "final"
        self.state["evidence"] = [self.evidence("native_export_binding", verification_method="native_export_observation"),
                                  self.evidence("human_listening", actor="user", verification_method="user_listening", user_confirmation_ref="user-message-7")]


class ClockTests(unittest.TestCase):
    def test_fraction_right_exclusive_stop(self):
        r=vw.map_clock(2705,"timeline","timeline","frame","second","24")
        self.assertEqual(r["value"]["exact"],"2705/24"); self.assertTrue(r["right_end_exclusive"])
        self.assertEqual(vw.seconds(1000000,"microsecond"),1)

    def test_cross_clock_requires_explicit_mapping(self):
        with self.assertRaises(vw.WorkflowError):vw.map_clock(24,"source","timeline","frame","frame","24")
        m={"source_clock":"source","target_clock":"timeline","source_in":"10","source_out":"12",
           "source_unit":"second","target_in":0,"target_out":24,"target_unit":"frame","target_fps":"24"}
        self.assertEqual(vw.map_clock(11,"source","timeline","second","frame",mapping=m)["value"]["exact"],"12")
        with self.assertRaises(vw.WorkflowError):vw.map_clock(13,"source","timeline","second","frame",mapping=m)

    def test_illegal_units_bounds_and_missing_fps(self):
        for args in [(1,0,"second",None),(-1,2,"second",None),(0,1,"frame",None),(0,"1.2","microsecond",None)]:
            with self.subTest(args=args),self.assertRaises(vw.WorkflowError):vw.interval(*args)
        with self.assertRaises(vw.WorkflowError):vw.seconds(1,"milliseconds")


class UsageTests(ProjectFixture):
    def test_accidental_overlap_rejected(self):
        row=copy.deepcopy(self.row);row.update(id="V2",target_in_frame=48,target_out_frame_exclusive=96,occurrence_id="second")
        self.plan["shots"].append(row)
        r=vw.audit_usage(self.project());self.assertFalse(r["valid"])
        self.assertIn("unintentional_source_overlap",[x["code"] for x in r["errors"]])

    def test_declared_replay_freeze_reverse_loop_not_accidental(self):
        for mode in sorted(vw.CREATIVE):
            row=copy.deepcopy(self.row);row.update(id="V2",target_in_frame=48,target_out_frame_exclusive=96,
                design_intent=mode,repeat_of="V1",authorization_ref="profile:1",
                reuse_declaration={"source_identity":"A","target_interval":{"clock":"timeline","unit":"frame","in":48,"out":96},
                                   "effect_type":mode,"creative_purpose":"intentional rhythm accent"})
            self.plan["shots"]=[self.row,row]
            with self.subTest(mode=mode):self.assertTrue(vw.audit_usage(self.project())["valid"])

    def test_false_creative_declaration_cannot_bypass(self):
        row=copy.deepcopy(self.row);row.update(id="V2",target_in_frame=48,target_out_frame_exclusive=96,design_intent="replay",authorization_ref="profile:1",
            reuse_declaration={"source_identity":"not A","target_interval":{"clock":"timeline","unit":"frame","in":0,"out":48},
                               "effect_type":"replay","creative_purpose":"fake"})
        self.plan["shots"].append(row);self.assertFalse(vw.audit_usage(self.project())["valid"])

    def test_same_instant_composite_math_and_position_required(self):
        overlay=copy.deepcopy(self.row);overlay.update(id="FG",proxy_in_frame=12,proxy_out_frame_exclusive=36,
            target_in_frame=12,target_out_frame_exclusive=36,design_intent="same_instant_composite",composite_of="V1",sync_verified=True,transform_same_position=True)
        self.plan["shots"].append(overlay)
        self.assertTrue(vw.audit_usage(self.project())["valid"])
        overlay["target_in_frame"]=13;overlay["target_out_frame_exclusive"]=37
        self.assertFalse(vw.audit_usage(self.project())["valid"])

    def test_group_name_is_not_same_instant_proof(self):
        r=copy.deepcopy(self.row);r.update(id="FG",target_in_frame=12,target_out_frame_exclusive=60)
        self.plan["shots"].append(r);self.assertFalse(vw.audit_usage(self.project())["valid"])

    def test_final_proxy_frame_cut_is_candidate_until_human_trim(self):
        self.asset["boundary"]["cuts"]=[47]
        r=vw.audit_usage(self.project());self.assertTrue(r["valid"]);self.assertFalse(r["review_complete"])
        self.assertTrue(r["candidates"][0]["requires_human_confirmation"])
        self.asset["proxy"]["safe_out_frame_exclusive"]=47;self.row["proxy_out_frame_exclusive"]=47
        self.assertTrue(vw.audit_usage(self.project())["review_complete"])

    def test_cross_master_alias_candidate_not_silently_unique(self):
        b=copy.deepcopy(self.asset);b["id"]="B";b["source"].update(path="episode2.mkv",canonical_scene_id="recap_a",aliases=["camera_a"])
        self.manifest["assets"].append(b)
        row=copy.deepcopy(self.row);row.update(id="V2",asset_id="B",target_in_frame=48,target_out_frame_exclusive=96,occurrence_id="second")
        self.plan["shots"].append(row);r=vw.audit_usage(self.project())
        self.assertFalse(r["review_complete"]);self.assertEqual(r["candidates"][0]["code"],"cross_master_alias_or_same_camera_reuse")


class StateAndOutputTests(ProjectFixture):
    def test_encrypted_native_not_old_plaintext_authority(self):
        self.assertTrue(vw.validate_state(self.project())["valid"])
        self.state["latest_native_artifact_id"]="legacy"
        self.assertFalse(vw.validate_state(self.project())["valid"])

    def test_final_needs_real_user_listening_and_native_export_binding(self):
        self.state["phase"]="final";self.state["evidence"]=[self.evidence("ASR")]
        r=vw.validate_state(self.project());codes={x["code"] for x in r["errors"]}
        self.assertIn("final_not_listened",codes);self.assertIn("native_export_unbound",codes)
        self.make_final();self.assertTrue(vw.validate_state(self.project())["valid"])
        self.state["evidence"][1]["verification_method"]="ASR"
        self.assertFalse(vw.validate_state(self.project())["valid"])

    def test_final_unresolved_major_and_old_export_hash_refused(self):
        self.make_final();self.state["known_issues"]=[{"id":"hole", "severity":"major","status":"open"}]
        self.assertFalse(vw.validate_state(self.project())["valid"])
        self.state["known_issues"][0]["status"]="resolved"
        self.state["evidence"][0]["fingerprints"]["export"]="old_hash"
        self.assertFalse(vw.validate_state(self.project())["valid"])

    def test_existing_and_frozen_outputs_never_overwritten(self):
        existing=self.base/"existing";existing.mkdir();(existing/"keep").write_bytes(b"untouched")
        with self.assertRaises(vw.WorkflowError):vw.new_output(existing,self.project())
        with self.assertRaises(vw.WorkflowError):vw.new_output(self.native/"audit",self.project())
        with self.assertRaises(vw.WorkflowError):vw.new_output(self.base/"media/new",self.project())
        self.assertEqual((existing/"keep").read_bytes(),b"untouched")

    def test_frozen_media_drift_detected(self):
        (self.base/"media/A.mp4").write_bytes(b"unexpected new encode")
        self.assertFalse(vw.validate_state(self.project())["valid"])

    def test_no_write_cli_has_no_new_files(self):
        self.project();before={str(p.relative_to(self.base)) for p in self.base.rglob("*")}
        with contextlib.redirect_stdout(io.StringIO()):self.assertEqual(vw.main(["handoff","--state",str(self.base/"state.json"),"--no-write"]),0)
        after={str(p.relative_to(self.base)) for p in self.base.rglob("*")};self.assertEqual(before,after)

    def test_pack_preserves_full_native_cache_no_original_copy(self):
        p=self.project();original={str(x):vw.sha256_file(x) for x in vw.tree_files(self.base)}
        report=vw.pack_project(p,self.base/"package")
        dest=self.base/"package"
        self.assertEqual((dest/"native/native/cache/mask.bin").read_bytes(),b"native mask")
        self.assertEqual((dest/"native/native/draft_content.json").read_bytes(),b"\x00opaque encrypted draft\xff")
        self.assertFalse(report["migration_verified"]);self.assertEqual(report["dependency_status"],"conservative_superset")
        self.assertFalse(any(x.name=="original.mkv" for x in dest.rglob("*")))
        for path,expected in original.items():self.assertEqual(vw.sha256_file(path),expected)
        self.assertTrue(vw.validate_state(vw.Project(dest/"PROJECT_STATE.json"))["valid"])
        names={x["path"] for x in vw.read_json(dest/"PACKAGE_HASHES.json")["files"]}
        self.assertIn("PACKAGE_REPORT.json",names)
        with self.assertRaises(vw.WorkflowError):vw.pack_project(p,dest)

    def test_pack_no_write_plan_never_creates_dir(self):
        report=vw.pack_project(self.project(),self.base/"never",True)
        self.assertTrue(report["no_write"]);self.assertFalse((self.base/"never").exists())

    def test_pack_source_index_evidence_and_full_historical_backup(self):
        (self.base/"AGENTS.md").write_text("Read PROJECT_STATE.json first",encoding="utf-8")
        (self.base/"snapshot.json").write_text('{"record_role":"derived_constraint_snapshot_not_rule_authority"}',encoding="utf-8")
        self.state["constraints"]["snapshot_path"]="snapshot.json"
        index={"sources":[{"relative_path":"huge.mkv","bytes":123456789,"head_tail_1MiB_sha256":"partial","fingerprint_is_full_file_hash":False}]}
        (self.base/"index.json").write_text(json.dumps(index),encoding="utf-8")
        (self.base/"review.txt").write_text("visual only, not heard",encoding="utf-8")
        history=self.base/"historical";history.mkdir();(history/"draft_content.json").write_bytes(b"historical encrypted")
        (history/"cache").mkdir();(history/"cache/oldmask.bin").write_bytes(b"historical cache")
        self.manifest["source_index"]="index.json"
        self.state["artifacts"].append({"id":"old-native","role":"native_backup","authority":"design_reference",
            "path":"historical/draft_content.json","project_directory":"historical","sha256":vw.sha256_file(history/"draft_content.json")})
        self.state["evidence"]=[self.evidence("source_boundary",report_path="review.txt")]
        report=vw.pack_project(self.project(),self.base/"bundle")
        b=self.base/"bundle";s=vw.read_json(b/"PROJECT_STATE.json");m=vw.read_json(b/"assets/MEDIA_MANIFEST.json")
        self.assertEqual(vw.read_json(b/m["source_index"]),index)
        original_record=next(x for x in report["original_source_index"] if x.get("relative_path")=="huge.mkv")
        self.assertEqual(original_record["fingerprint_kind"],"declared_partial_not_full")
        self.assertFalse(original_record["fingerprint_is_full_file_hash"])
        self.assertEqual((b/s["evidence"][0]["report_path"]).read_text(encoding="utf-8"),"visual only, not heard")
        a=next(a for a in s["artifacts"] if a["id"]=="old-native")
        self.assertEqual((b/a["project_directory"]/"cache/oldmask.bin").read_bytes(),b"historical cache")
        self.assertEqual(s["editing_owner"]["handoff_ref"],"HANDOFF.md")
        self.assertTrue((b/s["workflow_toolkit_path"]).is_file())
        self.assertEqual((b/s["constraints"]["snapshot_path"]).read_bytes(),(self.base/"snapshot.json").read_bytes())
        self.assertEqual((b/"AGENTS.md").read_text(encoding="utf-8"),"Read PROJECT_STATE.json first")
        self.assertIn("SHA256",(b/"HANDOFF.md").read_text(encoding="utf-8"))
        self.assertTrue(vw.validate_state(vw.Project(b/"PROJECT_STATE.json"))["valid"])

    def test_font_embedding_requires_license_and_permission(self):
        (self.base/"media/font.ttf").write_bytes(b"font fixture")
        font={"id":"font","path":"media/font.ttf","kind":"font","redistributable":True,"frozen":True}
        self.manifest["assets"].append(font)
        with self.assertRaises(vw.WorkflowError):vw.pack_project(self.project(),self.base/"invalid_font")
        self.assertFalse((self.base/"invalid_font").exists())
        (self.base/"license.txt").write_text("Fixture may be redistributed",encoding="utf-8");font["license_path"]="license.txt"
        r=vw.pack_project(self.project(),self.base/"font_bundle")
        self.assertTrue(any(c["kind"]=="font_license" for c in r["copies"]))

    def test_handoff_cli_generates_markdown_same_state_fingerprint(self):
        self.project();target=self.base/"handoff"
        before=(self.base/"state.json").read_bytes()
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(vw.main(["handoff","--state",str(self.base/"state.json"),"--out",str(target)]),0)
        report=vw.read_json(target/"REPORT.json")
        text=(target/"HANDOFF.md").read_text(encoding="utf-8")
        self.assertIn(report["source_state_sha256"],text);self.assertIn("工具没有进行真实视听",text)
        self.assertEqual((self.base/"state.json").read_bytes(),before)

    def test_invalidation_new_state_only_and_scope_aware(self):
        self.state["evidence"]=[self.evidence("source_boundary",scope={"asset_ids":["A"]}),
                                  self.evidence("usage_audit",scope={"asset_ids":["A"]})]
        p=self.project();before=(self.base/"state.json").read_bytes()
        r=vw.invalidate_evidence(p,{"reason":"allow intentional replay", "project_overrides":{"reuse_policy":"allow_declared"}})
        self.assertEqual(r["invalidated_evidence_ids"],["usage_audit"])
        self.assertEqual(r["new_state"]["constraints"]["revision"],2)
        self.assertEqual(r["new_state"]["evidence"][0]["constraints_revision"],2)
        self.assertEqual((self.base/"state.json").read_bytes(),before)

    def test_invalidation_cli_output_retains_original_project_clock_and_paths(self):
        self.project();changes=self.base/"changes.json"
        changes.write_text(json.dumps({"reason":"new tail","target_ranges":[{"clock":"timeline","unit":"frame","in":24,"out":48}]}),encoding="utf-8")
        dest=self.base/"newstate"
        with contextlib.redirect_stdout(io.StringIO()):
            vw.main(["invalidate-evidence","--state",str(self.base/"state.json"),"--changes",str(changes),"--out",str(dest)])
        proposed=vw.Project(dest/"PROJECT_STATE.json")
        self.assertEqual(proposed.base,self.base)
        self.assertEqual(proposed.manifest_path,self.base/"manifest.json")
        self.assertTrue(vw.validate_state(proposed)["valid"])


class MediaAuditTests(unittest.TestCase):
    def decoded(self,fps=24,frames=4,black=False):
        return {"pts":[Fraction(i,fps) for i in range(frames)],"pixels":bytes([0 if black else 80])*576*frames,
                "time_base":Fraction(1,12288),"advertised_fps":Fraction(fps),"actual_fps":Fraction(fps),
                "deltas":[Fraction(1,fps)]*(frames-1),"decoder_stderr_errors":[]}

    def test_proxy_actual_25_declared_24_fails(self):
        with mock.patch.object(vw,"decode_video",return_value=self.decoded(25)):
            r=vw.probe("fixture", "mock", declared_fps="24")
        self.assertEqual(r["actual_fps"],"25");self.assertFalse(r["valid"])

    def test_black_and_cut_word_only_candidates_not_heard(self):
        a=array.array("f",[.1,-.1]*8000)
        proc=subprocess.CompletedProcess([],0,a.tobytes(),b"")
        with mock.patch.object(vw,"decode_video",return_value=self.decoded(black=True)),mock.patch.object(vw,"run_tool",return_value=proc),mock.patch.object(vw,"sha256_file",return_value="hash"):
            r=vw.audit_export("fixture","mock","24",4)
        self.assertTrue(r["technical_pass"]);self.assertFalse(r["human_visual_review_verified"])
        self.assertTrue(r["black_candidates"][0]["requires_human_confirmation"])
        self.assertFalse(r["audio"]["human_listening_verified"])
        self.assertEqual(r["audio"]["cut_word_status"],"not_determined_by_numeric_analysis")

    def test_clipped_audio_not_technical_pass(self):
        a=array.array("f",[1.2,-1.1]*10);proc=subprocess.CompletedProcess([],0,a.tobytes(),b"")
        with mock.patch.object(vw,"decode_video",return_value=self.decoded()),mock.patch.object(vw,"run_tool",return_value=proc),mock.patch.object(vw,"sha256_file",return_value="hash"):
            r=vw.audit_export("fixture","mock","24",4)
        self.assertFalse(r["technical_pass"]);self.assertGreater(r["audio"]["samples_at_or_above_0dbfs"],0)

    @unittest.skipUnless(os.environ.get("VIDEO_WORKFLOW_TEST_FFMPEG"),"Set VIDEO_WORKFLOW_TEST_FFMPEG for real local-media decode")
    def test_real_decoder_25fps_and_timestamp_count(self):
        with tempfile.TemporaryDirectory(prefix="workflow_media_",dir=HERE) as t:
            path=Path(t)/"proxy.mp4";ff=os.environ["VIDEO_WORKFLOW_TEST_FFMPEG"]
            vw.run_tool([ff,"-nostdin","-v","error","-f","lavfi","-i","color=c=red:s=64x64:r=25:d=0.2",
                         "-c:v","libx264","-pix_fmt","yuv420p",str(path)])
            r=vw.probe(path,ff,os.environ.get("VIDEO_WORKFLOW_TEST_FFPROBE"),"24")
            self.assertEqual(r["decoded_frames"],5);self.assertEqual(r["actual_fps"],"25");self.assertFalse(r["valid"])

    @unittest.skipUnless(os.environ.get("VIDEO_WORKFLOW_TEST_FFMPEG"),"Set local FFmpeg path for actual export fixture")
    def test_real_export_stopline_black_candidates_and_aac_numbers(self):
        with tempfile.TemporaryDirectory(prefix="workflow_export_",dir=HERE) as t:
            path=Path(t)/"export.mp4";ff=os.environ["VIDEO_WORKFLOW_TEST_FFMPEG"]
            vw.run_tool([ff,"-nostdin","-v","error","-f","lavfi","-i","color=c=black:s=64x64:r=24",
                "-f","lavfi","-i","sine=frequency=1000:sample_rate=48000",
                "-frames:v","4","-t","0.1666666667","-c:v","libx264","-pix_fmt","yuv420p","-c:a","aac",str(path)])
            r=vw.audit_export(path,ff,"24",4)
            self.assertEqual(r["decoded_frames"],4);self.assertEqual(r["last_visible_frame"],3)
            self.assertTrue(r["technical_pass"]);self.assertEqual(len(r["black_candidates"]),4)
            self.assertFalse(r["human_listening_verified"])
            self.assertIn("after_expected_stop",r["audio"])


if __name__ == "__main__":unittest.main(verbosity=2)
