"""#t77 作业中心安全回归：无 pickle、敏感 payload、路径逃逸、shell 元字符。"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.services.job_center import (
    coerce_extra_vars,
    validate_adhoc_command,
    validate_playbook_filename,
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
