from __future__ import annotations

import os
import re
import unittest

import test_state_contract as _contract_module
from ydbdoc_review_ng.state import RepoIdentity, YdbState, real_ydb_test_config_from_env


REAL_ENABLED = bool(
    os.environ.get("YDBDOC_REAL_YDB_STATE") == "1"
    and os.environ.get("YDBDOC_YDB_ENDPOINT")
    and os.environ.get("YDBDOC_YDB_DATABASE")
    and os.environ.get("YDBDOC_YDB_SA_KEY_FILE")
    and os.environ.get("YDBDOC_REAL_YDB_TABLE_PREFIX")
)


@unittest.skipUnless(REAL_ENABLED, "not executed: explicit real-YDB credentials were not supplied")
class RealYdbStateContract(_contract_module.StateContract):
    """Runs the same A-H public contract against isolated real YDB tables."""

    def setUp(self):
        config = real_ydb_test_config_from_env(os.environ)
        if config is None:
            self.skipTest("not executed: explicit real-YDB credentials were not supplied")
        repository = os.environ.get("YDBDOC_REAL_YDB_REPOSITORY", "")
        if not re.fullmatch(r"acceptance/r[0-9a-f]{16}", repository):
            raise RuntimeError("Некорректная область данных проверки YDB.")
        owner, name = repository.split("/", 1)
        self.state = YdbState(
            config, RepoIdentity(owner, name),
        )
        self.state.ensure_schema()
        self.state.cleanup_test_rows(maximum_rows=1000)

    def tearDown(self):
        try:
            self.state.cleanup_test_rows(maximum_rows=1000)
        finally:
            self.state.driver.stop(timeout=5)

    def _expire_run(self):
        table, key = self.state._table("command_runs"), "run:receipt-1"
        self.state._serializable(lambda tx: tx.execute(
            f"DECLARE $r AS Utf8; DECLARE $k AS Utf8; UPDATE `{table}` SET expires_at=CurrentUtcTimestamp() WHERE repository=$r AND record_key=$k;",
            {"$r": self.state.repository.canonical, "$k": key}, commit_tx=True,
        ))

    def test_c_lease_nonce_owner_release_and_exact_boundary(self):
        from ydbdoc_review_ng.state import LeaseOwner, new_claim_nonce
        first, next_owner = LeaseOwner("worker", new_claim_nonce()), LeaseOwner("worker", new_claim_nonce())
        self.assertTrue(self.state.acquire_source_lease(45949, first).won)
        table, key = self.state._table("command_runs"), f"lock:{self.state.repository.canonical}#45949"
        self.state._serializable(lambda tx: tx.execute(
            f"DECLARE $r AS Utf8; DECLARE $k AS Utf8; UPDATE `{table}` SET lease_until=CurrentUtcTimestamp() WHERE repository=$r AND record_key=$k;",
            {"$r": self.state.repository.canonical, "$k": key}, commit_tx=True,
        ))
        self.assertTrue(self.state.acquire_source_lease(45949, next_owner).won)
        self.assertFalse(self.state.release_source_lease(45949, first).changed)
        self.assertTrue(self.state.release_source_lease(45949, next_owner).changed)

    def test_g_logical_expiry_and_accepted_continue_only_refresh(self):
        from ydbdoc_review_ng.state import AcceptedContinue
        self.state.create_lineage(self.lineage())
        table = self.state._table("lineages")
        self.state._serializable(lambda tx: tx.execute(
            f"DECLARE $r AS Utf8; DECLARE $id AS Utf8; UPDATE `{table}` SET expires_at=Unwrap(CurrentUtcTimestamp()+Interval('PT1H'),'test lineage expiry') WHERE repository=$r AND lineage_id=$id;",
            {"$r": self.state.repository.canonical, "$id": "lin-1"}, commit_tx=True,
        ))
        self.assertTrue(self.state.record_accepted_continue("lin-1", AcceptedContinue({"kind": "force"}, 1)).changed)
        self.assertIsNotNone(self.state.get_lineage("lin-1"))


if __name__ == "__main__": unittest.main()
