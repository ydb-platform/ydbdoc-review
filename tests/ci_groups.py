"""Exhaustive, disjoint CI partition. Every collected node gets exactly one group."""
GROUPS = ('unit-parser', 'quality-links', 'translate', 'verify', 'continue', 'store-reports', 'cli', 'scale')


def group_for(nodeid):
    path = nodeid.split('::')[0]
    name = path.rsplit('/', 1)[-1]
    if name == 'test_t15_scale_f13.py':
        return 'scale'
    if 't15' in name or name == 'test_settings.py':
        return 'cli'
    if any(word in name for word in ('t13', 'continue')):
        return 'continue'
    if any(word in name for word in ('t12', 't14', 'report')):
        return 'store-reports'
    if any(word in name for word in ('t11', 'verify')):
        return 'verify'
    if any(word in name for word in ('t10', 'translate')):
        return 'translate'
    if any(word in name for word in ('quality', 't07', 't08', 't09', 'links_build')):
        return 'quality-links'
    return 'unit-parser'
