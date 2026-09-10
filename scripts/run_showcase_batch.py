"""Exactly bounded production cohort; no write retries or auto publication.

Credentials are read from the terminal into memory, never environment/files.
Restart requires a new output directory or explicit journal reconciliation.
"""
import argparse
import getpass
import json
import time
from pathlib import Path

from production_showcase import Showcase, redact
from run_live_generation_e2e import login


def run():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    parser.add_argument('--count', type=int, default=15)
    parser.add_argument('--concurrency', type=int, choices=[1, 2], default=2)
    parser.add_argument('--case-ids', nargs='+', help='Explicit distinct manifest cases; length must equal --count')
    args = parser.parse_args()
    if not 1 <= args.count <= 15:
        raise ValueError('This cohort is limited to 15 first-generation attempts')
    output = Path(args.output)
    if (output / 'events.jsonl').exists():
        raise ValueError('Existing journal: reconcile accepted tasks; do not resubmit')
    account = input('Account: ').strip()
    password = getpass.getpass('Password: ')
    client = Showcase('https://www.zlspace.ai', login('https://www.zlspace.ai', account, password), output)
    del password
    indices = list(range(args.count))
    if args.case_ids:
        if len(args.case_ids) != args.count or len(set(args.case_ids)) != args.count:
            raise ValueError('Explicit cases must be distinct and match --count; no tasks submitted')
        case_index = {case['id']: index for index, case in enumerate(client.manifest['cases'])}
        if any(case_id not in case_index for case_id in args.case_ids):
            raise ValueError('Unknown case ID; no tasks submitted')
        indices = [case_index[case_id] for case_id in args.case_ids]
    quota = client.get('users/quota')
    sub = quota.get('subscription') or {}
    remaining = int(quota.get('freeQuota') or 0) + (max(0, int(sub.get('quotaThisPeriod') or 0) - int(sub.get('usedThisPeriod') or 0)) if sub.get('active') else 0)
    print(json.dumps({'event': 'ready', 'allowance': remaining, 'count': args.count, 'concurrency': args.concurrency}), flush=True)
    if remaining < args.count:
        raise ValueError('Insufficient allowance for this cohort; no quota changes made')
    active, results = {}, []
    next_index = 0
    stopped = False
    while active or (next_index < args.count and not stopped):
        while len(active) < args.concurrency and next_index < args.count and not stopped:
            index = indices[next_index]
            next_index += 1
            try:
                submitted = client.submit(index)
                game_id = submitted['response']['gameId']
                active[game_id] = {'index': index, 'started': time.monotonic(), 'last': None, 'errors': 0}
                print(json.dumps({'event': 'submitted', 'index': index, 'title': client.manifest['cases'][index]['title'], 'gameId': game_id, 'taskId': submitted['response'].get('taskId')}, ensure_ascii=False), flush=True)
            except Exception as exc:
                # A timed-out POST may have been accepted. Stop additional writes
                # and reconcile, never repeat or conceal the ambiguous attempt.
                stopped = True
                print(json.dumps({'event': 'submission_blocked', 'index': index, 'error': redact(str(exc))}, ensure_ascii=False), flush=True)
        for game_id, entry in list(active.items()):
            try:
                status = client.get(f'games/{game_id}/generation-status')
                entry['errors'] = 0
            except Exception as exc:
                entry['errors'] += 1
                print(json.dumps({'event': 'status_read_error', 'gameId': game_id, 'error': redact(str(exc))}, ensure_ascii=False), flush=True)
                if entry['errors'] >= 5:
                    stopped = True
                if time.monotonic() - entry['started'] > 2100:
                    raise RuntimeError('Status unavailable beyond task budget: reconcile journal before continuing')
                continue
            state = status.get('status')
            marker = (state, status.get('progressStage') or status.get('stage'), status.get('progressPct'))
            if marker != entry['last']:
                entry['last'] = marker
                client.record('status', work_id=game_id, data=status)
                print(json.dumps({'event': 'progress', 'index': entry['index'], 'gameId': game_id, 'status': state, 'stage': marker[1], 'elapsed_s': round(time.monotonic()-entry['started'], 1)}, ensure_ascii=False), flush=True)
            if state in {'succeeded', 'failed', 'canceled', 'timed_out'}:
                row = {'index': entry['index'], 'case_id': client.manifest['cases'][entry['index']]['id'], 'title': client.manifest['cases'][entry['index']]['title'], 'gameId': game_id, 'taskId': status.get('taskId'), 'status': state, 'elapsed_s': round(time.monotonic()-entry['started'], 1), 'errorMessage': status.get('errorMessage'), 'showcase_acceptance': 'pending'}
                try:
                    client.evidence(game_id)
                    if state == 'succeeded':
                        row['html'] = client.cache_playable(game_id)
                except Exception as exc:
                    row['evidence_error'] = redact(str(exc))
                results.append(redact(row))
                client.record('terminal', **row)
                (output / 'summary.json').write_text(json.dumps({'attempted': next_index, 'completed': results, 'active': list(active)}, ensure_ascii=False, indent=2))
                print(json.dumps({'event': 'terminal', **redact(row)}, ensure_ascii=False), flush=True)
                del active[game_id]
            elif time.monotonic() - entry['started'] > 2100:
                raise RuntimeError('Accepted task exceeded observation budget: reconcile without resubmitting')
        if active:
            time.sleep(15)
    summary = {'attempted': next_index, 'completed': sorted(results, key=lambda row: row['index']), 'stopped': stopped, 'remainingQuota': client.get('users/quota')}
    (output / 'summary.json').write_text(json.dumps(redact(summary), ensure_ascii=False, indent=2))
    print(json.dumps({'event': 'cohort_finished', 'attempted': next_index, 'completed': len(results), 'succeeded': sum(row['status'] == 'succeeded' for row in results), 'stopped': stopped}), flush=True)


if __name__ == '__main__':
    run()
