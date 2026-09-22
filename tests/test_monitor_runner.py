"""Quota and failure isolation tests; no network or real model calls."""
import argparse
import copy
import fcntl
import json
from pathlib import Path
import subprocess
import sys

import pytest

from monitoring import runner as R


def quota(primary=15, secondary=35):
    return {"ordinaryUsageAllowed": True, "rateLimitsByLimitId": {"codex": {
        "primary": {"usedPercent": primary, "windowDurationMins": 300, "resetsAt": 2_000_000_000},
        "secondary": {"usedPercent": secondary, "windowDurationMins": 10_080, "resetsAt": 2_001_000_000}}}}


@pytest.mark.parametrize("primary,secondary,status", [
    (89, 89, "ready"), (90, 0, "paused_quota"), (0, 90, "paused_quota"),
    (100, 100, "paused_quota"), (None, 10, "paused_quota_unknown"),
    (float("nan"), 10, "paused_quota_unknown"), (True, 10, "paused_quota_unknown"),
])
def test_quota_reserves_both_windows(primary, secondary, status):
    payload = quota(primary, secondary)
    payload["accountId"] = "must-not-persist"
    gate = R.quota_gate(payload, 10)
    assert gate["status"] == status
    assert "must-not-persist" not in json.dumps(gate)


def test_quota_prefers_codex_bucket_and_fails_closed_when_unknown():
    payload = quota(90, 15)
    payload["rateLimits"] = quota(0, 0)["rateLimitsByLimitId"]["codex"]
    assert R.quota_gate(payload, 10)["status"] == "paused_quota"
    assert R.quota_gate({}, 10)["status"] == "paused_quota_unknown"
    payload = quota(0, 0)
    payload["ordinaryUsageAllowed"] = False
    assert R.quota_gate(payload, 10)["status"] == "paused_quota"


def assessment():
    return {"health": "ok", "summary": "Kurallar korunuyor.", "observations": [], "position_reviews": []}


@pytest.fixture
def setup_observer(tmp_path, monkeypatch):
    state = tmp_path / "bot-state.json"
    state.write_text('{"balance": 1000}')
    args = argparse.Namespace(state=state, log=None, output_dir=tmp_path / "observer",
                              codex="never-run", model="gpt-6-astra", reserve_percent=10, timeout=180)
    monkeypatch.setattr(R, "build_snapshot", lambda *_: {"status": "observed", "open_positions": []})
    monkeypatch.setattr(R, "read_quota", lambda *_: quota())

    def model(command, prompt, directory, timeout):
        assert "<untrusted_snapshot>" in prompt
        assert 0 < timeout <= 180
        Path(command[command.index("-o") + 1]).write_text(json.dumps(assessment()))
        return {"input_tokens": 100, "output_tokens": 20}

    monkeypatch.setattr(R, "run_model", model)
    return args


def test_success_preserves_sources_and_keeps_immutable_snapshots(setup_observer):
    args = setup_observer
    original = args.state.read_bytes(), args.state.stat().st_mtime_ns
    first, second = R.observe(args), R.observe(args)
    assert first["status"] == second["status"] == "completed"
    assert first["run_id"] != second["run_id"]
    assert first["usage"]["output_tokens"] == 20
    for name in ("snapshot_sha256", "prompt_sha256", "schema_sha256", "runner_sha256"):
        assert len(first[name]) == 64
    assert (args.state.read_bytes(), args.state.stat().st_mtime_ns) == original
    assert len(list((args.output_dir / "runs").glob("*/snapshot.json"))) == 2
    assert json.loads((args.output_dir / "latest.json").read_text())["run_id"] == second["run_id"]
    assert len((args.output_dir / "history.jsonl").read_text().splitlines()) == 2


@pytest.mark.parametrize("mode,status", [("low", "paused_quota"), ("unknown", "paused_quota_unknown"),
                                        ("unreadable", "no_data")])
def test_skipped_runs_never_call_model(setup_observer, monkeypatch, mode, status):
    if mode == "unreadable":
        monkeypatch.setattr(R, "build_snapshot", lambda *_: {"status": "unavailable"})
        monkeypatch.setattr(R, "read_quota", lambda *_: pytest.fail("quota unnecessary without data"))
    else:
        monkeypatch.setattr(R, "read_quota", lambda *_: quota(0, 90) if mode == "low" else {})
    monkeypatch.setattr(R, "run_model", lambda *_: pytest.fail("must not invoke model"))
    assert R.observe(setup_observer)["status"] == status


@pytest.mark.parametrize("kind,status", [("failure", "failed_model"), ("timeout", "failed_model"),
                                       ("json", "invalid_assessment"), ("extra", "invalid_assessment"),
                                       ("hallucinated", "invalid_assessment"), ("large", "invalid_assessment")])
def test_failures_publish_explicit_status_not_old_success(setup_observer, monkeypatch, kind, status):
    args = setup_observer
    assert R.observe(args)["status"] == "completed"

    def model(command, prompt, directory, timeout):
        if kind == "failure":
            raise RuntimeError("provider failed")
        if kind == "timeout":
            raise subprocess.TimeoutExpired(command, timeout)
        data = assessment()
        if kind == "extra":
            data["execute_trade"] = True
        if kind == "hallucinated":
            data["position_reviews"] = [{"symbol": "BTCUSDT", "recommendation": "REVIEW", "reason": "invented"}]
        raw = "{" if kind == "json" else "x" * 33_000 if kind == "large" else json.dumps(data)
        Path(command[command.index("-o") + 1]).write_text(raw)
        return {}

    monkeypatch.setattr(R, "run_model", model)
    result = R.observe(args)
    assert result["status"] == status
    assert "assessment" not in result
    assert json.loads((args.output_dir / "latest.json").read_text())["status"] == status


def test_lock_prevents_model_call_and_latest_overwrite(setup_observer, monkeypatch):
    args = setup_observer
    args.output_dir.mkdir()
    (args.output_dir / "latest.json").write_text("old result")
    monkeypatch.setattr(R, "build_snapshot", lambda *_: pytest.fail("overlapping run"))
    with (args.output_dir / ".lock").open("w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert R.observe(args)["status"] == "skipped_overlap"
    assert (args.output_dir / "latest.json").read_text() == "old result"


def test_command_enforces_observer_isolation(tmp_path):
    command = R.model_command("codex", "gpt-6-astra", tmp_path, tmp_path / "out.json")
    assert "--ignore-user-config" in command and "--ignore-rules" in command
    assert command[command.index("-s") + 1] == "read-only"
    assert 'approval_policy="never"' in command and 'web_search="disabled"' in command
    assert "skip_host_skill_discovery" in command
    disabled = {command[i + 1] for i, item in enumerate(command[:-1]) if item == "--disable"}
    assert {"shell_tool", "unified_exec", "plugins", "apps", "multi_agent", "hooks", "computer_use"} <= disabled


def test_position_reviews_must_cover_only_open_positions_once():
    data = assessment()
    position = {"symbol": "ETHUSDT", "recommendation": "KEEP_RULES", "reason": "observed"}
    snapshot = {"open_positions": [{"symbol": "ETHUSDT"}]}
    with pytest.raises(ValueError):
        R.validate_positions(data, snapshot)
    data["position_reviews"] = [position]
    R.validate_positions(data, snapshot)
    data["position_reviews"].append(copy.deepcopy(position))
    with pytest.raises(ValueError):
        R.validate_positions(data, snapshot)


def test_real_subprocess_timeout_and_usage_capture(tmp_path):
    with pytest.raises(subprocess.TimeoutExpired):
        R.run_model([sys.executable, "-c", "import time; time.sleep(10)"], "", tmp_path, 0.05)
    script = 'import json; print(json.dumps({"type":"turn.completed","usage":{"input_tokens":12,"output_tokens":3}}))'
    assert R.run_model([sys.executable, "-c", script], "", tmp_path, 2) == {"input_tokens": 12, "output_tokens": 3}
    with pytest.raises(RuntimeError, match="complete"):
        R.run_model([sys.executable, "-c", "print('{}')"], "", tmp_path, 2)


def test_quota_jsonrpc_handshake_without_network(tmp_path):
    executable = tmp_path / "fake-codex"
    executable.write_text(f"#!{sys.executable}\n" + '''import json,sys,time
assert sys.argv[1] == "app-server"
first = json.loads(sys.stdin.readline())
assert first["method"] == "initialize"
print(json.dumps({"id":1,"result":{}}), flush=True)
assert json.loads(sys.stdin.readline())["method"] == "initialized"
assert json.loads(sys.stdin.readline())["method"] == "account/rateLimits/read"
print(json.dumps({"id":2,"result":{"ordinaryUsageAllowed":False}}), flush=True)
time.sleep(10)
''')
    executable.chmod(0o700)
    assert R.read_quota(str(executable), 2) == {"ordinaryUsageAllowed": False}
