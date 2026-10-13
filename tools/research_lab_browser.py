"""Import, verify or serve the local read-only historical Research Lab."""
from __future__ import annotations
import argparse

import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from research_engine.v10.continuous.research_lab_archive import (
    import_completed, load_archive, verified_file, require)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', type=Path, default=ROOT/'reports/research_lab_archive')
    sub = parser.add_subparsers(dest='command', required=True)
    imp = sub.add_parser('import', help='Copy and verify a completed transfer; never mutate its source')
    imp.add_argument('--package', type=Path, required=True)
    sub.add_parser('verify', help='Verify every published archive file, including copied originals')
    server = sub.add_parser('serve', help='Serve the browser Lab on loopback; no import/write endpoints')
    server.add_argument('--port', type=int, default=8765)
    args = parser.parse_args()
    if args.command == 'import':
        print(json.dumps(import_completed(args.package, args.archive), indent=2)); return 0
    directory, catalog, summary, index = load_archive(args.archive)
    if args.command == 'verify':
        for run in index['runs']:
            d, c, s, _ = load_archive(args.archive, run['archive_id'])
            for relative in c['files']:
                verified_file(d, c, relative)
            require(len({q['question_id'] for q in s['questions']}) == 70, 'QUESTION_IDENTITY_COUNT_INVALID')
        print(json.dumps({'status':'VERIFIED_LOCAL_ARCHIVE','runs':len(index['runs']),
                          'latest':index['latest'],'questions':len(summary['questions']),
                          'raw_evidence_body_verification':False}, indent=2)); return 0
    # The original Lab is the only browser interface. Keep import/verify CLI
    # compatibility; do not start the retired parallel archive viewer.
    from research_engine.v10.lab import server as original_lab
    return original_lab.main(["--read-only", "--archive", str(args.archive),
                              "--port", str(args.port)])

if __name__ == '__main__':
    try: raise SystemExit(main())
    except (ValueError, OSError, KeyError) as exc:
        print(f'RESEARCH_LAB_FAIL_CLOSED: {exc}', file=sys.stderr); raise SystemExit(1)
