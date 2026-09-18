#!/bin/sh
set -eu
case "${INPUT_MODE:-run}" in
  run|doc_translate) mode=doc_translate ;;
  verify|doc_verify) mode=doc_verify ;;
  continue|doc_continue) mode=doc_continue ;;
  *) echo 'Unsupported mode: use run, verify, continue or doc_* names' >&2; exit 2 ;;
esac
set -- "$mode" --repo "$INPUT_REPO" --pr "$INPUT_PR"
if [ -n "${INPUT_CONFIG:-}" ]; then
  set -- "$@" --config "$INPUT_CONFIG"
fi
exec ydbdoc-review "$@"
