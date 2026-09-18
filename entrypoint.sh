#!/bin/sh
set -eu
case "${INPUT_MODE:-}" in
  doc_translate|doc_verify|doc_continue) ;;
  *) echo 'Unsupported mode: use doc_translate, doc_verify or doc_continue' >&2; exit 2 ;;
esac
exec ydbdoc-review "$INPUT_MODE" --repo "$INPUT_REPO" --pr "$INPUT_PR" --config "$INPUT_CONFIG"
