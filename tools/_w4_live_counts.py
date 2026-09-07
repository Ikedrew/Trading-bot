import sys
sys.path.insert(0, '.')

for name in ('execution_attempts', 'execution_results', 'execution_context',
             'protection_audit', 'trade_truth'):
    try:
        from research_engine.data_access.loaders import (
            load_execution_attempts, load_execution_results,
            load_execution_context, load_protection_audit, load_trade_truth,
        )
        fn = {
            'execution_attempts': load_execution_attempts,
            'execution_results': load_execution_results,
            'execution_context': load_execution_context,
            'protection_audit': load_protection_audit,
            'trade_truth': load_trade_truth,
        }[name]
        recs = fn()
        print(f"{name}: {len(recs)} records")
        if recs:
            print(f"  sample keys: {sorted(recs[0].keys())[:18]}")
    except Exception as e:
        print(f"{name}: ERROR {type(e).__name__}: {str(e)[:180]}")
