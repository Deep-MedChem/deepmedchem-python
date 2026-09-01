from deepmedchem import Client, Run, Selection

template = (
    Selection.from_database("enamine-real-v5a", release="2026-08-29.1")
    .ranked()
    .require_preset("lipinski-ro5/v1")
    .maximize_similarity("rdkit.ecfp4_tanimoto", reference="query")
    .limit(10)
)
spec = Run.selection_batch(
    template=template,
    items={"lead-001": {"query": "CCO"}, "lead-002": {"query": "CCN"}},
)

with Client() as dmc:
    dmc.runs.estimate(spec)
    run = dmc.runs.create(spec, idempotency_key="lead-set-2026-08-v1")
    for event in dmc.runs.watch(run.id, after=run.last_event_sequence):
        print(event.type)
    terminal = dmc.runs.wait(run.id)
    for item in dmc.runs.iter_results(run.id):
        print(item.id, item.result if item.ok else item.error)
