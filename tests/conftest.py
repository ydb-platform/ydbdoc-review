"""Offline test isolation, including child processes."""
import os

import pytest


@pytest.fixture(autouse=True)
def isolated_credentials(monkeypatch):
    for name in tuple(os.environ):
        if any(word in name.upper() for word in ('TOKEN', 'SECRET', 'PASSWORD', 'API_KEY', 'SA_KEY', 'CREDENTIAL')):
            monkeypatch.delenv(name, raising=False)


def pytest_addoption(parser):
    from tests.ci_groups import GROUPS
    parser.addoption('--ci-group', choices=GROUPS)
    parser.addoption('--ci-inventory', help='Write complete nodeid/group inventory JSON')


def pytest_collection_modifyitems(config, items):
    import json
    from pathlib import Path

    from tests.ci_groups import GROUPS, group_for
    inventory = [{'nodeid': item.nodeid, 'group': group_for(item.nodeid)} for item in items]
    assert len({row['nodeid'] for row in inventory}) == len(inventory), 'Duplicate nodeids'
    assert all(row['group'] in GROUPS for row in inventory)
    if output := config.getoption('--ci-inventory'):
        Path(output).write_text(json.dumps(inventory, indent=2)+'\n')
    if group := config.getoption('--ci-group'):
        selected = [item for item in items if group_for(item.nodeid) == group]
        rejected = [item for item in items if group_for(item.nodeid) != group]
        config.hook.pytest_deselected(items=rejected)
        items[:] = selected
        assert items, f'Empty required CI group: {group}'
