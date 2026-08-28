from __future__ import annotations

import inspect
import unittest
from unittest.mock import patch

from ydbdoc_review_ng.state import (
    RepoIdentity, StateError, YdbConfig, YdbState, _BoundedSession, _BoundedTransaction,
)


class Transient(Exception): pass
class Permanent(Exception): pass


class RequestSettings:
    def __init__(self): self.timeout = self.operation_timeout = None
    def with_timeout(self, value): self.timeout = value; return self
    def with_operation_timeout(self, value): self.operation_timeout = value; return self


class RetrySettings:
    created = []
    def __init__(self, **kwargs): self.kwargs = kwargs; self.created.append(kwargs)


class Issues:
    Aborted = Transient


class FakeYdb:
    BaseRequestSettings = RequestSettings
    RetrySettings = RetrySettings
    issues = Issues
    @staticmethod
    def SerializableReadWrite(): return "serializable"


class Tx:
    def __init__(self): self.calls = []
    def execute(self, *args, **kwargs): self.calls.append(("execute", args, kwargs)); return "executed"
    def commit(self, *args, **kwargs): self.calls.append(("commit", args, kwargs)); return "committed"


class Session:
    def __init__(self): self.tx, self.scheme = Tx(), []
    def transaction(self, *args, **kwargs): return self.tx
    def execute_scheme(self, *args, **kwargs): self.scheme.append((args, kwargs)); return "schema"


class Pool:
    def __init__(self, outcomes=()): self.outcomes, self.calls, self.sessions = list(outcomes), [], []
    def retry_operation_sync(self, operation, retry_settings=None):
        self.calls.append(retry_settings)
        if self.outcomes:
            outcome = self.outcomes.pop(0)
            if isinstance(outcome, Exception): raise outcome
        session = Session(); self.sessions.append(session)
        return operation(session)


def adapter(pool):
    value = object.__new__(YdbState)
    value.config = YdbConfig("grpcs://example.invalid:2135", "/safe/database", "/safe/key")
    value.repository = RepoIdentity("owner", "repo")
    value.pool, value._ydb, value._schema_initialized = pool, FakeYdb, False
    return value


class BoundedSdkContract(unittest.TestCase):
    def setUp(self): RetrySettings.created.clear()

    def test_every_pool_attempt_disables_sdk_retries_and_bounds_acquire(self):
        pool = Pool([Transient("one"), Transient("two")])
        state = adapter(pool)
        with patch("ydbdoc_review_ng.state.time.sleep"):
            self.assertEqual(state._pool_attempt(lambda session: "ok", wall_seconds=12, rpc_seconds=3, error_marker="SAFE"), "ok")
        self.assertEqual(len(pool.calls), 3)
        for settings in pool.calls:
            self.assertEqual(settings.kwargs["max_retries"], 0)
            self.assertTrue(settings.kwargs["idempotent"])
            self.assertGreater(settings.kwargs["max_session_acquire_timeout"], 0)
            self.assertLessEqual(settings.kwargs["max_session_acquire_timeout"], 3)
            self.assertLessEqual(settings.kwargs["get_session_client_timeout"], 3)

    def test_permanent_fails_once_and_transient_stops_at_attempt_bound(self):
        permanent = Pool([Permanent("sentinel-secret")])
        with self.assertRaisesRegex(StateError, "^SAFE$"):
            adapter(permanent)._pool_attempt(lambda session: None, wall_seconds=12, rpc_seconds=3, error_marker="SAFE")
        self.assertEqual(len(permanent.calls), 1)
        transient = Pool([Transient("x"), Transient("x"), Transient("x")])
        with patch("ydbdoc_review_ng.state.time.sleep"), self.assertRaisesRegex(StateError, "^SAFE$"):
            adapter(transient)._pool_attempt(lambda session: None, wall_seconds=12, rpc_seconds=3, error_marker="SAFE")
        self.assertEqual(len(transient.calls), 3)

    def test_monotonic_deadline_prevents_second_attempt(self):
        state, pool = adapter(Pool([Transient("x")])), None
        pool = state.pool
        with patch("ydbdoc_review_ng.state.time.monotonic", side_effect=[0.0, 0.0, 2.0]), patch("ydbdoc_review_ng.state.time.sleep"):
            with self.assertRaisesRegex(StateError, "^SAFE$"):
                state._pool_attempt(lambda session: None, wall_seconds=1, rpc_seconds=1, error_marker="SAFE")
        self.assertEqual(len(pool.calls), 1)

    def test_scheme_execute_commit_get_both_rpc_settings_and_explicit_is_preserved(self):
        session = Session()
        bounded = _BoundedSession(session, lambda: RequestSettings().with_timeout(3).with_operation_timeout(3))
        bounded.execute_scheme("DDL")
        tx = bounded.transaction("mode")
        tx.execute("SELECT")
        tx.commit()
        for _, kwargs in (session.scheme[0], session.tx.calls[0][1:]):
            settings = kwargs["settings"]
            self.assertEqual((settings.timeout, settings.operation_timeout), (3, 3))
        commit_settings = session.tx.calls[1][2]["settings"]
        self.assertEqual((commit_settings.timeout, commit_settings.operation_timeout), (3, 3))
        explicit = object()
        bounded.execute_scheme("DDL", settings=explicit)
        tx.execute("SELECT", settings=explicit)
        tx.commit(settings=explicit)
        self.assertIs(session.scheme[-1][1]["settings"], explicit)
        self.assertIs(session.tx.calls[-2][2]["settings"], explicit)
        self.assertIs(session.tx.calls[-1][2]["settings"], explicit)

    def test_structural_guard_only_central_owner_calls_sdk_retry(self):
        source = inspect.getsource(__import__("ydbdoc_review_ng.state", fromlist=["*"]))
        self.assertEqual(source.count("self.pool.retry_operation_sync("), 1)
        self.assertIn("return self._pool_attempt(", source)

    def test_schema_uses_separate_budget_request_settings_and_flushed_markers(self):
        state, pool = adapter(Pool()), None
        pool = state.pool
        with patch("builtins.print") as output:
            state.ensure_schema()
        self.assertEqual(len(pool.calls), 4)
        for retry, session in zip(pool.calls, pool.sessions, strict=True):
            self.assertEqual(retry.kwargs["max_retries"], 0)
            settings = session.scheme[0][1]["settings"]
            self.assertEqual((settings.timeout, settings.operation_timeout), (5, 5))
        self.assertEqual(output.call_count, 8)
        self.assertTrue(all(call.kwargs == {"flush": True} for call in output.call_args_list))
        self.assertEqual(output.call_args_list[0].args, ("YDB_SCHEMA_INDEX_START 0",))
        self.assertEqual(output.call_args_list[-1].args, ("YDB_SCHEMA_INDEX_DONE 3",))

    def test_schema_permanent_error_is_safe_and_flushes_static_error_marker(self):
        state = adapter(Pool([Permanent("sentinel-endpoint")]))
        with patch("builtins.print") as output, self.assertRaisesRegex(StateError, "^YDB_SCHEMA_INDEX_ERROR$"):
            state.ensure_schema()
        self.assertEqual([call.args[0] for call in output.call_args_list], ["YDB_SCHEMA_INDEX_START 0", "YDB_SCHEMA_INDEX_ERROR 0"])
        self.assertTrue(all(call.kwargs == {"flush": True} for call in output.call_args_list))


if __name__ == "__main__": unittest.main()
