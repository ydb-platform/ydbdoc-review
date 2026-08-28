from __future__ import annotations

import inspect
import unittest
from enum import Enum
from unittest.mock import patch

from ydbdoc_review_ng.state import (
    EffectCheckpoint, RepoIdentity, StateError, YdbConfig, YdbState, _BoundedSession,
    _BoundedTransaction, _effects_json, _safe_ydb_fingerprint,
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
        self.assertNotIn("operation(session)", source)
        self.assertIn("operation(_BoundedSession(session, request_settings))", source)
        self.assertIn("return self._pool_attempt(", source)

    def test_acquire_and_each_successive_rpc_recompute_remaining_budget(self):
        state, pool = adapter(Pool()), None
        pool = state.pool
        def operation(session):
            session.execute_scheme("ONE")
            session.execute_scheme("TWO")
            return "ok"
        with patch("ydbdoc_review_ng.state.time.monotonic", side_effect=[0.0, 1.0, 3.0, 6.0]):
            self.assertEqual(state._pool_attempt(operation, wall_seconds=10, rpc_seconds=10, error_marker="SAFE"), "ok")
        self.assertEqual(pool.calls[0].kwargs["max_session_acquire_timeout"], 9.0)
        settings = [call[1]["settings"] for call in pool.sessions[0].scheme]
        self.assertEqual([(item.timeout, item.operation_timeout) for item in settings], [(7.0, 7.0), (4.0, 4.0)])

    def test_expired_deadline_blocks_next_rpc_before_raw_execute(self):
        state, pool = adapter(Pool()), None
        pool = state.pool
        def operation(session):
            session.execute_scheme("ONE")
            session.execute_scheme("MUST_NOT_RUN")
        with patch("ydbdoc_review_ng.state.time.monotonic", side_effect=[0.0, 0.0, 0.5, 1.1]):
            with self.assertRaisesRegex(StateError, "^SAFE$"):
                state._pool_attempt(operation, wall_seconds=1, rpc_seconds=1, error_marker="SAFE")
        self.assertEqual(len(pool.sessions[0].scheme), 1)

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

    def test_four_schema_statements_share_one_absolute_deadline(self):
        state, pool = adapter(Pool()), None
        pool = state.pool
        with patch("ydbdoc_review_ng.state.time.monotonic", side_effect=[0, 1, 2, 9, 10, 15, 16, 17, 18]), patch("builtins.print"):
            state.ensure_schema()
        settings = [session.scheme[0][1]["settings"] for session in pool.sessions]
        self.assertEqual([item.timeout for item in settings], [5, 5, 4, 2])

    def test_count_and_four_drops_receive_one_cleanup_deadline(self):
        from ydbdoc_review_ng.state import RealYdbTestConfig
        state = adapter(Pool())
        state.config = RealYdbTestConfig("grpcs://example.invalid:2135", "/safe/database", "/safe/key", "m0_contract_test12")
        seen = []
        def serializable(operation, *, absolute_deadline=None): seen.append(("count", absolute_deadline)); return 0
        def pool_attempt(operation, **kwargs): seen.append(("drop", kwargs["absolute_deadline"])); return None
        state._serializable, state._pool_attempt = serializable, pool_attempt
        with patch("ydbdoc_review_ng.state.time.monotonic", return_value=100), patch("builtins.print"):
            state.teardown_test_schema()
        self.assertEqual(seen, [("count", 120.0)] + [("drop", 120.0)] * 4)

    def test_schema_permanent_error_is_safe_and_flushes_static_error_marker(self):
        state = adapter(Pool([Permanent("sentinel-endpoint")]))
        with patch("builtins.print") as output, self.assertRaisesRegex(StateError, "^YDB_SCHEMA_INDEX_ERROR$"):
            state.ensure_schema()
        self.assertEqual([call.args[0] for call in output.call_args_list], [
            "YDB_SCHEMA_INDEX_START 0", "YDB_ATTEMPT_ERROR class=OTHER status=NONE issues=NONE",
            "YDB_SCHEMA_INDEX_ERROR 0",
        ])
        self.assertTrue(all(call.kwargs == {"flush": True} for call in output.call_args_list))

    def test_error_fingerprint_is_allowlisted_bounded_and_never_reads_messages(self):
        class Status(Enum): UNAVAILABLE = 1
        class Issue:
            def __init__(self, code, severity, children=()):
                self.issue_code, self.severity, self.issues = code, severity, children
                self.message = "sentinel-secret endpoint query payload"
                self.args = (self.message,)
        class Unavailable(Exception):
            status = Status.UNAVAILABLE
        root = Unavailable("sentinel-secret endpoint query payload")
        level4 = [Issue(400 + index, 4) for index in range(20)]
        level3 = [Issue(300 + index, 3, level4) for index in range(20)]
        level2 = [Issue(200 + index, 2, level3) for index in range(20)]
        root.issues = [Issue(100 + index, 1, level2) for index in range(20)]
        value = _safe_ydb_fingerprint(root)
        self.assertNotIn("sentinel", value)
        self.assertEqual(value.split()[0], "class=Unavailable")
        self.assertIn("status=UNAVAILABLE", value)
        fingerprints = value.split("issues=", 1)[1].split(",")
        self.assertLessEqual(len(fingerprints), 16)
        self.assertFalse(any(item.startswith("400") for item in fingerprints))

    def test_unknown_error_prints_only_fixed_safe_fingerprint_before_rethrow(self):
        state = adapter(Pool([Permanent("sentinel-secret endpoint query")]))
        with patch("builtins.print") as output, self.assertRaisesRegex(StateError, "^SAFE$"):
            state._pool_attempt(lambda session: None, wall_seconds=1, rpc_seconds=1, error_marker="SAFE")
        rendered = output.call_args.args[0]
        self.assertEqual(rendered, "YDB_ATTEMPT_ERROR class=OTHER status=NONE issues=NONE")
        self.assertNotIn("sentinel", rendered)
        self.assertEqual(output.call_args.kwargs, {"flush": True})

    def test_effect_checkpoint_markers_follow_read_write_verify_boundaries(self):
        effects = (EffectCheckpoint(0, "PUSH_BRANCH", "PLANNED", "branch:test", "a" * 64),)
        payload = _effects_json(effects)
        class Result:
            def __init__(self, rows): self.rows = rows
        class EffectTx:
            def __init__(self, fail_at=None): self.calls, self.fail_at, self.commits = 0, fail_at, 0
            def execute(self, *args, **kwargs):
                self.calls += 1
                if self.calls == self.fail_at: raise RuntimeError("sentinel-query")
                if self.calls == 1: return [Result([{"alive": True, "effects_schema_version": None, "effect_checkpoints": None}])]
                if self.calls == 3: return [Result([{"effects_schema_version": "command-effects/v1", "effect_checkpoints": payload}])]
                return []
            def commit(self): self.commits += 1
        expected = [
            "EFFECT_CHECKPOINT_READ_START", "EFFECT_CHECKPOINT_READ_DONE",
            "EFFECT_CHECKPOINT_WRITE_START", "EFFECT_CHECKPOINT_WRITE_DONE",
            "EFFECT_CHECKPOINT_VERIFY_START", "EFFECT_CHECKPOINT_VERIFY_DONE",
        ]
        for fail_at, visible in ((None, expected), (2, expected[:3]), (3, expected[:5])):
            state, tx = adapter(Pool()), EffectTx(fail_at)
            state._serializable = lambda operation, **kwargs: operation(tx)
            with patch("builtins.print") as output:
                if fail_at is None:
                    self.assertTrue(state.put_effect_checkpoints("receipt", effects).won)
                else:
                    with self.assertRaises(RuntimeError): state.put_effect_checkpoints("receipt", effects)
            self.assertEqual([call.args[0] for call in output.call_args_list], visible)
            self.assertTrue(all(call.kwargs == {"flush": True} for call in output.call_args_list))

    def test_effect_checkpoint_early_commit_markers_are_ordered(self):
        effects = (EffectCheckpoint(0, "PUSH_BRANCH", "PLANNED", "branch:test", "a" * 64),)
        class Result: rows = []
        class Tx:
            def __init__(self): self.commits = 0
            def execute(self, *args, **kwargs): return [Result()]
            def commit(self): self.commits += 1
        state, tx = adapter(Pool()), Tx()
        state._serializable = lambda operation, **kwargs: operation(tx)
        with patch("builtins.print") as output:
            state.put_effect_checkpoints("receipt", effects)
        self.assertEqual([call.args[0] for call in output.call_args_list], [
            "EFFECT_CHECKPOINT_READ_START", "EFFECT_CHECKPOINT_READ_DONE",
            "EFFECT_CHECKPOINT_EARLY_COMMIT_START", "EFFECT_CHECKPOINT_EARLY_COMMIT_DONE",
        ])
        self.assertEqual(tx.commits, 1)

    def test_effect_checkpoint_constructs_result_only_after_early_commit(self):
        effects = (EffectCheckpoint(0, "PUSH_BRANCH", "PLANNED", "branch:test", "a" * 64),)
        events = []
        class Result: rows = []
        class Tx:
            def execute(self, *args, **kwargs): return [Result()]
            def commit(self): events.append("commit")
        state = adapter(Pool())
        state._serializable = lambda operation, **kwargs: operation(Tx())
        with patch("builtins.print"), patch("ydbdoc_review_ng.state.ClaimResult", side_effect=lambda *args, **kwargs: events.append("construct") or object()):
            state.put_effect_checkpoints("receipt", effects)
        self.assertEqual(events, ["commit", "construct"])


if __name__ == "__main__": unittest.main()
