import json
import subprocess
import sys

from app.config import REPO_ROOT


def cli(*args):
    return subprocess.run([sys.executable, "-X", "utf8", "-m", "app.cli", *map(str, args)], cwd=REPO_ROOT / "backend",
        capture_output=True, text=True, encoding="utf-8")


def test_authored_cli_and_unknown_query_have_honest_exit_codes(tmp_path):
    output = tmp_path / "recordings"
    fixture = REPO_ROOT / "data/recordings/examples/text-authored.json"
    assert cli("record", "--authored-input", fixture, "--recordings", output).returncode == 0
    result = cli("verify-recordings", "--recordings", output)
    assert result.returncode == 0
    assert json.loads(result.stdout) == {"record_count": 1, "origins": ["authored"], "network_used": False}
    known = cli("verify-recordings", "--recordings", output, "--requests", fixture)
    assert known.returncode == 0 and json.loads(known.stdout)["verified_requests"] == 1
    unknown = cli("verify-recordings", "--recordings", output, "--requests", REPO_ROOT / "data/recordings/examples/text-unknown.json")
    assert unknown.returncode != 0 and "REPLAY_RECORD_MISSING" in unknown.stderr
    before = (output / "active.json").read_bytes()
    refused = cli("record", "--recordings", output)
    assert refused.returncode != 0 and "LIVE_RECORDING_NOT_CONNECTED" in refused.stderr
    assert (output / "active.json").read_bytes() == before


def test_record_command_refuses_demo_output(tmp_path):
    fixture = REPO_ROOT / "data/recordings/examples/text-authored.json"
    result = cli("record", "--authored-input", fixture, "--recordings", REPO_ROOT / "data/demo")
    assert result.returncode != 0
