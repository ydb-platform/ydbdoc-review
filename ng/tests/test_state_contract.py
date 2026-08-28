from __future__ import annotations

import inspect
import threading
import unittest
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch

from ydbdoc_review_ng.state import (
    AcceptedContinue, ClaimStatus, CommandReceipt, DryState, LeaseOwner,
    ModelCallIdentity, ModelCallReservation, ModelReconciliation, ModelState,
    NewLineage, RecordedModelResult, RepoIdentity, RotationClaim,
    StateError, UnknownModelOutcome, VerificationResult, YdbConfig, YdbState, new_claim_nonce, schema_statements,
    EffectCheckpoint, validate_effects,
)


SHA = "a" * 64


class FixtureClock:
    def __init__(self): self.value = datetime(2026, 8, 28, 10, tzinfo=timezone.utc)
    def __call__(self): return self.value
    def advance(self, delta): self.value += delta


class ReadPool:
    def __init__(self, row=None, error: Exception | None = None):
        self.row, self.error, self.calls = row, error, 0

    def retry_operation_sync(self, operation):
        self.calls += 1
        if self.error:
            raise self.error
        row = self.row
        class Result: rows = [row] if row is not None else []
        class Tx:
            def execute(self, *args, **kwargs): return [Result()]
        class Session:
            def transaction(self): return Tx()
        return operation(Session())


class StateContract(unittest.TestCase):
    def setUp(self):
        self.clock = FixtureClock()
        self.state = DryState(RepoIdentity("YDB-Platform", "YDB"), self.clock)

    def receipt(self):
        return CommandReceipt("receipt-1", "10", 1, "pull_request_target", "labeled", 20, SHA, "DOC_TRANSLATE", "sintjuri", 45949)

    def reservation(self, nonce=None, identity="model-1"):
        return ModelCallReservation(ModelCallIdentity(identity), nonce or new_claim_nonce(), "lin-1", "receipt-1", "yandex", "model-a", "TRANSLATOR_A", 1, 1)

    def lineage(self):
        return NewLineage("lin-1", 45949, None, "ydbdoc-review/pr-45949", "WAITING", "1"*40, "2"*40, "3"*40, "manifest/v1", SHA, {"files": []}, "4"*40, "5"*40, "decisions/v1")

    def verification(self):
        critic = {"provider":"yandex","model":"b","role":"CRITIC_B","pass":1,"attempt":1,"call_identity":"c","provider_outcome":"SUCCESS","result_sha256":SHA,"verdict":"PASS"}
        return VerificationResult("v1", "case", SHA, "lin-1", "receipt-1", "4"*40, "5"*40, SHA, SHA, None, SHA, "BLOCKED", (critic,), (), (), (), "PASS")

    def _expire_run(self):
        self.clock.advance(timedelta(days=14))

    def reconcile_adapter(self, row=None, error=None):
        adapter = object.__new__(YdbState)
        adapter.config = YdbConfig("grpcs://secret-endpoint", "/secret-db", "/secret-key")
        adapter.repository = RepoIdentity("ydb-platform", "ydb")
        adapter.pool = ReadPool(row, error)
        return adapter

    def test_a_schema_exact_four_tables_and_nonce_columns(self):
        statements = schema_statements()
        self.assertEqual(len(statements), 4)
        self.assertIn("mutation_nonce Utf8", statements[0])
        self.assertIn("reservation_nonce Utf8", statements[2])
        self.assertIn("rotation_nonce Utf8", statements[2])
        self.assertNotIn("mutation_nonce Utf8 NOT NULL", statements[0])
        self.assertNotIn("reservation_nonce Utf8 NOT NULL", statements[2])

    def test_a_closed_ordered_effect_checkpoint_shape(self):
        kinds = ("CLOSE_OLD_DRAFT", "DELETE_OLD_BRANCH", "UPDATE_BRANCH", "CREATE_DRAFT", "PUSH_BRANCH", "POST_OR_UPDATE_COMMENT")
        targets = ("pr:60000", "branch:old", "branch:new", "draft:45949", "branch:new", "comment:70001")
        effects = tuple(EffectCheckpoint(i, kind, "PLANNED", targets[i], SHA) for i, kind in enumerate(kinds))
        validate_effects("command-effects/v1", effects)
        with self.assertRaises(ValueError):
            validate_effects("command-effects/v2", effects)
        with self.assertRaises(ValueError):
            validate_effects("command-effects/v1", effects[1:])

    def test_a_six_effect_checkpoints_are_durable_and_monotonic(self):
        self.state.receive_command(self.receipt())
        kinds = ("CLOSE_OLD_DRAFT", "DELETE_OLD_BRANCH", "UPDATE_BRANCH", "CREATE_DRAFT", "PUSH_BRANCH", "POST_OR_UPDATE_COMMENT")
        targets = ("pr:60000", "branch:old", "branch:new", "draft:45949", "branch:new", "comment:70001")
        planned = tuple(EffectCheckpoint(i, kind, "PLANNED", targets[i], SHA) for i, kind in enumerate(kinds))
        self.assertEqual(self.state.put_effect_checkpoints("receipt-1", planned).status, ClaimStatus.CREATED)
        self.assertEqual(self.state.get_effect_checkpoints("receipt-1"), planned)
        skipped = tuple(EffectCheckpoint(i, kind, "CONFIRMED", targets[i], SHA, "2026-08-28T10:00:00Z", f"external-{i}", "2026-08-28T10:01:00Z") for i, kind in enumerate(kinds))
        self.assertEqual(self.state.put_effect_checkpoints("receipt-1", skipped).status, ClaimStatus.CONFLICT)
        intent = tuple(EffectCheckpoint(i, kind, "INTENT_RECORDED", targets[i], SHA, "2026-08-28T10:00:00Z") for i, kind in enumerate(kinds))
        self.assertEqual(self.state.put_effect_checkpoints("receipt-1", intent).status, ClaimStatus.WON)
        confirmed = tuple(EffectCheckpoint(i, kind, "CONFIRMED", targets[i], SHA, "2026-08-28T10:00:00Z", f"external-{i}", "2026-08-28T10:01:00Z") for i, kind in enumerate(kinds))
        self.assertEqual(self.state.put_effect_checkpoints("receipt-1", confirmed).status, ClaimStatus.WON)
        stored = self.state.get_effect_checkpoints("receipt-1")
        self.assertTrue(all(item.intent_recorded_at == "2026-08-28T10:00:00Z" for item in stored))
        self.assertTrue(all(item.confirmed_at == "2026-08-28T10:01:00Z" and item.confirmed_at > item.intent_recorded_at for item in stored))
        self.assertEqual(self.state.put_effect_checkpoints("receipt-1", planned).status, ClaimStatus.CONFLICT)
        self.assertEqual(self.state.get_effect_checkpoints("receipt-1"), confirmed)

    def test_b_receipt_explicit_winner_duplicate_and_conflict(self):
        first = self.state.receive_command(self.receipt())
        same = self.state.receive_command(self.receipt())
        changed = CommandReceipt(**{**self.receipt().__dict__, "payload_sha256": "b"*64})
        conflict = self.state.receive_command(changed)
        self.assertEqual((first.status, same.status, conflict.status), (ClaimStatus.CREATED, ClaimStatus.EXISTING_SAME, ClaimStatus.CONFLICT))

    def test_b_concurrent_receipt_one_winner(self):
        results = []
        threads = [threading.Thread(target=lambda: results.append(self.state.receive_command(self.receipt()))) for _ in range(8)]
        [t.start() for t in threads]; [t.join() for t in threads]
        self.assertEqual(sum(x.won for x in results), 1)

    def test_b_run_logical_expiry_allows_a_new_claim(self):
        self.assertEqual(self.state.receive_command(self.receipt()).status, ClaimStatus.CREATED)
        self._expire_run()
        self.assertEqual(self.state.receive_command(self.receipt()).status, ClaimStatus.CREATED)

    def test_c_lease_nonce_owner_release_and_exact_boundary(self):
        first = LeaseOwner("worker", new_claim_nonce())
        stale = LeaseOwner("worker", new_claim_nonce())
        self.assertTrue(self.state.acquire_source_lease(45949, first).won)
        self.assertFalse(self.state.acquire_source_lease(45949, stale).won)
        self.assertFalse(self.state.release_source_lease(45949, stale).changed)
        self.clock.advance(timedelta(hours=2))
        self.assertTrue(self.state.acquire_source_lease(45949, stale).won)
        self.assertFalse(self.state.release_source_lease(45949, first).changed)
        self.assertTrue(self.state.release_source_lease(45949, stale).changed)

    def test_d_model_reservation_nonce_is_fixed(self):
        reservation = self.reservation()
        self.assertTrue(self.state.reserve_model_call(reservation).won)
        duplicate = self.state.reserve_model_call(reservation)
        self.assertEqual(duplicate.status, ClaimStatus.EXISTING_SAME)
        self.assertFalse(duplicate.won)
        other = self.reservation(new_claim_nonce())
        existing = self.state.reserve_model_call(other)
        self.assertEqual(existing.status, ClaimStatus.EXISTING_SAME)
        self.assertFalse(existing.won)
        self.state.record_model_result(reservation.identity, RecordedModelResult("SUCCESS", "p"))
        self.assertEqual(self.state.get_model_call(reservation.identity).reservation.reservation_nonce, reservation.reservation_nonce)

    def test_d_ambiguous_reservation_reconciliation_is_bounded_and_nonce_exact(self):
        reservation = self.reservation()
        row = {
            "state": "RESERVED", "reservation_nonce": reservation.reservation_nonce,
            "idempotency_identity": reservation.identity.idempotency_identity,
            "lineage_id": reservation.lineage_id, "run_receipt_identity": reservation.run_receipt_identity,
            "provider": reservation.provider, "model": reservation.model, "role": reservation.role,
            "verification_pass": reservation.verification_pass, "attempt": reservation.attempt,
        }
        adapter = self.reconcile_adapter(row)
        result = adapter._reconcile_model_reservation_once(reservation)
        self.assertEqual((result.status, result.won, adapter.pool.calls), (ClaimStatus.CREATED, True, 1))
        other = self.reservation(new_claim_nonce())
        result = adapter._reconcile_model_reservation_once(other)
        self.assertEqual((result.status, result.won, adapter.pool.calls), (ClaimStatus.EXISTING_SAME, False, 2))

    def test_d_forced_ambiguous_receipt_model_lease_rotation_are_typed_and_bounded(self):
        receipt = self.receipt()
        receipt_row = {**receipt.__dict__, "phase": "RECEIVED"}
        adapter = self.reconcile_adapter(receipt_row)
        with patch.object(adapter, "_serializable", side_effect=StateError("secret")):
            result = adapter.receive_command(receipt)
        self.assertEqual((result.status, result.won, adapter.pool.calls), (ClaimStatus.EXISTING_SAME, False, 1))

        reservation = self.reservation()
        model_row = {
            "state": "RESERVED", "reservation_nonce": reservation.reservation_nonce,
            "idempotency_identity": reservation.identity.idempotency_identity,
            "lineage_id": reservation.lineage_id, "run_receipt_identity": reservation.run_receipt_identity,
            "provider": reservation.provider, "model": reservation.model, "role": reservation.role,
            "verification_pass": reservation.verification_pass, "attempt": reservation.attempt,
        }
        adapter = self.reconcile_adapter(model_row)
        with patch.object(adapter, "_serializable", side_effect=StateError("secret")):
            result = adapter.reserve_model_call(reservation)
        self.assertEqual((result.status, result.won, adapter.pool.calls), (ClaimStatus.CREATED, True, 1))

        owner = LeaseOwner("worker", new_claim_nonce())
        adapter = self.reconcile_adapter({"lock_owner": owner.owner_id, "mutation_nonce": owner.mutation_nonce})
        with patch.object(adapter, "_serializable", side_effect=StateError("secret")):
            result = adapter.acquire_source_lease(45949, owner)
        self.assertEqual((result.status, result.won, adapter.pool.calls), (ClaimStatus.WON, True, 1))

        adapter = self.reconcile_adapter(None)
        with patch.object(adapter, "_serializable", side_effect=StateError("secret")):
            released = adapter.release_source_lease(45949, owner)
        self.assertTrue(released.changed)
        self.assertEqual(adapter.pool.calls, 1)

        claim = RotationClaim("CRITIC_B", 0, 1, new_claim_nonce())
        adapter = self.reconcile_adapter({"rotation_cursor": 1, "rotation_nonce": claim.rotation_nonce})
        with patch.object(adapter, "_serializable", side_effect=StateError("secret")):
            result = adapter.advance_rotation(claim)
        self.assertEqual((result.status, result.won, adapter.pool.calls), (ClaimStatus.WON, True, 1))

        adapter = self.reconcile_adapter(error=RuntimeError("secret-endpoint /secret-db /secret-key"))
        unavailable = adapter._reconcile_rotation_once(claim)
        self.assertEqual((unavailable.status, unavailable.won, adapter.pool.calls), (ClaimStatus.INCONCLUSIVE, False, 1))
        self.assertNotIn("secret", repr(unavailable))

    def test_e_transitions_nullable_success_unknown_and_reconciliation(self):
        null_success = self.reservation(identity="null-success")
        self.state.reserve_model_call(null_success)
        transition = self.state.record_model_result(null_success.identity, RecordedModelResult("SUCCESS", None))
        self.assertTrue(transition.changed)
        self.assertIsNone(self.state.get_model_call(null_success.identity).result.actual_cost_rub)
        unknown = self.reservation(identity="unknown")
        self.state.reserve_model_call(unknown)
        self.state.mark_model_unknown(unknown.identity, UnknownModelOutcome())
        row = self.state.get_model_call(unknown.identity)
        self.assertEqual(row.state, ModelState.UNKNOWN_BILLED)
        self.assertIsNone(row.finished_moscow_day)
        reconciliation = ModelReconciliation("RECONCILED_NOT_BILLED", SHA)
        self.assertTrue(self.state.reconcile_model_call(unknown.identity, reconciliation).changed)

    def test_e_strict_result_and_verification_validation(self):
        with self.assertRaises(ValueError):
            RecordedModelResult("SUCCESS", "p", input_tokens=-1)
        with self.assertRaises(ValueError):
            RecordedModelResult("SUCCESS", "p", input_tokens=1, output_tokens=2, total_tokens=4)
        with self.assertRaises(ValueError):
            RecordedModelResult("invented", "p")
        value = self.verification()
        bad_critic = dict(value.critic_results[0]) | {"provider_outcome": "invented"}
        with self.assertRaises(ValueError):
            VerificationResult(**{**value.__dict__, "critic_results": (bad_critic,)})
        repair = {"provider":"yandex","model":"b","role":"REPAIR_B","attempt":1,"call_identity":"r1","input_findings_sha256":SHA,"proposed_candidate_sha256":SHA,"outcome":"SUCCESS"}
        one = VerificationResult(**{**value.__dict__, "repair_evidence": (repair,)})
        self.assertEqual(len(one.repair_evidence), 1)
        second = dict(repair) | {"role":"REPAIR_A", "attempt":2, "call_identity":"r2"}
        two = VerificationResult(**{**value.__dict__, "repair_evidence": (repair, second)})
        self.assertEqual(len(two.repair_evidence), 2)

    def test_f_actual_budget_unknown_gate_rotation_nonce(self):
        paid = self.reservation(identity="paid")
        self.state.reserve_model_call(paid)
        self.state.record_model_result(paid.identity, RecordedModelResult("SUCCESS", "p", actual_cost_rub=Decimal("1.25")))
        null = self.reservation(identity="null")
        self.state.reserve_model_call(null)
        self.state.record_model_result(null.identity, RecordedModelResult("SUCCESS", "p"))
        self.assertEqual(self.state.actual_spend_current_moscow_day(), Decimal("1.25"))
        unknown = self.reservation(identity="u")
        self.state.reserve_model_call(unknown); self.state.mark_model_unknown(unknown.identity, UnknownModelOutcome())
        self.assertTrue(self.state.has_unknown_for_current_moscow_day())
        n1, n2 = new_claim_nonce(), new_claim_nonce()
        self.assertTrue(self.state.advance_rotation(RotationClaim("CRITIC_B", 0, 1, n1)).won)
        self.assertFalse(self.state.advance_rotation(RotationClaim("CRITIC_B", 0, 1, n2)).won)
        self.assertEqual(self.state.get_rotation("CRITIC_B").rotation_nonce, n1)

    def test_g_logical_expiry_and_accepted_continue_only_refresh(self):
        self.state.receive_command(self.receipt())
        self.state.create_lineage(self.lineage())
        self.state.put_verification_result(self.verification())
        reservation = self.reservation(); self.state.reserve_model_call(reservation)
        self.clock.advance(timedelta(days=13, hours=23))
        self.assertTrue(self.state.record_accepted_continue("lin-1", AcceptedContinue({"kind":"force"}, 1)).changed)
        self.clock.advance(timedelta(hours=2))
        self.assertIsNone(self.state.get_model_call(reservation.identity))
        self.assertIsNone(self.state.get_verification_result("v1"))
        self.assertIsNotNone(self.state.get_lineage("lin-1"))
        self.assertNotIn("refresh", " ".join(name for name, _ in inspect.getmembers(type(self.state), inspect.isfunction) if name != "record_accepted_continue"))

    def test_h_verification_immutable_duplicate(self):
        value = self.verification()
        self.assertTrue(self.state.put_verification_result(value).won)
        self.assertEqual(self.state.put_verification_result(value).status, ClaimStatus.EXISTING_SAME)
        changed = VerificationResult(**{**value.__dict__, "final_verdict":"BLOCKED"})
        self.assertEqual(self.state.put_verification_result(changed).status, ClaimStatus.CONFLICT)

    def test_h_lineage_duplicate_compares_complete_identity(self):
        value = self.lineage()
        self.assertEqual(self.state.create_lineage(value).status, ClaimStatus.CREATED)
        self.assertEqual(self.state.create_lineage(value).status, ClaimStatus.EXISTING_SAME)
        changed = NewLineage(**{**value.__dict__, "main_tree_sha": "6" * 40})
        self.assertEqual(self.state.create_lineage(changed).status, ClaimStatus.CONFLICT)


if __name__ == "__main__": unittest.main()
