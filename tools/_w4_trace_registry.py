import sys
sys.path.insert(0, '.')
from research_engine.registry.research_question_registry import REGISTRY_BY_ID
for qid in ('X1', 'X2', 'X3', 'X4', 'X5', 'X6'):
    q = REGISTRY_BY_ID[qid]
    print(f"{qid}: [{q.runner_module or 'NONE'}.{q.runner_function or 'NONE'}]")
    print(f"  title: {q.title}")
    print(f"  desc: {q.description[:160]}")
    print(f"  fields: {q.required_fields}")
    print(f"  sources: {[d.value for d in q.data_sources]}")
    print()
