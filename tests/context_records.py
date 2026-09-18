"""Tests inspect full call data through durable references, as required by §8.1."""
from ydbdoc_review.store import decode


def context_with_records(store, run_id):
    context = store.context(run_id)
    return {**context, **{kind: [decode(store.get(run_id, key)) for key in context[kind]]
                          for kind in ('requests', 'attempts')}}
