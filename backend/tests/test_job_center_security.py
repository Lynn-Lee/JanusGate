"""#t77 作业中心安全回归：无 pickle、敏感 payload、路径逃逸、shell 元字符。"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services.job_center import (
    MAX_INTERVAL_SECONDS,
    PLAYBOOK_CONTENT_MAX_BYTES,
    coerce_extra_vars,
    validate_adhoc_command,
    validate_interval_seconds,
    validate_playbook_content,
    validate_playbook_filename,
    validate_variable_name,
)


def test_job_center_modules_do_not_import_pickle() -> None:
    roots = [
        Path("app/services/job_center.py"),
        Path("app/services/ansible_adhoc.py"),
        Path("app/api/job_center.py"),
        Path("app/models/job_center.py"),
        Path("app/services/automation_worker.py"),
    ]
    for path in roots:
        source = Path(__file__).resolve().parents[1].joinpath(path).read_text(encoding="utf-8")
        assert "import pickle" not in source
        assert "from pickle" not in source


def test_playbook_filename_rejects_path_escape() -> None:
    with pytest.raises(ValueError, match="ANSIBLE_PLAYBOOK_NOT_ALLOWED"):
        validate_playbook_filename("../etc/passwd.yml")
    with pytest.raises(ValueError, match="ANSIBLE_PLAYBOOK_NOT_ALLOWED"):
        validate_playbook_filename("/tmp/evil.yml")
    with pytest.raises(ValueError, match="ANSIBLE_PLAYBOOK_NOT_ALLOWED"):
        validate_playbook_filename("notes.txt")
    assert validate_playbook_filename("linux-baseline.yml") == "linux-baseline.yml"


def test_adhoc_rejects_shell_metacharacters_and_shell_module() -> None:
    with pytest.raises(ValueError, match="ADHOC_MODULE_NOT_ALLOWED"):
        validate_adhoc_command(module="shell", args="uptime")
    with pytest.raises(ValueError, match="ADHOC_ARGS_NOT_ALLOWED"):
        validate_adhoc_command(module="command", args="uptime; rm -rf /")
    with pytest.raises(ValueError, match="ADHOC_ARGS_NOT_ALLOWED"):
        validate_adhoc_command(module="command", args="echo $(id)")
    with pytest.raises(ValueError, match="ADHOC_ARGS_NOT_ALLOWED"):
        validate_adhoc_command(module="command", args="cat /etc/passwd | mail")
    module, args = validate_adhoc_command(module="command", args="uptime")
    assert module == "command"
    assert args == "uptime"


def test_extra_vars_reject_sensitive_keys() -> None:
    with pytest.raises(ValueError, match="AUTOMATION_JOB_PAYLOAD_CONTAINS_SECRET"):
        coerce_extra_vars({"region": "ap-east", "password": "plain"})
    with pytest.raises(ValueError, match="AUTOMATION_JOB_PAYLOAD_CONTAINS_SECRET"):
        coerce_extra_vars({"nested": {"token": "abc"}})
    assert coerce_extra_vars({"region": "ap-east", "replicas": 2}) == {
        "region": "ap-east",
        "replicas": 2,
    }


def test_playbook_content_interval_and_variable_name_edges() -> None:
    with pytest.raises(ValueError, match="JOB_PLAYBOOK_CONTENT_TOO_LARGE"):
        validate_playbook_content("x" * (PLAYBOOK_CONTENT_MAX_BYTES + 1))
    assert validate_playbook_content("---\n") == "---\n"
    with pytest.raises(ValueError, match="ANSIBLE_PLAYBOOK_NOT_ALLOWED"):
        validate_playbook_filename("   ")
    with pytest.raises(ValueError, match="ADHOC_ARGS_REQUIRED"):
        validate_adhoc_command(module="command", args="   ")
    with pytest.raises(ValueError, match="ADHOC_ARGS_TOO_LONG"):
        validate_adhoc_command(module="command", args="u" * 1025)
    with pytest.raises(ValueError, match="JOB_VARIABLE_NAME_INVALID"):
        validate_variable_name("1bad")
    with pytest.raises(ValueError, match="JOB_INTERVAL_INVALID"):
        validate_interval_seconds(1)
    with pytest.raises(ValueError, match="JOB_INTERVAL_INVALID"):
        validate_interval_seconds(MAX_INTERVAL_SECONDS + 1)
    assert validate_interval_seconds(None) is None
    assert validate_interval_seconds(60) == 60
    with pytest.raises(ValueError, match="JOB_VARIABLE_INVALID"):
        coerce_extra_vars(["not", "an", "object"])
    with pytest.raises(ValueError, match="JOB_VARIABLE_INVALID"):
        coerce_extra_vars({"": "x"})
    with pytest.raises(ValueError, match="JOB_VARIABLE_INVALID"):
        coerce_extra_vars({"ok": object()})
