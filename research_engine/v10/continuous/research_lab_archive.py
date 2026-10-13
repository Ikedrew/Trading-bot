"""Read-only completed-artifact import for the browser Research Lab.

Source artifacts are copied byte-for-byte and hash-checked. The browser index is
an explicitly derived archive view, never a new canonical research projection.
No evaluator, coordinator, SDK client, or scientific transition is invoked.
"""
from __future__ import annotations
import csv
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import re
import shutil
import uuid

ROOT = Path(__file__).resolve().parents[3]
SAFE = re.compile(r"^[A-Za-z0-9_.-]+$")
HEX = re.compile(r"^[a-f0-9]{64}$")


def require(value, message):
    if not value:
        raise ValueError(message)


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def atomic(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temp.open('w', encoding='utf-8', newline='\n') as stream:
            json.dump(value, stream, ensure_ascii=False, allow_nan=False, sort_keys=True)
            stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def safe_path(root, relative):
    require(isinstance(relative, str) and relative, 'EMPTY_ARTIFACT_PATH')
    win = PureWindowsPath(relative)
    require(not win.is_absolute() and not win.drive and '..' not in win.parts,
            'UNSAFE_ARTIFACT_PATH:' + relative)
    parts = relative.replace('\\', '/').split('/')
    require(all(part not in ('', '.', '..') and ':' not in part and part == part.rstrip(' .') and not PureWindowsPath(part).is_reserved() for part in parts), 'UNSAFE_ARTIFACT_PATH')
    current = root
    for part in parts:
        current = current / part
        require(not current.is_symlink() and not getattr(current, 'is_junction', lambda: False)(),
                'REPARSE_ARTIFACT_REJECTED:' + relative)
    require(current.resolve().is_relative_to(root.resolve()), 'ARTIFACT_ESCAPE')
    return current


def read_json(path):
    with path.open('r', encoding='utf-8-sig') as stream:
        return json.load(stream)


def stamp(value):
    dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    require(dt.tzinfo is not None, 'TIMESTAMP_WITHOUT_TIMEZONE')
    return dt.astimezone(timezone.utc)


def import_completed(package, destination):
    import ijson
    from research_engine.registry.baseline_manifest import BASELINE_QUESTION_IDS
    from research_engine.v10.continuous.question_cycle_state import CanonicalQuestionResult, CanonicalQuestionCycleResult
    from research_engine.v10.continuous.scientific_state_store import ScientificStateStore
    from research_engine.v10.continuous.scientific_state_bridge import ScientificStateBridgeResult
    from research_engine.v10.investigation_snapshot import InvestigationSnapshot
    from research_engine.control_plane.stage4_dataset_snapshot import canonical_json
    package = Path(package).resolve(); destination = Path(destination).resolve()
    require(destination.is_relative_to(ROOT) and not destination.is_relative_to(package), 'OUTPUT_MUST_BE_INSIDE_STAGE_A_AND_OUTSIDE_SOURCE')
    require(not package.is_relative_to(destination), 'SOURCE_CANNOT_BE_INSIDE_ARCHIVE')
    require('trading bot build' not in {p.casefold() for p in package.parts}, 'PRODUCTION_SOURCE_FORBIDDEN')
    destination.mkdir(parents=True, exist_ok=True)
    lock = destination / 'import.lock'
    fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    os.close(fd)
    stage = destination / ('.pending-' + uuid.uuid4().hex)
    stage.mkdir()
    try:
        manifest = safe_path(package, 'transfer_hashes.csv')
        archive_id = digest(manifest)
        entries = {}; seen_paths = set()
        with manifest.open(newline='', encoding='utf-8-sig') as stream:
            for row in csv.DictReader(stream):
                rel = row['RelativePath'].replace('\\', '/')
                require(rel.casefold() not in seen_paths and rel.casefold() != 'transfer_hashes.csv', 'DUPLICATE_HASH_MANIFEST_PATH')
                seen_paths.add(rel.casefold())
                expected = row['SHA256'].lower(); size = int(row['SizeBytes'])
                require(HEX.fullmatch(expected) and size >= 0, 'INVALID_HASH_MANIFEST')
                source = safe_path(package, rel)
                target = safe_path(stage / 'source', rel)
                require(source.is_file() and source.stat().st_size == size, 'ARTIFACT_MISSING_OR_SIZE_MISMATCH:' + rel)
                target.parent.mkdir(parents=True, exist_ok=True)
                h = hashlib.sha256()
                with source.open('rb') as src, target.open('xb') as dst:
                    for block in iter(lambda: src.read(1024*1024), b''):
                        h.update(block); dst.write(block)
                    dst.flush(); os.fsync(dst.fileno())
                require(h.hexdigest() == expected, 'ARTIFACT_SHA256_MISMATCH:' + rel)
                entries[rel] = expected
        for required in ('question_cycles/current_projection.json', 'scientific_state/state.json',
                         'optimisation_registry/registry.json', 'bridge_execution_receipt.json'):
            require(required in entries, 'REQUIRED_HASHED_ARTIFACT_MISSING:' + required)
        shutil.copyfile(manifest, stage / 'source/transfer_hashes.csv')
        require(digest(stage / 'source/transfer_hashes.csv') == archive_id, 'TRANSFER_MANIFEST_CHANGED')
        entries['transfer_hashes.csv'] = archive_id
        source = stage / 'source'
        def required_json(rel):
            require(rel in entries, 'REQUIRED_HASHED_ARTIFACT_MISSING:' + rel)
            return read_json(safe_path(source, rel))
        bridge_wrapper = required_json('bridge_execution_receipt.json')
        bridge = ScientificStateBridgeResult.from_dict(bridge_wrapper['bridge_result'])
        require(bridge.status in ('COMPLETED', 'COMPLETED_WITH_REVIEW_REQUIRED', 'NO_SCIENTIFIC_STATE_CHANGE'), 'BRIDGE_NOT_COMPLETE')
        require(not bridge.failures, 'BRIDGE_ITEM_FAILURES_REQUIRE_REVIEW')
        bridge_row = required_json('scientific_state/runs/' + bridge.bridge_run_id + '.json')
        require(canonical_json(bridge_row) == canonical_json(bridge.to_dict()), 'BRIDGE_RECEIPT_MISMATCH')
        cycle_raw = required_json('question_cycles/cycles/' + bridge.source_cycle_id + '.json')
        cycle = CanonicalQuestionCycleResult.from_dict(cycle_raw)
        require(cycle.cycle_status in ('COMPLETED', 'COMPLETED_WITH_QUESTION_FAILURES'), 'QUESTION_CYCLE_NOT_COMPLETE')
        require(cycle.snapshot_id == bridge.snapshot_id and cycle.cycle_id == bridge.source_cycle_id, 'CYCLE_BRIDGE_LINEAGE_MISMATCH')
        snapshot_raw = required_json('snapshot/manifests/' + cycle.snapshot_id + '.json')
        snapshot = InvestigationSnapshot.from_dict(snapshot_raw)
        require(snapshot.snapshot_fingerprint == cycle.fingerprint and snapshot.evidence_epoch == cycle.investigation_epoch, 'SNAPSHOT_LINEAGE_MISMATCH')
        require(json.loads(snapshot.source_authority_json).get('bucket') not in ('offline-pipeline-benchmark', 'incremental-test', 'question-cycle-test'), 'SYNTHETIC_FIXTURE_NOT_HISTORICAL_RESEARCH')
        state = required_json('scientific_state/state.json'); ScientificStateStore._validate(state)
        registry = required_json('optimisation_registry/registry.json')
        require(set(registry) == {'hypotheses', 'candidates', 'plans'}, 'REGISTRY_SECTIONS_INVALID')
        current = state['current']
        require(set(current['hypotheses']) == set(registry['hypotheses']), 'HYPOTHESIS_CURRENT_REGISTRY_MISMATCH')
        require(set(current['candidates']) == set(registry['candidates']), 'CANDIDATE_CURRENT_REGISTRY_MISMATCH')
        for fid, ref in current['findings'].items():
            finding = state['findings'][fid][-1]
            require(ref == f"{fid}:v{finding['finding_version']}", 'FINDING_CURRENT_VERSION_MISMATCH')
        for hid, row in registry['hypotheses'].items():
            require(row['hypothesis_id'] == hid and row['source_finding'] in state['findings'] and current['hypotheses'][hid] in row.get('source_finding_versions', []) and current['hypotheses'][hid].startswith(row['source_finding'] + ':v'), 'HYPOTHESIS_LINEAGE_MISMATCH')
        for cid, row in registry['candidates'].items():
            require(row['candidate_id'] == cid and row.get('hypothesis_id') in registry['hypotheses'], 'CANDIDATE_LINEAGE_MISMATCH')
            require(state['candidate_history'].get(cid), 'CANDIDATE_HISTORY_MISSING')
            require(current['candidates'][cid] == state['candidate_history'][cid][-1]['finding_version'], 'CANDIDATE_VERSION_MISMATCH')
        projection_path = source / 'question_cycles/current_projection.json'
        with projection_path.open('rb') as stream:
            header = {}
            # Scalars at the top level only: never collect the 871 MB questions map.
            for prefix, event, value in ijson.parse(stream, use_float=True):
                if '.' not in prefix and event in ('string', 'number', 'boolean', 'null'):
                    header[prefix] = value
        require(header.get('cycle_schema') == 'canonical_question_cycle_v1' and header.get('cycle_id') == cycle.cycle_id
                and header.get('cycle_snapshot_id') == cycle.snapshot_id, 'QUESTION_PROJECTION_LINEAGE_MISMATCH')
        summaries = []; known = set(); files = {}
        def write_detail(kind, identity, field, value):
            require(all(SAFE.fullmatch(x) for x in (kind, identity, field)), 'UNSAFE_DETAIL_KEY')
            rel = f'details/{kind}/{identity}/{field}.json'
            atomic(stage / rel, value)
            files[rel] = {'sha256': digest(stage / rel), 'bytes': (stage / rel).stat().st_size}
            return {'field': field, 'kind': 'list' if isinstance(value, list) else 'map' if isinstance(value, dict) else 'scalar',
                    'count': len(value) if isinstance(value, (list, dict)) else None, 'path': rel}
        snapshots = {snapshot.snapshot_id: snapshot}
        with projection_path.open('rb') as stream:
            for qid, row in ijson.kvitems(stream, 'questions', use_float=True):
                require(qid in BASELINE_QUESTION_IDS and qid not in known, 'DUPLICATE_OR_UNKNOWN_QUESTION')
                known.add(qid)
                require(row.get('question_id', qid) == qid, 'QUESTION_ROW_ID_MISMATCH')
                raw = row['result']; result = CanonicalQuestionResult.from_dict(raw)
                require(result.question_id == qid and cycle.result_ids[qid] == result.result_id, 'QUESTION_RESULT_ID_MISMATCH')
                if result.snapshot_id not in snapshots:
                    snapshots[result.snapshot_id] = InvestigationSnapshot.from_dict(required_json('snapshot/manifests/' + result.snapshot_id + '.json'))
                result_snapshot = snapshots[result.snapshot_id]
                require(result.snapshot_fingerprint == result_snapshot.snapshot_fingerprint, 'RESULT_SNAPSHOT_FINGERPRINT_MISMATCH')
                bound = {(b.dataset, obj.identifier): obj for b in result_snapshot.datasets for obj in b.objects}
                for ref in result.evidence_references:
                    if ref.get('identifier'):
                        obj = bound.get((ref.get('dataset'), ref['identifier']))
                        require(obj is not None and ref.get('content_sha256') == obj.content_sha256, 'RESULT_EVIDENCE_MEMBERSHIP_MISMATCH')
                descriptors = []
                for key, value in row.items():
                    if key == 'result':
                        for field, item in value.items():
                            descriptors.append(write_detail('question', qid, 'result.' + field, item))
                    else:
                        descriptors.append(write_detail('question', qid, key, value))
                answer = raw.get('substantive_answer')
                summaries.append({'question_id': qid, 'title': row.get('definition', {}).get('title', qid),
                    'status': result.status, 'answer_preview': answer[:2000] if isinstance(answer, str) else None,
                    'answer_is_structured': isinstance(answer, (dict, list)), 'result_id': result.result_id,
                    'snapshot_id': result.snapshot_id, 'evaluated_at': result.evaluated_at,
                    'sample_n': result.sample_n, 'minimum_required_n': result.minimum_required_n,
                    'sample_deficit': result.sample_deficit, 'work_state': row.get('work_state'),
                    'execution_freshness': row.get('execution_freshness'),
                    'failure_reason': result.failure_reason, 'reason_code': result.reason_code,
                    'missing_evidence': list(result.missing_evidence), 'fields': descriptors})
                del result, row, raw
        require(known == set(BASELINE_QUESTION_IDS) and set(cycle.result_ids) == known, 'CANONICAL_70_INVARIANT_FAILED')
        entities = {'finding': [], 'hypothesis': [], 'candidate': []}
        for fid, versions in state['findings'].items():
            finding = versions[-1]
            require(set(finding.get('source_question_ids', [])) <= known, 'FINDING_QUESTION_MISSING')
            require(set(finding.get('source_question_result_ids', [])) <= set(cycle.result_ids.values()), 'FINDING_RESULT_LINEAGE_MISMATCH')
            fields = [write_detail('finding', fid, k, v) for k, v in finding.items()]
            fields.append(write_detail('finding', fid, 'version_history', versions))
            entities['finding'].append({'id': fid, 'status': finding.get('status'), 'statement': finding.get('proposition'), 'links': state['dependencies'].get(fid, {}), 'fields': fields})
        for kind, plural, identity in (('hypothesis', 'hypotheses', 'hypothesis_id'), ('candidate', 'candidates', 'candidate_id')):
            for key, row in registry[plural].items():
                entities[kind].append({'id': key, 'status': row.get('status'), 'statement': row.get('statement') or row.get('expected_outcome'),
                    'fields': [write_detail(kind, key, k, v) for k, v in row.items()]})
                history_key = 'hypothesis_history' if kind == 'hypothesis' else 'candidate_history'
                entities[kind][-1]['fields'].append(write_detail(kind, key, 'scientific_state_history', state[history_key].get(key, [])))
                if kind == 'candidate':
                    plans = [p for p in registry['plans'].values() if p.get('candidate_id') == key]
                    entities[kind][-1]['fields'].append(write_detail(kind, key, 'validation_plans', plans))
        by_id = {q['question_id']: q for q in summaries}
        for qid, q in by_id.items():
            q['linked_findings'] = [fid for fid, rows in state['findings'].items() if qid in rows[-1].get('source_question_ids', [])]
            q['linked_hypotheses'] = sorted({h for f in q['linked_findings'] for h in state['dependencies'].get(f, {}).get('hypotheses', [])})
            q['linked_candidates'] = sorted({c for f in q['linked_findings'] for c in state['dependencies'].get(f, {}).get('candidates', [])})
        completed = cycle.completed_at; stamp(completed)
        summary = {'schema': 'research_lab_completed_archive_v1', 'archive_id': archive_id,
            'cycle_id': cycle.cycle_id, 'snapshot_id': cycle.snapshot_id, 'snapshot_fingerprint': cycle.fingerprint,
            'bridge_run_id': bridge.bridge_run_id, 'cycle_status': cycle.cycle_status, 'bridge_status': bridge.status,
            'completed_at': completed, 'imported_at': datetime.now(timezone.utc).isoformat(),
            'freshness': 'HISTORICAL_OFFLINE_COPY', 'live_currentness_verified': False,
            'newer_source': 'No live source connected; import a separately transferred verified completed package to refresh.',
            'verification': {'transport_sha256': True, 'result_identities': True, 'snapshot_manifest_identity': True,
                'evidence_membership_references': True, 'raw_evidence_bodies_verified': False,
                'source_authenticity': 'Operator-provided verified transfer; checksums are integrity checks, not signatures.'},
            'counts': dict(Counter(q['status'] for q in summaries)),
            'questions': sorted(summaries, key=lambda q: list(BASELINE_QUESTION_IDS).index(q['question_id'])),
            'entities': entities, 'review_required': list(bridge.review_required),
            'candidate_design_required': list(bridge.candidate_design_required),
            'validation_handoff': list(bridge.validation_handoff), 'governance_signals': list(bridge.governance_signals),
            'validation_queue_available': False,
            'governance': 'Read-only historical research. Findings, hypotheses and candidates do not authorize trading or strategy changes.'}
        atomic(stage / 'summary.json', summary)
        files['summary.json'] = {'sha256': digest(stage / 'summary.json'), 'bytes': (stage / 'summary.json').stat().st_size}
        for rel, h in entries.items():
            files['source/' + rel] = {'sha256': h, 'bytes': (source / rel).stat().st_size}
        atomic(stage / 'catalog.json', {'schema': 'research_lab_archive_catalog_v1', 'archive_id': archive_id, 'files': files})
        catalog_hash = digest(stage / 'catalog.json')
        pointer_path = destination / 'index.json'
        pointer = read_json(pointer_path) if pointer_path.exists() else {'schema': 'research_lab_archive_index_v1', 'runs': [], 'latest': None}
        require(pointer.get('schema') == 'research_lab_archive_index_v1', 'ARCHIVE_INDEX_SCHEMA_INVALID')
        for old in pointer['runs']:
            require(old['cycle_id'] != cycle.cycle_id or old['archive_id'] == archive_id, 'CONFLICTING_COMPLETED_CYCLE_IMPORT')
        if pointer['runs'] and archive_id not in {r['archive_id'] for r in pointer['runs']}:
            previous = max(pointer['runs'], key=lambda r: stamp(r['completed_at']))
            require(stamp(completed) != stamp(previous['completed_at']), 'AMBIGUOUS_COMPLETION_ORDER')
            if stamp(completed) > stamp(previous['completed_at']):
                require(cycle.predecessor_cycle_id == previous['cycle_id'], 'NEWER_RUN_PREDECESSOR_NOT_IMPORTED')
                _, _, previous_summary, _ = load_archive(destination, previous['archive_id'])
                require(cycle.predecessor_snapshot_id == previous_summary['snapshot_id'], 'NEWER_RUN_PREDECESSOR_SNAPSHOT_MISMATCH')
        target = destination / archive_id
        if target.exists():
            require(any(r['archive_id'] == archive_id for r in pointer['runs']), 'UNINDEXED_ARCHIVE_REQUIRES_OPERATOR_REVIEW')
            old = next(r for r in pointer['runs'] if r['archive_id'] == archive_id)
            require(digest(target / 'catalog.json') == old['catalog_sha256'], 'EXISTING_ARCHIVE_CATALOG_CHANGED')
            for rel, info in read_json(target / 'catalog.json')['files'].items():
                require(digest(safe_path(target, rel)) == info['sha256'], 'EXISTING_ARCHIVE_CONTENT_CHANGED:' + rel)
            shutil.rmtree(stage)
        else:
            os.rename(stage, target)
            pointer['runs'].append({'archive_id': archive_id, 'catalog_sha256': catalog_hash,
                                   'cycle_id': cycle.cycle_id, 'completed_at': completed})
        latest = max(pointer['runs'], key=lambda row: (stamp(row['completed_at']), row['cycle_id']))
        pointer['latest'] = latest['archive_id']
        atomic(pointer_path, pointer)
        return {'archive_id': archive_id, 'latest': pointer['latest'], 'questions': 70, 'path': str(target)}
    finally:
        lock.unlink(missing_ok=True)
        # Failed staging directories remain unpublished for diagnostics.


def load_archive(root, archive_id=None):
    root = Path(root).resolve(); index = read_json(root / 'index.json')
    require(index.get('schema') == 'research_lab_archive_index_v1', 'ARCHIVE_INDEX_SCHEMA_INVALID')
    chosen = archive_id or index['latest']
    require(HEX.fullmatch(chosen or ''), 'INVALID_ARCHIVE_ID')
    descriptor = next((row for row in index['runs'] if row['archive_id'] == chosen), None)
    require(descriptor, 'UNINDEXED_ARCHIVE')
    directory = safe_path(root, chosen)
    require(digest(directory / 'catalog.json') == descriptor['catalog_sha256'], 'ARCHIVE_CATALOG_CHANGED')
    catalog = read_json(directory / 'catalog.json')
    summary_path = verified_file(directory, catalog, 'summary.json')
    summary = read_json(summary_path)
    require(summary['archive_id'] == chosen and len(summary['questions']) == 70, 'ARCHIVE_SUMMARY_INVALID')
    return directory, catalog, summary, index


def verified_file(directory, catalog, relative):
    info = catalog['files'].get(relative)
    require(info, 'UNINDEXED_ARTIFACT')
    path = safe_path(directory, relative)
    require(path.stat().st_size == info['bytes'] and digest(path) == info['sha256'], 'ARCHIVE_ARTIFACT_CHANGED:' + relative)
    return path
