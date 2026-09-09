"""python -m core.accounts [--config-only] [--json] [--request request.json]."""

import argparse
import json
from pathlib import Path

from .config import load_accounts
from .identity import CanonicalExecutionRequest
from .manager import diagnose


def main(argv=None):
    parser = argparse.ArgumentParser(description='Read-only isolated MT5 demo account diagnostics; never trades or switches logins.')
    parser.add_argument('--config-only', action='store_true', help='Validate configuration without connecting to MT5')
    parser.add_argument('--json', action='store_true', help='Emit full account and ten-symbol matrices as JSON')
    parser.add_argument('--symbol-inventory', action='store_true', help='Include all broker symbol descriptions/specifications in JSON for explicit mapping review')
    parser.add_argument('--request', type=Path, help='Existing canonical execution request JSON for read-only broker eligibility')
    parser.add_argument('--timeout', type=float, default=25, help='Per-account worker deadline in seconds')
    args = parser.parse_args(argv)
    if not 1 <= args.timeout <= 60:
        parser.error('timeout must be between 1 and 60 seconds')
    # Same environment/.env convention as core.config, with no startup imports.
    try:
        from dotenv import load_dotenv
        load_dotenv(Path(__file__).resolve().parents[2] / '.env', override=False)
    except ImportError:
        pass
    request = None
    if args.request:
        try:
            request = CanonicalExecutionRequest(**json.loads(args.request.read_text(encoding='utf-8')))
        except Exception:
            parser.error('invalid canonical request JSON')
    reports = diagnose(load_accounts(), request, timeout=args.timeout, config_only=args.config_only,
                       include_symbol_inventory=args.symbol_inventory)
    if args.json:
        print(json.dumps(reports, indent=2, allow_nan=False))
    else:
        for r in reports:
            print(f"{r['account_id']} connected={r['connected']} login={r['login']} server={r['server']} "
                  f"balance={r['balance']} equity={r['equity']} margin_free={r['margin_free']} "
                  f"currency={r['currency']} leverage={r['leverage']} "
                  f"trade_allowed={r['trade_allowed']} trade_expert={r['trade_expert']} "
                  f"execution_enabled=False reasons={','.join(r['reasons']) or '-'}")
            for s in r['symbols']:
                print(f"  {s['canonical_symbol']:7} {s['broker_symbol'] or '-':16} {s['status']:12} "
                      f"digits={s['digits']} point={s['point']} contract={s['contract_size']} "
                      f"volume={s['volume_min']}/{s['volume_step']}/{s['volume_max']} "
                      f"stops={s['trade_stops_level']} freeze={s['trade_freeze_level']} "
                      f"candidates={','.join(s['candidates'])}")
            if 'eligibility' in r:
                print('  eligibility=' + json.dumps(r['eligibility']))
    if args.config_only:
        return int(any(r['enabled'] and r['reasons'] != ['CONFIGURED_NOT_PROBED'] for r in reports))
    return int(any(r['enabled'] and (
        not r['connected'] or r['reasons']
        or any(s['status'] != 'available' for s in r['symbols'])
        or ('eligibility' in r and not r['eligibility']['broker_eligible'])
    ) for r in reports))


if __name__ == '__main__':
    raise SystemExit(main())
