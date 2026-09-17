"""Visible pending E2E entry points, deliberately not counted as implemented coverage."""
import pytest


@pytest.mark.parametrize("mode", ["doc_translate", "doc_verify", "doc_continue"])
@pytest.mark.parametrize("outcome", ["success", "error"])
def test_complete_process_pending(mode, outcome):
    pytest.skip(f"R37 pending T15: {mode}/{outcome}; production stages, Git bytes/SHA, "
                "publication, both reports, stored context and all paid costs required")
