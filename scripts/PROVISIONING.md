# Provisioning and legacy cutover

These artifacts have **not** been executed against live YDB. First run them in a disposable database, review SDK/server compatibility and retain the command output. The product never creates or alters tables.

Use the installed project environment, `YDBDOC_YDB_ENDPOINT`, `YDBDOC_YDB_DATABASE`, and the existing `YDB_SA_KEY` secret. No secrets belong in command arguments or source control.

For a fresh database: `python scripts/provision_ydb.py --new`. This creates `runs` with primary key `(run_id, entry_id)` and no TTL, and `run_objects` with key `(run_id, object_key, generation, part_no)` and 14-day TTL on `created_at`. The adjacent SQL files describe precisely the same tables. Existing tables are never dropped/recreated automatically.

For the previous schema:

1. Disable label workflows and stop all old/new writers. Take and verify the organisation's normal database backup before migration. Save a schema description and daily row counts/sums, including unknown costs.
2. Run `python scripts/provision_ydb.py --migrate-legacy migration_20260918` (choose an unused suffix). The script validates the exact legacy primary keys, creates separate staging tables, reads old financial rows, and copies each total once under a deterministic unique key. It validates per-day counts, RUB sums and unknown counts before requesting a single SDK rename operation. Invalid/missing costs remain errors/unknowns, never invented zeros. Original totals remain unsplit (`legacy-unsplit`), since old aggregate rows cannot truthfully reconstruct operation costs.
3. Both old tables remain intact under `runs_preT15_migration_20260918` and `run_objects_preT15_migration_20260918`. The new `runs` includes all old financial totals, so today's admission does not reset the budget. The context table is fresh: old checkpoint schemas are not compatible with continuation. Start a new translate/verify. The archived context retains its old TTL; the financial archive has no TTL.
4. Verify table PKs/types/TTL and daily sums again, then enable only the final three-mode workflows. Save the migration output and archive names with the deployment record. Do not delete financial archives as part of deployment.

An interrupted run may leave staging tables. Inspect schema and counts; do not rerun with the same suffix or drop tables blindly. A failed rename must be investigated before enabling either writer. Rollback before accepting new paid work can rename the archived originals back, preserving the new tables under another unused name. After new paid work, restoring the old ledger alone would lose those expenses; reconcile/export all new financial rows first. No automatic rollback or destructive cleanup is provided.

The SDK-boundary tests validate conversion, row keys, totals and DDL request shapes. They do not prove live YQL compilation, permissions or an existing deployed schema.
