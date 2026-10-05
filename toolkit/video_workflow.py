#!/usr/bin/env python3
"""Read-only media inspection and append-only project evidence. Python stdlib only.

This tool never writes Jianying drafts, starts the editor, downloads tools/models,
or grants perceptual approval. --no-write performs inspection in memory/stdout.
"""
import sys
sys.dont_write_bytecode = True
import argparse
import array
import copy
import datetime as dt
from fractions import Fraction
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import uuid

VERSION = "1.0"
UNITS = {"frame", "second", "microsecond"}
NATIVE_ROLES = {"native_project", "native_current"}
CREATIVE = {"freeze", "reverse", "replay", "loop"}


class WorkflowError(ValueError):
    pass


def require(condition, message):
    if not condition:
        raise WorkflowError(message)


def ratio(value, positive=False):
    require(value is not None and not isinstance(value, bool), "An explicit numeric value/fps is required")
    try:
        v = Fraction(str(value))
    except (ValueError, ZeroDivisionError):
        raise WorkflowError("Invalid rational value: %r" % value)
    require(not positive or v > 0, "fps/rate must be positive")
    return v


def fraction_json(v):
    v = Fraction(v)
    return {"exact": str(v), "decimal": float(v)}


def seconds(value, unit, fps=None):
    require(unit in UNITS, "Explicit unit must be frame, second, or microsecond")
    v = ratio(value)
    if unit in {"frame", "microsecond"}:
        require(v.denominator == 1, unit + " input must be an integer")
    if unit == "frame":
        return v / ratio(fps, positive=True)
    return v / 1000000 if unit == "microsecond" else v


def from_seconds(value, unit, fps=None):
    require(unit in UNITS, "Unknown output unit")
    return value * ratio(fps, positive=True) if unit == "frame" else value * 1000000 if unit == "microsecond" else value


def interval(a, b, unit, fps=None):
    sa, sb = seconds(a, unit, fps), seconds(b, unit, fps)
    require(sa >= 0 and sb > sa, "Interval must be nonnegative, increasing, and right-end-exclusive")
    return sa, sb


def map_clock(value, from_clock, to_clock, from_unit, to_unit, fps=None, mapping=None):
    require(from_clock and to_clock, "Clock IDs must be explicit")
    if from_clock == to_clock:
        result = from_seconds(seconds(value, from_unit, fps), to_unit, fps)
    else:
        require(mapping is not None, "Different clocks require an explicit source/target mapping")
        m = mapping
        require(from_clock == m.get("source_clock") and to_clock == m.get("target_clock"), "Mapping clock IDs do not match")
        si, so = interval(m["source_in"], m["source_out"], m["source_unit"], m.get("source_fps"))
        ti, to = interval(m["target_in"], m["target_out"], m["target_unit"], m.get("target_fps"))
        point = seconds(value, from_unit, m.get("source_fps"))
        require(si <= point <= so, "Point is outside the mapping (out may be mapped as an endpoint only)")
        result = from_seconds(ti + (point - si) * (to - ti) / (so - si), to_unit, m.get("target_fps"))
    return {"from_clock": from_clock, "to_clock": to_clock, "from_unit": from_unit, "to_unit": to_unit,
            "value": fraction_json(result), "right_end_exclusive": True,
            "requires_rounding_for_integer_unit": to_unit != "second" and result.denominator != 1}


def read_json(path):
    try:
        with Path(path).open("r", encoding="utf-8-sig") as f:
            return json.load(f)
    except OSError as exc:
        raise WorkflowError("Cannot read JSON input: " + str(exc))


def resolve(path, base):
    p = Path(path).expanduser()
    return (p if p.is_absolute() else Path(base) / p).resolve()


def is_inside(path, root):
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
        return True
    except ValueError:
        return False


def linked(path):
    p = Path(path)
    return p.is_symlink() or bool(getattr(p, "is_junction", lambda: False)())


def tree_files(path):
    p = Path(path)
    require(not linked(p), "Symlinks/junctions cannot be packaged or fingerprinted: " + str(p))
    if p.is_file():
        return [p]
    require(p.is_dir(), "Missing artifact: " + str(p))
    files = []
    for directory, dirs, names in os.walk(p, followlinks=False):
        for name in dirs + names:
            q = Path(directory) / name
            require(not linked(q), "Linked dependency is not a closed snapshot: " + str(q))
        files.extend(Path(directory) / name for name in names)
    return sorted(files, key=lambda f: f.relative_to(p).as_posix())


def sha256_file(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1048576), b""):
            h.update(block)
    return h.hexdigest()


def fingerprint(path):
    p = Path(path)
    if p.is_file():
        return sha256_file(p)
    h = hashlib.sha256()
    for f in tree_files(p):
        h.update(f.relative_to(p).as_posix().encode("utf-8") + b"\0" + sha256_file(f).encode("ascii") + b"\n")
    return h.hexdigest()


class Project:
    def __init__(self, state_path, profile_path=None):
        self.state_path = Path(state_path).resolve()
        self.state = read_json(self.state_path)
        self.base = resolve(self.state.get("project_root", "."), self.state_path.parent)
        paths = self.state.get("paths", {})
        mp = self.state.get("media_manifest") or paths.get("media_manifest")
        ep = self.state.get("edit_plan") or paths.get("edit_plan")
        self.manifest_path = resolve(mp, self.base) if mp else None
        self.plan_path = resolve(ep, self.base) if ep else None
        self.manifest = read_json(self.manifest_path) if self.manifest_path else {"assets": []}
        self.plan = read_json(self.plan_path) if self.plan_path else {"shots": []}
        pp = profile_path or self.state.get("profile_path")
        installed = Path(__file__).resolve().parent.parent / "user-profile.json"
        self.profile_path = resolve(pp, self.base) if pp else installed
        self.profile = read_json(self.profile_path) if self.profile_path.exists() else {"defaults": {}}
        self.rules = dict(self.profile.get("defaults", {}))
        self.rule_sources = {key: "profile" for key in self.rules}
        overrides = self.state.get("constraints", {}).get("project_overrides", {})
        self.rules.update(overrides)
        self.rule_sources.update({key: "project_overrides" for key in overrides})
        self.assets = {a["id"]: a for a in self.manifest.get("assets", [])}
        self.artifacts = {a["id"]: a for a in self.state.get("artifacts", [])}

    def asset_path(self, asset):
        return resolve(asset["path"], self.base)

    def artifact_path(self, artifact):
        return resolve(artifact["path"], self.base)

    def native_directory(self, artifact):
        configured = artifact.get("project_directory") or self.state.get("paths", {}).get("native_project")
        p = resolve(configured, self.base) if configured else self.artifact_path(artifact)
        require(p.is_dir(), "A full native project_directory/paths.native_project is required, not only draft_content.json")
        return p

    def fps(self, clock="timeline"):
        return ratio(self.state.get("clocks", {}).get(clock, {}).get("fps"), positive=True)

    def protected(self):
        protected = [resolve(p, self.base) for p in self.state.get("protected_paths", [])]
        protected += [self.native_directory(a) if a.get("role") in NATIVE_ROLES or a.get("project_directory") else self.artifact_path(a)
                      for a in self.artifacts.values() if a.get("role") in NATIVE_ROLES or a.get("project_directory") or a.get("frozen")]
        protected += [self.asset_path(a).parent for a in self.assets.values() if a.get("frozen")]
        for dep in self.dependencies():
            if dep.get("frozen"):
                p = resolve(dep["path"], self.base)
                protected.append(p if p.is_dir() else p.parent)
        return protected

    def dependencies(self):
        return list(self.state.get("dependencies", [])) + list(self.manifest.get("dependencies", []))


def validate_state(project, verify_fingerprints=True):
    s = project.state
    errors, warnings = [], []
    def error(code, message):
        errors.append({"code": code, "message": message})
    if s.get("schema_version") != VERSION:
        error("schema_version", "Unsupported or missing schema_version")
    if not s.get("project_id"):
        error("project_id", "Missing project_id")
    try:
        project.fps()
    except WorkflowError as exc:
        error("timeline_fps", str(exc))
    artifacts = s.get("artifacts", [])
    if len(project.artifacts) != len(artifacts):
        error("duplicate_artifact_id", "Artifact IDs are not unique")
    if len(project.assets) != len(project.manifest.get("assets", [])):
        error("duplicate_asset_id", "Media IDs are not unique")
    latest = s.get("latest_native_artifact_id")
    native = project.artifacts.get(latest)
    native_authorities = {True, "latest_editable_state", "native_saved_project", "current_native", "latest_saved_native_state"}
    if not native or native.get("role") not in NATIVE_ROLES or native.get("authority") not in native_authorities:
        error("native_authority", "Latest native artifact must explicitly be authoritative native_project/native_current")
    current = [a for a in artifacts if a.get("role") in NATIVE_ROLES and a.get("authority") in native_authorities]
    if len(current) != 1:
        error("native_authority_count", "Exactly one native artifact may be authoritative")
    for a in artifacts:
        if a.get("role") == "legacy_plaintext" and a.get("authority") not in {None, False, "design_reference", "historical"}:
            error("legacy_authority", "Historical plaintext cannot be current authority")
    actual = {}
    for a in artifacts:
        p = project.artifact_path(a)
        if not p.exists():
            error("artifact_missing", str(p)); continue
        if a.get("role") in NATIVE_ROLES or a.get("project_directory"):
            try:
                directory = project.native_directory(a)
                require(is_inside(p, directory), "Native artifact path must belong to its full project directory")
            except WorkflowError as exc:
                error("native_not_directory", str(exc))
        if verify_fingerprints:
            try:
                actual[a["id"]] = fingerprint(p)
                if a.get("sha256") and actual[a["id"]] != a["sha256"]:
                    error("artifact_drift", a["id"])
                for f in a.get("files", []):
                    directory = project.native_directory(a) if a.get("role") in NATIVE_ROLES else p
                    fp = resolve(f["path"], directory)
                    require(is_inside(fp, directory), "Native file fingerprint escapes artifact directory")
                    if not fp.is_file() or sha256_file(fp) != f["sha256"]:
                        error("native_file_drift", str(fp))
            except WorkflowError as exc:
                error("artifact_fingerprint", str(exc))
    rev = s.get("constraints", {}).get("revision")
    if not isinstance(rev, int) or rev < 1:
        error("constraints_revision", "constraints.revision must be a positive integer")
    bound_profile = s.get("constraints", {}).get("profile_revision")
    if bound_profile is not None and bound_profile != project.profile.get("revision"):
        error("profile_revision", "Profile changed; resolve rules and invalidate affected evidence before acceptance")
    for row in list(project.assets.values()) + project.dependencies():
        if row.get("frozen") and row.get("sha256"):
            path = resolve(row["path"], project.base)
            try:
                if fingerprint(path) != row["sha256"]:
                    error("frozen_media_drift", row.get("id", str(path)))
            except WorkflowError as exc:
                error("frozen_media_missing", str(exc))
    valid_evidence = []
    for e in s.get("evidence", []):
        if e.get("status") != "verified":
            continue
        if e.get("report_path") and not resolve(e["report_path"], project.base).exists():
            error("evidence_report_missing", e.get("id", "unnamed"))
        stale = e.get("constraints_revision") != rev
        refs = e.get("fingerprints", {})
        for aid, expected in refs.items():
            a = project.artifacts.get(aid)
            if a is None:
                stale = True
            elif verify_fingerprints and actual.get(aid) != expected:
                stale = True
            elif a.get("sha256") != expected:
                stale = True
        if stale:
            error("stale_evidence", e.get("id", "unnamed")); continue
        valid_evidence.append(e)
    export_id = s.get("latest_export_artifact_id")
    export = project.artifacts.get(export_id)
    bound = any(e.get("kind") == "native_export_binding" and
                e.get("verification_method") in {"native_export_observation", "user_export_confirmation"} and
                latest in e.get("fingerprints", {}) and export_id in e.get("fingerprints", {}) for e in valid_evidence)
    if not bound:
        if s.get("phase") == "final":
            error("native_export_unbound", "Final needs verified current native/export fingerprint binding")
        else:
            warnings.append("Current native/export pairing is not verified; design or old export cannot prove it")
    if s.get("phase") == "final":
        if not export or export.get("role") != "export_current":
            error("final_export", "Final requires an explicit current export")
        for issue in s.get("known_issues", []):
            if issue.get("severity") in {"blocker", "major"} and issue.get("status") != "resolved":
                error("unresolved_severe_issue", issue.get("id", issue.get("description", "unnamed")))
        if project.rules.get("human_listening_required_for_final", True):
            heard = any(e.get("kind") == "human_listening" and e.get("actor") == "user" and
                        e.get("verification_method") == "user_listening" and e.get("user_confirmation_ref") and
                        export_id in e.get("fingerprints", {}) for e in valid_evidence)
            if not heard:
                error("final_not_listened", "ASR/numeric checks cannot replace user listening on the current export")
    if native and native.get("encrypted"):
        warnings.append("Encrypted native artifact is preserved as bytes; plaintext design cannot prove active native state")
    if s.get("dependency_status", project.manifest.get("dependency_status")) != "verified_active":
        warnings.append("Dependencies are declared/conservative; active encrypted references and migration are not verified")
    return {"command": "validate-state", "valid": not errors, "errors": errors, "warnings": warnings,
            "resolved_rules_snapshot": project.rules, "rule_sources": project.rule_sources,
            "fingerprints_checked": verify_fingerprints, "human_listening_inferred_from_asr": False}


def audit_usage(project):
    errors, candidates, accepted = [], [], []
    shots = project.plan.get("shots", [])
    by_id = {r["id"]: r for r in shots}
    require(len(by_id) == len(shots), "Shot IDs must be unique")
    tfps = project.fps()
    selected = []
    # An explicit shared alias ID denotes the same camera/action across masters.
    aliases = {}
    def root(x):
        aliases.setdefault(x, x)
        while aliases[x] != x:
            x = aliases[x]
        return x
    for a in project.assets.values():
        source = a.get("source", {}); sid = source.get("canonical_scene_id")
        if sid:
            for alias in source.get("aliases", []):
                require(isinstance(alias, str), "aliases must be canonical scene IDs")
                aliases[root(alias)] = root(sid)
    for r in shots:
        aid = r.get("asset_id")
        if aid not in project.assets:
            errors.append({"shot": r["id"], "code": "missing_asset"}); continue
        a = project.assets[aid]; proxy = a.get("proxy", {}); source = a.get("source", {})
        try:
            pfps = ratio(proxy.get("fps"), positive=True)
            pin, pout = ratio(r["proxy_in_frame"]), ratio(r["proxy_out_frame_exclusive"])
            interval(pin, pout, "frame", pfps)
            tin, tout = ratio(r["target_in_frame"]), ratio(r["target_out_frame_exclusive"])
            interval(tin, tout, "frame", tfps)
            total = ratio(proxy.get("frames"), positive=True)
            require(total.denominator == 1, "Proxy frame count must be integral")
            require(0 <= pin < pout <= total, "Selected frames exceed real proxy bounds")
            safe_in = ratio(proxy.get("safe_in_frame")); safe_out = ratio(proxy.get("safe_out_frame_exclusive"))
            require(0 <= safe_in < safe_out <= total, "Safe interval exceeds proxy bounds")
            require(safe_in <= pin and pout <= safe_out, "Selection exceeds the audited safe camera")
            si, so = interval(source["in"], source["out"], source["unit"], source.get("fps"))
            require(source.get("clock"), "Source clock ID is required")
            sid = source.get("canonical_scene_id")
            require(sid, "canonical_scene_id is required (unknown aliases cannot be assumed safe)")
            mode = r.get("design_intent", "normal")
            require(mode in CREATIVE | {"normal", "same_instant_composite"}, "Unknown design_intent")
            if mode in CREATIVE:
                require(project.rules.get("reuse_policy", "unique_occurrence") == "allow_declared", "Project rules prohibit deliberate reuse")
                declaration = r.get("reuse_declaration", {})
                declared = False
                if declaration:
                    target = declaration.get("target_interval", {})
                    require(declaration.get("source_identity") == aid, "Reuse source_identity must match this asset_id")
                    require(target.get("clock") == "timeline" and target.get("unit") == "frame", "Reuse target must name timeline/frame")
                    require(ratio(target.get("in")) == tin and ratio(target.get("out")) == tout,
                            "Reuse declaration must match the actual target interval")
                    declared = declaration.get("effect_type") == mode and bool(declaration.get("creative_purpose"))
                legacy_declared = r.get("intent_note") and r.get("repeat_of") in by_id
                require((declared or legacy_declared) and r.get("authorization_ref"),
                        "Creative reuse requires source_identity, target_interval, effect_type, creative_purpose and authorization_ref (profile policy may authorize)")
                accepted.append({"shot": r["id"], "type": mode, "status": "declared_design_not_automatic_visual_approval"})
            boundary = a.get("boundary", {})
            if boundary.get("status") != "verified":
                candidates.append({"shot": r["id"], "code": "boundary_review_pending"})
            for cut in boundary.get("cuts", []):
                if pin < ratio(cut) < pout:
                    candidates.append({"shot": r["id"], "code": "internal_cut_candidate", "proxy_frame": cut,
                                       "requires_human_confirmation": True})
            selected.append({"row": r, "asset": a, "source_path": str(resolve(source["path"], project.base)),
                             "source_in": si + pin / total * (so - si), "source_out": si + pout / total * (so - si),
                             "scene": root(sid), "pin": pin, "pout": pout, "tin": tin, "tout": tout, "pfps": pfps})
        except (WorkflowError, KeyError) as exc:
            errors.append({"shot": r["id"], "code": "invalid_interval_or_clock", "message": str(exc)})
    def composite_ok(a, b):
        r, base = a["row"], b["row"]
        if r.get("design_intent") != "same_instant_composite" or r.get("composite_of") != base["id"]:
            return False
        if not ((r.get("sync_verified") or r.get("synced")) and r.get("transform_same_position")):
            return False
        if a["source_path"] != b["source_path"] or a["scene"] != b["scene"]:
            return False
        ti, to = max(a["tin"], b["tin"]), min(a["tout"], b["tout"])
        if ti >= to:
            return False
        def at(z, t):
            return z["source_in"] + (t-z["tin"]) / (z["tout"]-z["tin"]) * (z["source_out"]-z["source_in"])
        return at(a, ti) == at(b, ti) and at(a, to) == at(b, to)
    for i, a in enumerate(selected):
        for b in selected[i+1:]:
            ra, rb = a["row"], b["row"]
            overlap = a["source_path"] == b["source_path"] and max(a["source_in"], b["source_in"]) < min(a["source_out"], b["source_out"])
            same_scene = a["scene"] == b["scene"]
            if not overlap and not same_scene:
                continue
            pair = [ra["id"], rb["id"]]
            if composite_ok(a, b) or composite_ok(b, a):
                accepted.append({"shots": pair, "type": "same_instant_composite", "status": "clock_and_position_declaration_checked"}); continue
            deliberate = any(r.get("design_intent") in CREATIVE and r.get("authorization_ref") and
                             (r.get("repeat_of") == other["id"] and r.get("intent_note") or
                              r.get("reuse_declaration", {}).get("source_identity") == r.get("asset_id") and
                              r.get("reuse_declaration", {}).get("creative_purpose")) for r, other in [(ra, rb), (rb, ra)])
            if deliberate and project.rules.get("reuse_policy") == "allow_declared":
                continue
            same_occurrence = ra.get("occurrence_id") and ra.get("occurrence_id") == rb.get("occurrence_id")
            if same_occurrence and not overlap and a["source_path"] == b["source_path"]:
                continue
            if overlap:
                errors.append({"shots": pair, "code": "unintentional_source_overlap"})
            elif same_scene:
                candidates.append({"shots": pair, "code": "cross_master_alias_or_same_camera_reuse", "requires_human_confirmation": True})
    for r in shots:
        if r.get("design_intent") == "same_instant_composite" and not any(x.get("type") == "same_instant_composite" and r["id"] in x.get("shots", []) for x in accepted):
            errors.append({"shot": r["id"], "code": "composite_not_synchronized"})
    return {"command": "audit-usage", "valid": not errors, "review_complete": not errors and not candidates,
            "errors": errors, "candidates": candidates, "accepted_design": accepted,
            "limitations": ["Canonical IDs/aliases are declarations, not semantic identification by the tool",
                            "Natural animation held drawings are not labeled freeze", "Candidates require visual review; no perceptual acceptance is inferred"]}


def run_tool(command):
    env = os.environ.copy()
    env.pop("FFREPORT", None)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    try:
        return subprocess.run([str(x) for x in command], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              check=True, timeout=55, shell=False, env=env)
    except (OSError, subprocess.SubprocessError) as exc:
        text = getattr(exc, "stderr", b"")
        raise WorkflowError("Local media tool failed: " + str(exc) + " " + text.decode("utf-8", "replace")[-1800:])


def local_tool(explicit, name):
    p = explicit or shutil.which(name)
    require(p and Path(p).is_file(), "Local %s is required; supply its path (no automatic download)" % name)
    return str(Path(p).resolve())


def decode_video(path, ffmpeg):
    require(Path(path).is_file(), "Input media must be an existing local file")
    r = run_tool([ffmpeg, "-nostdin", "-v", "info", "-i", path, "-map", "0:v:0", "-an", "-sn", "-dn",
                  "-vf", "scale=32:18:flags=area,format=gray,showinfo", "-fps_mode", "passthrough",
                  "-pix_fmt", "gray", "-f", "rawvideo", "pipe:1"])
    text = r.stderr.decode("utf-8", "replace")
    match = re.search(r"config in time_base:\s*(\d+/\d+),\s*frame_rate:\s*([\d/]+)", text)
    require(match, "Decoder did not expose a real input time base")
    tb = ratio(match.group(1), positive=True)
    advertised = ratio(match.group(2))
    pts = [int(m.group(1)) * tb for m in re.finditer(r"\bn:\s*\d+\s+pts:\s*(-?\d+)\s+pts_time:", text)]
    require(len(r.stdout) % 576 == 0 and len(pts) == len(r.stdout)//576, "Decoded pixels and timestamps disagree")
    require(pts, "No video frames decoded")
    deltas = [b-a for a, b in zip(pts, pts[1:])]
    return {"pts": pts, "pixels": r.stdout, "time_base": tb, "advertised_fps": advertised,
            "actual_fps": Fraction(len(pts)-1, 1)/(pts[-1]-pts[0]) if len(pts)>1 and pts[-1]>pts[0] else None,
            "deltas": deltas, "decoder_stderr_errors": [x for x in text.splitlines() if "Error while" in x or "corrupt" in x.lower()]}


def probe(path, ffmpeg, ffprobe=None, declared_fps=None):
    d = decode_video(path, ffmpeg)
    actual = d["actual_fps"]
    report = {"command": "probe", "path": str(Path(path).resolve()), "decoded_frames": len(d["pts"]),
              "time_base": str(d["time_base"]), "advertised_fps": str(d["advertised_fps"]),
              "actual_fps": str(actual) if actual else None, "first_pts_seconds": fraction_json(d["pts"][0]),
              "last_pts_seconds": fraction_json(d["pts"][-1]),
              "monotonic_pts": all(x>0 for x in d["deltas"]), "constant_pts_step": len(set(d["deltas"])) <= 1,
              "decode_errors": d["decoder_stderr_errors"], "media_modified": False}
    if ffprobe:
        r = run_tool([ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", path])
        meta = json.loads(r.stdout.decode("utf-8"))
        report["streams"] = meta.get("streams", [])
        report["format"] = meta.get("format", {})
    if declared_fps is not None:
        expected = ratio(declared_fps, positive=True)
        report["declared_fps"] = str(expected)
        report["declared_fps_matches_decoded"] = bool(d["deltas"]) and all(x == 1/expected for x in d["deltas"])
    report["valid"] = report["monotonic_pts"] and not report["decode_errors"] and report.get("declared_fps_matches_decoded", True)
    return report


def sample_metrics(samples, channels=2):
    finite = [x for x in samples if math.isfinite(x)]
    peak = max((abs(x) for x in finite), default=0)
    rms = math.sqrt(math.fsum(x*x for x in finite)/len(finite)) if finite else 0
    return {"samples_per_channel": len(samples)//channels, "peak_dbfs": 20*math.log10(peak) if peak else None,
            "rms_dbfs": 20*math.log10(rms) if rms else None, "nonfinite_samples": len(samples)-len(finite),
            "samples_at_or_above_0dbfs": sum(abs(x)>=1 for x in finite)}


def audit_export(path, ffmpeg, fps, expected_end_frame=None, black_threshold=1, cut_threshold=32):
    target_fps = ratio(fps, positive=True)
    require(0 <= black_threshold <= 255 and 0 <= cut_threshold <= 255, "Detector thresholds must be 0–255")
    d = decode_video(path, ffmpeg)
    raw = d["pixels"]; means, cuts, black = [], [], []
    for i in range(len(d["pts"])):
        frame = raw[i*576:(i+1)*576]; mean = sum(frame)/576; means.append(mean)
        if mean <= black_threshold:
            black.append({"frame": i, "mean_gray": mean, "status": "candidate", "requires_human_confirmation": True})
        if i:
            prev = raw[(i-1)*576:i*576]; delta = sum(abs(a-b) for a,b in zip(frame, prev))/576
            if delta >= cut_threshold:
                cuts.append({"first_new_frame": i, "mean_absolute_delta": delta, "status": "candidate", "requires_human_confirmation": True})
    audio_error = None
    try:
        ar = run_tool([ffmpeg, "-nostdin", "-v", "error", "-i", path, "-vn", "-sn", "-dn", "-map", "0:a:0",
                       "-ar", "48000", "-ac", "2", "-c:a", "pcm_f32le", "-f", "f32le", "pipe:1"])
        values = array.array("f"); values.frombytes(ar.stdout)
        if sys.byteorder != "little":
            values.byteswap()
        require(len(values)%2 == 0, "Audio sample channels disagree")
        audio = sample_metrics(values)
        audio.update(sample_rate=48000, channels=2, decoded_duration_seconds=len(values)/96000,
                     human_listening_verified=False, cut_word_status="not_determined_by_numeric_analysis")
        if expected_end_frame is not None:
            end = ratio(expected_end_frame)/target_fps
            first_after = math.ceil(end*48000)
            audio["after_expected_stop"] = sample_metrics(values[first_after*2:])
            audio["after_expected_stop"]["duration_seconds"] = max(0,len(values)//2-first_after)/48000
            audio["after_expected_stop"]["classification"] = "measured_residual_not_automatic_audible_extra_content"
    except WorkflowError as exc:
        audio_error = str(exc); audio = {"available": False, "error": audio_error, "human_listening_verified": False}
    frame_count = len(d["pts"])
    expected = ratio(expected_end_frame) if expected_end_frame is not None else None
    require(expected is None or expected.denominator == 1 and expected > 0, "Expected end frame must be a positive integer")
    technical = all(x == 1/target_fps for x in d["deltas"]) and d["pts"][0] == 0 and not d["decoder_stderr_errors"]
    technical = technical and (expected is None or frame_count == expected)
    technical = technical and not audio.get("nonfinite_samples", 0) and not audio.get("samples_at_or_above_0dbfs", 0)
    return {"command": "audit-export", "path": str(Path(path).resolve()), "sha256": sha256_file(path),
            "technical_pass": bool(technical), "decoded_frames": frame_count, "actual_fps": str(d["actual_fps"]),
            "last_visible_frame": frame_count-1, "video_end_exclusive_frame": frame_count,
            "expected_end_exclusive_frame": int(expected) if expected is not None else None,
            "video_end_seconds": fraction_json(d["pts"][-1]+1/target_fps),
            "black_candidates": black, "scene_change_candidates": cuts, "audio": audio,
            "human_visual_review_verified": False, "human_listening_verified": False,
            "limitations": ["Black/cut candidates are not automatic bad-frame verdicts", "No semantic duplicate detector or word-completeness hearing", "Silent/missing audio is reported, not silently certified"]}


def new_output(path=None, project=None, extra_protected=()):
    if path is None:
        base = project.base if project else Path.cwd()
        root = project.state.get("paths", {}).get("review_root", "review") if project else "review"
        path = resolve(root, base) / (dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S_%fZ") + "_" + uuid.uuid4().hex[:8])
    dest = Path(path).expanduser().absolute()
    protected = list(extra_protected) + (project.protected() if project else [])
    for p in protected:
        require(not is_inside(dest, p), "Output is inside a frozen/native/input directory: " + str(p))
    require(not dest.exists() and not dest.is_symlink(), "Output already exists; append-only outputs never overwrite: " + str(dest))
    dest.mkdir(parents=True, exist_ok=False)
    return dest


def write_json(path, content):
    # Exclusive create: a report is never a replacement for an existing report/state.
    with Path(path).open("x", encoding="utf-8", newline="\n") as f:
        json.dump(content, f, ensure_ascii=False, indent=2)
        f.write("\n")


def handoff(project):
    validation = validate_state(project)
    s = project.state
    current_id = s.get("latest_native_artifact_id")
    native = project.artifacts.get(current_id, {})
    return {"command": "handoff", "schema_version": VERSION, "project_id": s.get("project_id"),
            "generated_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
            "source_state_sha256": sha256_file(project.state_path), "generated_from_single_state": True,
            "state_entrypoint": str(project.state_path), "phase": s.get("phase"), "validation": validation,
            "current_native": native, "current_export": project.artifacts.get(s.get("latest_export_artifact_id")),
            "design_reference": str(project.plan_path) if project.plan_path else None,
            "media_manifest": str(project.manifest_path) if project.manifest_path else None,
            "resolved_rules_snapshot": project.rules, "rule_sources": project.rule_sources,
            "known_issues": s.get("known_issues", []), "pending_user_decisions": s.get("pending_user_decisions", []),
            "next_actions": s.get("next_actions", []), "editing_owner": s.get("editing_owner"),
            "current_permission_scope": s.get("authorization_scope", "Not inferred: follow current user instruction"),
            "verified_capabilities": s.get("verified_capabilities", []),
            "warning": "Encrypted native bytes are latest authority; older plaintext is only reference. Presence of old caches/audio does not prove enabled state. This handoff grants no execution or external permission."}


def handoff_markdown(report):
    native, export = report.get("current_native") or {}, report.get("current_export") or {}
    validation = report["validation"]
    lines = ["# 剪映项目接手快照", "", "项目："+str(report.get("project_id")),
             "生成时间（UTC）："+report["generated_utc"], "状态入口："+report["state_entrypoint"],
             "输入状态SHA256："+report["source_state_sha256"], "当前阶段："+str(report.get("phase")), "",
             "这是从唯一状态生成的快照，不会替换源状态、授予执行权限或自动提升为终版。工具没有进行真实视听；用户听审是否完成须看当前导出的用户确认记录。", "",
             "## 当前工程与导出", "", "原生工程："+str(native.get("path")),
             "完整工程目录："+str(native.get("project_directory",native.get("path"))),
             "原生角色／权威："+str(native.get("role"))+" / "+str(native.get("authority")),
             "加密原生内容："+str(native.get("encrypted", False)),
             "当前导出："+str(export.get("path")), "",
             "旧明文和历史EDL仅供参考，不能冒充最新原生状态；旧音轨/缓存存在不等于启用。冻结素材不得原地重编码。", "",
             "## 检查状态", "", "状态验证："+("通过" if validation["valid"] else "未通过"),
             "迁移与原生绝对路径重绑定不由此快照自动验证。"]
    for item in validation.get("errors", []):
        lines.append("- 未通过："+item["code"]+" — "+item["message"])
    for warning in validation.get("warnings", []):
        lines.append("- 提示："+warning)
    for label,key in [("已知问题","known_issues"),("待用户确认","pending_user_decisions"),("下一步","next_actions")]:
        lines += ["", "## "+label, ""]
        values=report.get(key) or []
        lines += ["- "+(v if isinstance(v,str) else json.dumps(v,ensure_ascii=False)) for v in values] or ["状态中未登记。"]
    lines += ["", "## 权限与责任", "", "当前授权范围："+str(report.get("current_permission_scope")),
              "工程/UI写入负责人："+json.dumps(report.get("editing_owner"),ensure_ascii=False),
              "", "个人默认规则和项目覆盖来源保存在状态与profile中；本快照中的resolved规则仅为解释结果。", ""]
    return "\n".join(lines)


def write_handoff(path, report):
    with Path(path).open("x",encoding="utf-8",newline="\n") as f:
        f.write(handoff_markdown(report))


def invalidate_evidence(project, changes):
    require(changes.get("reason"), "Changes need an explicit reason")
    s = copy.deepcopy(project.state)
    changed_assets = set(changes.get("changed_asset_ids", []))
    changed_artifacts = set(changes.get("changed_artifact_ids", []))
    target_ranges = changes.get("target_ranges", [])
    for r in target_ranges:
        require(r.get("clock"), "Changed range must name its clock")
        fps = project.state.get("clocks", {}).get(r["clock"], {}).get("fps")
        interval(r["in"], r["out"], r["unit"], fps)
    overrides = changes.get("project_overrides", changes.get("constraints", {}).get("project_overrides", {}))
    old_rev = s.get("constraints", {}).get("revision")
    require(isinstance(old_rev, int), "Cannot invalidate without an integer constraints revision")
    require(overrides or changed_assets or changed_artifacts or target_ranges, "Changes must name rules/assets/artifacts/ranges")
    new_rev = old_rev+1
    s["constraints"]["revision"] = new_rev
    s["constraints"].setdefault("project_overrides", {}).update(overrides)
    rule_kinds = {
        "reuse_policy": {"usage_audit"},
        "native_editable": {"native_editability", "visual_review"},
        "native_character_matting_only": {"native_editability", "visual_review"},
        "human_listening_required_for_final": {"human_listening"},
        "end_with_music": {"audio_numeric", "human_listening", "visual_review"},
    }
    affected = set()
    unknown = False
    for key in overrides:
        if key not in rule_kinds:
            unknown = True
        affected.update(rule_kinds.get(key, set()))
    invalidated, retained = [], []
    for e in s.get("evidence", []):
        if e.get("status") in {"invalidated", "pending", "candidate"}:
            continue
        scope = e.get("scope", {})
        hit = unknown or e.get("kind") in affected
        hit |= bool(changed_assets.intersection(scope.get("asset_ids", [])))
        hit |= bool(changed_artifacts.intersection(scope.get("artifact_ids", [])))
        hit |= bool(changed_artifacts.intersection(e.get("fingerprints", {})))
        for c in target_ranges:
            for r in scope.get("target_ranges", []):
                if c.get("clock") != r.get("clock"):
                    # Unknown cross-clock scope cannot safely remain verified.
                    hit = True; continue
                fps = project.state.get("clocks", {}).get(c["clock"], {}).get("fps")
                ci, co = interval(c["in"], c["out"], c["unit"], fps)
                ri, ro = interval(r["in"], r["out"], r["unit"], fps)
                hit |= max(ci, ri) < min(co, ro)
        # Whole-export evidence cannot survive a material/timeline edit just because it lacked detailed scopes.
        if (changed_assets or target_ranges) and e.get("kind") in {"human_listening", "visual_review", "audio_numeric", "export_decode"}:
            hit = True
        if (changed_assets or changed_artifacts or target_ranges) and not scope and not e.get("fingerprints"):
            hit = True
        if hit:
            e.update(status="invalidated", invalidation_reason=changes["reason"], invalidated_at_revision=new_rev)
            invalidated.append(e.get("id"))
        else:
            e.update(carried_forward_from_revision=e.get("constraints_revision"), constraints_revision=new_rev)
            retained.append(e.get("id"))
    if s.get("phase") == "final":
        s["phase"] = "review"
    s["last_updated_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
    # A new state file in a new output directory still refers to the original project.
    s["project_root"] = str(project.base)
    return {"command": "invalidate-evidence", "new_state": s, "invalidated_evidence_ids": invalidated,
            "retained_evidence_ids": retained, "source_state_modified": False,
            "note": "This output is a proposed new state, not an installed state or approval to edit media"}


def safe_id(value):
    require(isinstance(value, str) and value and value not in {".", ".."} and not any(c in value for c in '/\\:\0'),
            "IDs must be single safe filename components")
    return value


def pack_plan(project):
    validation = validate_state(project)
    require(validation["valid"], "Cannot package invalid current state: " + json.dumps(validation["errors"], ensure_ascii=False))
    native_id = project.state["latest_native_artifact_id"]
    native = project.artifacts[native_id]
    native_dir = project.native_directory(native)
    copies = []; index = []; font_refs = []; mapped = {}
    def add(path, dest, kind):
        p = Path(path).resolve()
        require(p.exists(), "Missing dependency: " + str(p))
        tree_files(p)  # Reject linked/junction escapes before creating an output.
        if p in mapped:
            return mapped[p]
        for known, relative_dir in list(mapped.items()):
            if known.is_dir() and is_inside(p, known):
                relative = (Path(relative_dir)/p.relative_to(known)).as_posix()
                mapped[p] = relative
                return relative
        relative = str(Path(dest).as_posix())
        require(not Path(relative).is_absolute() and ".." not in Path(relative).parts, "Unsafe package destination")
        mapped[p] = relative; copies.append({"source": str(p), "destination": relative, "kind": kind})
        return relative
    add(native_dir, "native/"+safe_id(native_id), "complete_native_project_with_caches")
    # A relocated project must not point back to the source workflow directory.
    # Bundle this reviewed CLI and its optional music helper as immutable tools.
    add(Path(__file__), "workflow_toolkit/video_workflow.py", "workflow_tool")
    music_helper = Path(__file__).with_name("music_analysis.py")
    if music_helper.is_file():
        add(music_helper, "workflow_toolkit/music_analysis.py", "workflow_tool")
    agents_file = project.base/"AGENTS.md"
    if agents_file.is_file():
        add(agents_file, "AGENTS.md", "project_entrypoint")
    snapshot = project.state.get("constraints", {}).get("snapshot_path")
    if snapshot:
        add(resolve(snapshot, project.base), "constraints/"+Path(snapshot).name, "derived_constraint_snapshot")
    for artifact in project.artifacts.values():
        if artifact.get("project_directory") and artifact["id"] != native_id:
            directory = resolve(artifact["project_directory"], project.base)
            add(directory, "native_history/"+safe_id(artifact["id"]), "complete_historical_native_backup")
    for a in project.artifacts.values():
        p = project.artifact_path(a)
        if a.get("role") not in {"original_media", "original_source"}:
            add(p, "artifacts/"+safe_id(a["id"])+"/"+p.name, a.get("role", "artifact"))
    provided_index = project.manifest.get("source_index", [])
    if isinstance(provided_index, str):
        path = resolve(provided_index, project.base)
        add(path, "source_index/"+path.name, "original_source_index_not_original_media")
        provided_records = read_json(path)
    else:
        provided_records = provided_index
    if isinstance(provided_records, dict):
        provided_records = provided_records.get("sources", provided_records.get("files", []))
    require(isinstance(provided_records,list), "Original source index must be a path, list, or sources/files object")
    for record in provided_records:
        require(isinstance(record,dict), "Source index records must be objects")
        index.append({**record, "copied":False, "fingerprint_kind":"declared_partial_not_full" if record.get("head_tail_1MiB_sha256") else "provided_not_recomputed"})
    evidence_paths = []
    for e in project.state.get("evidence", []):
        for key in ("report_path", "waveform_path", "context_path"):
            if e.get(key):
                evidence_paths.append((e, key, e[key]))
        for key in ("contact_sheet_paths", "evidence_files"):
            for value in e.get(key, []):
                evidence_paths.append((e, key, value))
    missing_evidence = []
    for e, key, value in evidence_paths:
        p = resolve(value, project.base)
        if not p.exists():
            require(e.get("status") != "verified", "Verified evidence file is missing: " + str(p))
            missing_evidence.append({"evidence_id": e.get("id"), "path": str(p)}); continue
        token = uuid.uuid5(uuid.NAMESPACE_URL, str(p)).hex[:12]
        add(p, "evidence/"+token+"/"+p.name, "audit_evidence")
    used = {r.get("asset_id") for key in ("shots", "overlays") for r in project.plan.get(key, []) if r.get("asset_id")}
    used.update(project.plan.get("font_asset_ids", []))
    superset = project.state.get("dependency_status", project.manifest.get("dependency_status")) != "verified_active"
    for aid, a in project.assets.items():
        if aid not in used and not superset:
            continue
        p = project.asset_path(a)
        if a.get("kind") in {"original_media", "original_source"}:
            index.append({"id": aid, "path": str(p), "bytes": p.stat().st_size if p.exists() else None,
                          "fingerprint": a.get("sha256"), "fingerprint_kind": "declared_not_recomputed", "copied": False}); continue
        if a.get("kind") == "font" and not a.get("redistributable"):
            font_refs.append({"id": aid, "path": str(p), "license_path": a.get("license_path"), "embedded": False,
                              "reason": "No redistribution permission declared"}); continue
        if a.get("kind") == "font":
            require(a.get("license_path"), "Font embedding requires a license_path")
            lp = resolve(a["license_path"], project.base)
            add(lp, "licenses/"+safe_id(aid)+"/"+lp.name, "font_license")
        add(p, "assets/"+safe_id(aid)+"/"+p.name, a.get("kind", "used_media"))
        source = a.get("source", {})
        if source.get("path"):
            sp = resolve(source["path"], project.base)
            index.append({"id": aid, "path": str(sp), "in": source.get("in"), "out": source.get("out"),
                          "unit": source.get("unit"), "clock": source.get("clock"),
                          "fingerprint_kind": "not_hashed_by_pack", "copied": False})
    for dep in project.dependencies():
        p = resolve(dep["path"], project.base); did = safe_id(dep.get("id", p.stem))
        if dep.get("kind") in {"original_media", "original_source"}:
            index.append({**dep, "path": str(p), "copied": False}); continue
        if dep.get("kind") == "font":
            if not dep.get("redistributable"):
                font_refs.append({**dep, "embedded": False}); continue
            require(dep.get("license_path"), "Font dependency needs redistribution license_path")
            lp = resolve(dep["license_path"], project.base)
            add(lp, "licenses/"+did+"/"+lp.name, "font_license")
        if dep.get("sha256"):
            require(fingerprint(p) == dep["sha256"], "Frozen dependency drift: " + did)
        if is_inside(p, native_dir):
            mapped[p] = str(Path("native")/native_id/p.relative_to(native_dir))
        else:
            add(p, "dependencies/"+did+"/"+p.name, dep.get("kind", "dependency"))
    for cache in native.get("cache_paths", []):
        p = resolve(cache, project.base)
        if not is_inside(p, native_dir):
            add(p, "caches/"+uuid.uuid5(uuid.NAMESPACE_URL, str(p)).hex[:12]+"/"+p.name, "external_native_cache")
    # Explicit plan audio files are dependencies even if not listed as video assets.
    for e in project.plan.get("original_audio_events", []) + project.plan.get("music_cuts", []):
        if e.get("path"):
            p = resolve(e["path"], project.base)
            add(p, "audio/"+safe_id(e.get("id", p.stem))+"/"+p.name, "used_audio")
    return {"command": "pack-project", "project_id": project.state["project_id"], "copies": copies,
            "original_source_index": index, "font_references": font_refs,
            "missing_pending_evidence": missing_evidence,
            "dependency_status": "conservative_superset" if superset else "declared_verified_active",
            "migration_verified": False, "native_paths_relinked": False,
            "warning": "Complete native bytes/caches are preserved; encrypted internal absolute paths are not rewritten. Opening/relinking on another computer remains required.",
            "validation": validation, "_mapped": mapped}


def pack_project(project, output_path=None, no_write=False, extra_protected=()):
    plan = pack_plan(project)
    mapped = plan.pop("_mapped")
    if no_write:
        plan["no_write"] = True
        return plan
    sources = [Path(c["source"]) for c in plan["copies"]]
    dest = new_output(output_path, project, sources + list(extra_protected))
    # A failure leaves a new partial package for inspection, never cleans source or overwrites an old package.
    for c in plan["copies"]:
        p = Path(c["source"]); target = dest/c["destination"]
        target.parent.mkdir(parents=True, exist_ok=True)
        before = fingerprint(p)
        if p.is_dir():
            shutil.copytree(p, target, symlinks=False)
        else:
            with p.open("rb") as src, target.open("xb") as dst:
                shutil.copyfileobj(src, dst, length=1048576)
        after = fingerprint(p); copied = fingerprint(target)
        require(before == after == copied, "Source changed during package/copy mismatch: " + str(p))
        c["sha256"] = copied
    new_state = copy.deepcopy(project.state)
    new_state["project_root"] = "."
    new_state["workflow_toolkit_path"] = "workflow_toolkit/video_workflow.py"
    new_state.setdefault("editing_owner", {})["handoff_ref"] = "HANDOFF.md"
    new_state.setdefault("paths", {}).update(media_manifest="assets/MEDIA_MANIFEST.json", edit_plan="design/EDIT_PLAN.json")
    new_state.pop("media_manifest", None); new_state.pop("edit_plan", None)
    def relocate(p):
        original = resolve(p, project.base)
        if original in mapped:
            return Path(mapped[original]).as_posix()
        return str(original)
    if new_state.get("constraints", {}).get("snapshot_path"):
        new_state["constraints"]["snapshot_path"] = relocate(new_state["constraints"]["snapshot_path"])
    for a in new_state.get("artifacts", []):
        a["path"] = relocate(a["path"])
        if a.get("project_directory"):
            a["project_directory"] = relocate(a["project_directory"])
        if a.get("cache_paths"):
            a["cache_paths"] = [relocate(p) for p in a["cache_paths"]]
    if new_state.get("paths", {}).get("native_project"):
        new_state["paths"]["native_project"] = relocate(new_state["paths"]["native_project"])
    for dep in new_state.get("dependencies", []):
        dep["path"] = relocate(dep["path"])
        if dep.get("license_path"):
            dep["license_path"] = relocate(dep["license_path"])
    for e in new_state.get("evidence", []):
        for key in ("report_path", "waveform_path", "context_path"):
            if e.get(key):
                e[key] = relocate(e[key])
        for key in ("contact_sheet_paths", "evidence_files"):
            if e.get(key):
                e[key] = [relocate(p) for p in e[key]]
    manifest = copy.deepcopy(project.manifest)
    for a in manifest.get("assets", []):
        a["path"] = relocate(a["path"])
        if a.get("source", {}).get("path"):
            a["source"]["path"] = str(resolve(a["source"]["path"], project.base))
        if a.get("license_path"):
            a["license_path"] = relocate(a["license_path"])
    for dep in manifest.get("dependencies", []):
        dep["path"] = relocate(dep["path"])
        if dep.get("license_path"):
            dep["license_path"] = relocate(dep["license_path"])
    if isinstance(manifest.get("source_index"), str):
        manifest["source_index"] = relocate(manifest["source_index"])
    edit_plan = copy.deepcopy(project.plan)
    for key in ("original_audio_events", "music_cuts"):
        for row in edit_plan.get(key, []):
            if row.get("path"):
                row["path"] = relocate(row["path"])
    for name in ("assets", "design"):
        (dest/name).mkdir(exist_ok=True)
    if project.profile_path.exists():
        new_state["profile_path"] = "user-profile.json"
        with project.profile_path.open("rb") as src, (dest/"user-profile.json").open("xb") as dst:
            shutil.copyfileobj(src, dst)
    new_state["protected_paths"] = [relocate(p) for p in new_state.get("protected_paths", [])]
    write_json(dest/"PROJECT_STATE.json", new_state)
    write_json(dest/"assets/MEDIA_MANIFEST.json", manifest)
    write_json(dest/"design/EDIT_PLAN.json", edit_plan)
    write_json(dest/"ORIGINAL_SOURCE_INDEX.json", {"sources": plan["original_source_index"], "originals_copied": False,
                                                  "provided_index": project.manifest.get("source_index", [])})
    handoff_report = handoff(Project(dest/"PROJECT_STATE.json"))
    write_json(dest/"HANDOFF.json", handoff_report)
    write_handoff(dest/"HANDOFF.md", handoff_report)
    with project.state_path.open("rb") as src, (dest/"SOURCE_STATE.json").open("xb") as dst:
        shutil.copyfileobj(src, dst)
    plan.update(output_directory=str(dest), snapshot_ready=True)
    write_json(dest/"PACKAGE_REPORT.json", plan)
    hashes = [{"path": f.relative_to(dest).as_posix(), "bytes": f.stat().st_size, "sha256": sha256_file(f)} for f in tree_files(dest)]
    write_json(dest/"PACKAGE_HASHES.json", {"files": hashes, "excludes_self": True, "algorithm": "sha256_full_file"})
    return plan


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--version", action="version", version=VERSION)
    subs = p.add_subparsers(dest="command", required=True)
    def common(s, state_required=False):
        s.add_argument("--state", required=state_required, help="Single PROJECT_STATE.json entrypoint")
        s.add_argument("--profile", help="Explicit user-profile path (otherwise state/profile install)")
        s.add_argument("--out", help="New output directory; existing/native/frozen destinations refused")
        s.add_argument("--no-write", action="store_true", help="Memory/stdout only; do not create files or dirs")
        s.add_argument("--protected-path", action="append", default=[], help="Additional frozen/source directory")
    s = subs.add_parser("probe", help="Full decode PTS/fps/frame count; no declared-metadata shortcut")
    s.add_argument("path"); s.add_argument("--ffmpeg"); s.add_argument("--ffprobe"); s.add_argument("--declared-fps")
    common(s)
    s = subs.add_parser("audit-usage", help="Intervals, safe cameras, aliases and explicitly declared creative reuse")
    common(s, True)
    s = subs.add_parser("map-clock", help="Exact Fraction clock conversion; no implicit source/target equivalence")
    s.add_argument("--value", required=True); s.add_argument("--from-clock", required=True); s.add_argument("--to-clock", required=True)
    s.add_argument("--from-unit", choices=sorted(UNITS), required=True); s.add_argument("--to-unit", choices=sorted(UNITS), required=True)
    s.add_argument("--fps"); s.add_argument("--mapping", help="JSON source/target mapping for different clocks")
    common(s)
    s = subs.add_parser("audit-export", help="Full decode, candidate dark/cut frames, audio numbers and stopline")
    s.add_argument("path"); s.add_argument("--ffmpeg"); s.add_argument("--ffprobe")
    s.add_argument("--fps"); s.add_argument("--expected-end-frame", type=int)
    s.add_argument("--black-threshold", type=float, default=1); s.add_argument("--cut-threshold", type=float, default=32)
    common(s)
    s = subs.add_parser("pack-project", help="Copy complete native/cache/dependencies to a new snapshot, no draft generation")
    common(s, True)
    s = subs.add_parser("handoff", help="Generate handoff from the unique current state, not old plaintext")
    common(s, True)
    s = subs.add_parser("validate-state", help="Authority, fingerprints, evidence revision and user-listening final gate")
    common(s, True)
    s = subs.add_parser("invalidate-evidence", help="Propose a new state; original state is never written")
    s.add_argument("--changes", required=True, help="JSON reason/overrides/changed IDs/explicit target_ranges")
    common(s, True)
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    project = Project(args.state, args.profile) if args.state else None
    cmd = args.command
    if cmd == "probe":
        ff = local_tool(args.ffmpeg, "ffmpeg")
        fp = local_tool(args.ffprobe, "ffprobe") if args.ffprobe else None
        report = probe(args.path, ff, fp, args.declared_fps)
    elif cmd == "audit-usage":
        report = audit_usage(project)
    elif cmd == "map-clock":
        report = map_clock(args.value, args.from_clock, args.to_clock, args.from_unit, args.to_unit,
                           args.fps, read_json(args.mapping) if args.mapping else None)
        report["command"] = cmd
    elif cmd == "audit-export":
        ff = local_tool(args.ffmpeg, "ffmpeg")
        fps = args.fps if args.fps is not None else project.fps() if project else None
        report = audit_export(args.path, ff, fps, args.expected_end_frame, args.black_threshold, args.cut_threshold)
        if args.ffprobe:
            report["container_metadata"] = json.loads(run_tool([local_tool(args.ffprobe, "ffprobe"), "-v", "error", "-show_streams", "-show_format", "-of", "json", args.path]).stdout)
    elif cmd == "pack-project":
        if not args.no_write and args.out is None:
            raise WorkflowError("pack-project requires an explicit NEW --out directory")
        report = pack_project(project, args.out, args.no_write, args.protected_path)
    elif cmd == "handoff":
        report = handoff(project)
    elif cmd == "validate-state":
        report = validate_state(project)
    elif cmd == "invalidate-evidence":
        report = invalidate_evidence(project, read_json(args.changes))
    else:
        raise WorkflowError("Unsupported command")
    report["no_write"] = args.no_write
    if not args.no_write and cmd != "pack-project":
        extra = list(args.protected_path)
        if cmd in {"probe", "audit-export"}:
            extra.append(Path(args.path).resolve())
        dest = new_output(args.out, project, extra)
        report["output_directory"] = str(dest)
        if cmd == "handoff":
            report["handoff_path"] = str(dest/"HANDOFF.md")
            write_handoff(dest/"HANDOFF.md", report)
        write_json(dest/("PROJECT_STATE.json" if cmd == "invalidate-evidence" else "REPORT.json"), report["new_state"] if cmd == "invalidate-evidence" else report)
        if cmd == "invalidate-evidence":
            write_json(dest/"INVALIDATION_REPORT.json", {k:v for k,v in report.items() if k != "new_state"})
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 1 if report.get("valid") is False or report.get("technical_pass") is False or cmd == "handoff" and not report["validation"]["valid"] else 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        raise SystemExit(main())
    except (WorkflowError, KeyError, json.JSONDecodeError, OSError) as exc:
        print(json.dumps({"error": str(exc), "writes_to_inputs": False}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(2)
