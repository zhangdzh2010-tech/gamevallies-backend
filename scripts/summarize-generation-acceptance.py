#!/usr/bin/env python3
"""Summarize real acceptance JSONL; never treat mocks or pending tasks as successes."""
import argparse
import json
import math
from collections import defaultdict


def lower_bound(successes, total, alpha=0.05):
    """One-sided exact binomial lower bound; samples must be independent/representative."""
    if not successes or not total:
        return 0.0
    def tail(p):
        if p <= 0: return 0.0
        if p >= 1: return 1.0
        return sum(math.exp(math.lgamma(total+1)-math.lgamma(k+1)-math.lgamma(total-k+1)
                            +k*math.log(p)+(total-k)*math.log1p(-p))
                   for k in range(successes, total+1))
    low, high = 0.0, 1.0
    for _ in range(60):
        mid = (low+high)/2
        if tail(mid) < alpha: low = mid
        else: high = mid
    return (low+high)/2


def summarize(rows):
    groups = defaultdict(list)
    seen = set()
    for row in rows:
        if row['run_id'] in seen: raise ValueError('Duplicate run_id')
        seen.add(row['run_id'])
        if row['kind'] not in ('game','tool','science'): raise ValueError('Invalid kind')
        if row['status'] not in ('passed','failed','pending'): raise ValueError('Invalid status')
        if row['environment'] != 'production' or not row['evidence_url']: raise ValueError('Real production evidence required')
        if type(row['attempts']) is not int or row['attempts'] < 1: raise ValueError('Invalid attempts')
        for kind in (row['kind'], 'all'):
            groups[(row['version'],kind)].append(row)
    result = []
    for (version,kind), items in sorted(groups.items()):
        done = [r for r in items if r['status'] != 'pending']
        successes = sum(r['status'] == 'passed' for r in done)
        pending = len(items)-len(done)
        bound = lower_bound(successes,len(done))
        result.append(dict(version=version,kind=kind,submitted=len(items),completed=len(done),passed=successes,
            failed=len(done)-successes,pending=pending,first_attempt_passed=sum(r['status']=='passed' and r['attempts']==1 for r in done),
            observed_success_rate=successes/len(done) if done else None,one_sided_95_lower_bound=bound,
            target_99_supported=pending==0 and bound>=0.99))
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('ledger', help='JSONL real generation acceptance records')
    args = parser.parse_args()
    with open(args.ledger) as stream:
        print(json.dumps(summarize([json.loads(line) for line in stream if line.strip()]),ensure_ascii=False,indent=2))
