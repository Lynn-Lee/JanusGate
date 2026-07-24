"""#t65 M1：数据脱敏规则（PolicyDecisionService.mask）测试。

覆盖：无规则原样、full 占位符、partial 保留前后缀、keyword 子串、多规则累计、选择器作用域、
租户隔离、非法正则安全跳过、失活忽略、redaction_count 与 applied_rule_ids。规则以 ORM 模型
在内存直接构造（不落库）。
"""

from __future__ import annotations

import json

from app.models.acl import (
    DataMaskingMatchType,
    DataMaskingMethod,
    DataMaskingRuleModel,
)
from app.policy.decision import PolicyDecisionService
from app.policy.schemas import MaskingRequest, ResourceRef, SubjectRef


def _rule(
    rule_id: str,
    patterns: list[str],
    *,
    match_type: DataMaskingMatchType = DataMaskingMatchType.REGEX,
    mask_method: DataMaskingMethod = DataMaskingMethod.FULL,
    placeholder: str = "***",
    keep_prefix: int = 0,
    keep_suffix: int = 0,
    priority: int = 50,
    tenant_id: str = "tenant-a",
    subject_ids: list[str] | None = None,
    asset_ids: list[str] | None = None,
    account_ids: list[str] | None = None,
    is_active: bool = True,
) -> DataMaskingRuleModel:
    return DataMaskingRuleModel(
        id=rule_id,
        tenant_id=tenant_id,
        name=rule_id,
        priority=priority,
        match_type=match_type,
        patterns_json=json.dumps(patterns),
        mask_method=mask_method,
        keep_prefix=keep_prefix,
        keep_suffix=keep_suffix,
        placeholder=placeholder,
        subject_ids_json=json.dumps(subject_ids or ["*"]),
        asset_ids_json=json.dumps(asset_ids or ["*"]),
        account_ids_json=json.dumps(account_ids or ["*"]),
        is_active=is_active,
    )


def _request(text: str, **overrides: object) -> MaskingRequest:
    data: dict[str, object] = {
        "subject": SubjectRef(id="user-1", type="user", tenant_id="tenant-a"),
        "resource": ResourceRef(id="asset-1", type="db_asset", tenant_id="tenant-a"),
        "account_id": "root",
        "text": text,
    }
    data.update(overrides)
    return MaskingRequest(**data)  # type: ignore[arg-type]


def test_text_unchanged_when_no_rules() -> None:
    service = PolicyDecisionService()

    result = service.mask(_request("select * from users"))

    assert result.masked_text == "select * from users"
    assert result.redaction_count == 0
    assert result.applied_rule_ids == []
    assert result.audit_event_id.startswith("pde_")


def test_full_masking_replaces_match_with_placeholder() -> None:
    service = PolicyDecisionService(
        data_masking_rules=[_rule("r1", [r"\d{3}-\d{2}-\d{4}"], placeholder="[SSN]")]
    )

    result = service.mask(_request("ssn=123-45-6789 done"))

    assert result.masked_text == "ssn=[SSN] done"
    assert result.redaction_count == 1
    assert result.applied_rule_ids == ["r1"]


def test_partial_masking_keeps_prefix_and_suffix() -> None:
    service = PolicyDecisionService(
        data_masking_rules=[
            _rule(
                "r1",
                [r"\d{16}"],
                mask_method=DataMaskingMethod.PARTIAL,
                keep_prefix=0,
                keep_suffix=4,
            )
        ]
    )

    result = service.mask(_request("card 1234567812345678 ok"))

    assert result.masked_text == "card ************5678 ok"
    assert result.redaction_count == 1


def test_partial_masking_full_masks_when_too_short() -> None:
    service = PolicyDecisionService(
        data_masking_rules=[
            _rule(
                "r1",
                ["ab"],
                match_type=DataMaskingMatchType.KEYWORD,
                mask_method=DataMaskingMethod.PARTIAL,
                keep_prefix=2,
                keep_suffix=2,
            )
        ]
    )

    result = service.mask(_request("ab"))

    # 保留长度 >= 值长度时整体打码，不泄露原值。
    assert result.masked_text == "**"


def test_keyword_matching_escapes_literal() -> None:
    service = PolicyDecisionService(
        data_masking_rules=[
            _rule("r1", ["a.b"], match_type=DataMaskingMatchType.KEYWORD, placeholder="X")
        ]
    )

    # 关键字按字面转义：命中 "a.b"，不命中 "axb"。
    result = service.mask(_request("a.b axb"))

    assert result.masked_text == "X axb"
    assert result.redaction_count == 1


def test_multiple_rules_accumulate() -> None:
    service = PolicyDecisionService(
        data_masking_rules=[
            _rule("r-ssn", [r"\d{3}-\d{2}-\d{4}"], placeholder="[SSN]", priority=10),
            _rule("r-email", [r"\w+@\w+\.\w+"], placeholder="[EMAIL]", priority=20),
        ]
    )

    result = service.mask(_request("ssn 123-45-6789 mail bob@acme.com"))

    assert result.masked_text == "ssn [SSN] mail [EMAIL]"
    assert result.redaction_count == 2
    assert result.applied_rule_ids == ["r-ssn", "r-email"]


def test_rule_scoped_to_other_account_is_skipped() -> None:
    service = PolicyDecisionService(
        data_masking_rules=[_rule("r1", [r"\d+"], account_ids=["deploy"])]
    )

    result = service.mask(_request("code 12345"))

    assert result.masked_text == "code 12345"
    assert result.redaction_count == 0


def test_other_tenant_rule_is_ignored() -> None:
    service = PolicyDecisionService(
        data_masking_rules=[_rule("r1", [r"\d+"], tenant_id="tenant-b")]
    )

    result = service.mask(_request("code 12345"))

    assert result.masked_text == "code 12345"


def test_tenant_mismatch_returns_text_unchanged() -> None:
    service = PolicyDecisionService(data_masking_rules=[_rule("r1", [r"\d+"])])

    result = service.mask(
        _request(
            "code 12345",
            resource=ResourceRef(id="asset-1", type="db_asset", tenant_id="tenant-x"),
        )
    )

    assert result.masked_text == "code 12345"
    assert "tenant_mismatch" in result.explain_trace


def test_invalid_regex_is_safe_no_op() -> None:
    service = PolicyDecisionService(
        data_masking_rules=[_rule("r1", ["("]), _rule("r2", [r"\d+"], placeholder="#", priority=99)]
    )

    # 非法正则规则安全跳过，不影响后续有效规则。
    result = service.mask(_request("id 7"))

    assert result.masked_text == "id #"
    assert result.applied_rule_ids == ["r2"]


def test_inactive_rule_is_ignored() -> None:
    service = PolicyDecisionService(
        data_masking_rules=[_rule("r1", [r"\d+"], is_active=False)]
    )

    result = service.mask(_request("code 12345"))

    assert result.masked_text == "code 12345"
