import sys
sys.path.insert(0, '.')
from core.production_data_contract import PRODUCTION_SCHEMA_REGISTRY
print('slippage_journal in registry:', 'slippage_journal' in PRODUCTION_SCHEMA_REGISTRY)
from research_engine.dataset_disposition import DISPOSITIONS
for name in ('slippage_journal', 'execution_attempts', 'execution_results',
             'execution_context', 'protection_audit'):
    d = DISPOSITIONS.get(name)
    print(name, '->', d.research_class if d else 'NO DISPOSITION')
