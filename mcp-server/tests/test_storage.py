import json
import stat
from pathlib import Path

from mcp_server.storage import write_json_atomic


def test_writes_json_and_leaves_no_temp_file(tmp_path: Path):
    target = tmp_path / "data" / "store.json"

    write_json_atomic(target, {"a": 1})

    assert json.loads(target.read_text()) == {"a": 1}
    assert not (tmp_path / "data" / "store.json.tmp").exists()


def test_file_is_readable_only_by_the_owner(tmp_path: Path):
    target = tmp_path / "store.json"

    write_json_atomic(target, {"a": 1})

    assert stat.S_IMODE(target.stat().st_mode) == 0o600


def test_replaces_existing_content(tmp_path: Path):
    target = tmp_path / "store.json"
    write_json_atomic(target, {"a": 1})

    write_json_atomic(target, {"b": 2})

    assert json.loads(target.read_text()) == {"b": 2}
