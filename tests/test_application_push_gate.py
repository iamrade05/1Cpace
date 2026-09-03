"""Regression tests for the application -> Itensity handoff helpers."""

from onecpase.applications import _itensity_result_is_valid


def test_itensity_success_requires_member_reference():
    assert _itensity_result_is_valid(True, "IT-123")
    assert not _itensity_result_is_valid(True, None)
    assert not _itensity_result_is_valid(True, "")
    assert not _itensity_result_is_valid(False, "IT-123")
