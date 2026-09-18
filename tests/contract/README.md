# Offline contract tests

`test_t15_cli.py` запускает все три настоящих CLI во внешнем Python-процессе. HTTP и YDB SDK подменяются, данные сохраняются в SQLite между процессами; Git fetch/push/immutable candidates, runner, quality, report и store реальны. Только YFM boundary в этих CLI tests заглушена; отдельные T08–T14 component suites выполняют actual YFM 5.61.0 на Node 24.

Сеть запрещена pytest-socket, host credentials очищаются. Никаких pending skips/catalog placeholders. `tests/ci_groups.py` задаёт полное непересекающееся распределение nodeids для PR CI. SDK doubles не доказывают live YQL compilation или deployed schema. Merged-verify characterization фиксирует текущее ограничение, не согласованное требование.
