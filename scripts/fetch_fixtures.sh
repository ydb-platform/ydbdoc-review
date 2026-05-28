#!/bin/bash
# scripts/fetch_fixtures.sh
# Downloads a set of real YDB documentation files for round-trip testing.

set -euo pipefail

BASE="https://raw.githubusercontent.com/ydb-platform/ydb/main"
FIXTURES_DIR="tests/fixtures/markdown_files"

mkdir -p "$FIXTURES_DIR/ru" "$FIXTURES_DIR/en"

# Format: relative_path_under_ydb/docs
FILES=(
  "ru/core/devops/deployment-options/manual/initial-deployment/deployment-configuration-v1.md"
  "en/core/devops/deployment-options/manual/initial-deployment/deployment-configuration-v1.md"

  "ru/core/devops/deployment-options/manual/initial-deployment/deployment-configuration-v2.md"
  "en/core/devops/deployment-options/manual/initial-deployment/deployment-configuration-v2.md"

  "ru/core/devops/deployment-options/manual/initial-deployment/deployment-preparation.md"
  "en/core/devops/deployment-options/manual/initial-deployment/deployment-preparation.md"

  "ru/core/devops/backup-and-recovery/system-tablet-backup.md"
  "en/core/devops/backup-and-recovery/system-tablet-backup.md"

  "ru/core/contributor/configuration-v2.md"
  "en/core/contributor/configuration-v2.md"

  "ru/core/yql/reference/syntax/create-streaming-query.md"  
  "en/core/yql/reference/syntax/create-streaming-query.md"

  "ru/core/yql/reference/types/primitive.md"
  "en/core/yql/reference/types/primitive.md"

  "ru/core/integrations/orm/spring-data-jdbc.md"
  "en/core/integrations/orm/spring-data-jdbc.md"

  # YDB CLI commands
  # The problematic file from failed PR #41736
  "ru/core/reference/ydb-cli/parameterized-query-execution.md"
  "en/core/reference/ydb-cli/parameterized-query-execution.md"

  # Short conceptual pages
  "ru/core/concepts/_index.md"
  "en/core/concepts/_index.md"

  # Page with lots of {% note %}
  "ru/core/concepts/transactions.md"
  "en/core/concepts/transactions.md"

  # Page with {% list tabs %}
  "ru/core/reference/ydb-sdk/example/_includes/init.md"
  "en/core/reference/ydb-sdk/example/_includes/init.md"

  # Page with {% cut %}
  "ru/core/devops/configuration-management/configuration-v1/cluster-expansion.md"
  "en/core/devops/configuration-management/configuration-v1/cluster-expansion.md"

  # YQL reference (complex tables, code)
  "ru/core/yql/reference/syntax/declare.md"
  "en/core/yql/reference/syntax/declare.md"

  # Long conceptual page with everything
  "ru/core/concepts/glossary.md"
  "en/core/concepts/glossary.md"

  # CLI commands
  "ru/core/reference/ydb-cli/sql.md"
  "en/core/reference/ydb-cli/sql.md"

  # Includes-heavy page
  "ru/core/quickstart.md"
  "en/core/quickstart.md"
)

for rel in "${FILES[@]}"; do
  url="$BASE/ydb/docs/$rel"
  out="$FIXTURES_DIR/$rel"
  mkdir -p "$(dirname "$out")"
  echo "→ $rel"
  curl -fsSL "$url" -o "$out" || echo "  ⚠ failed: $url"
done

echo "Done. Files in $FIXTURES_DIR"
