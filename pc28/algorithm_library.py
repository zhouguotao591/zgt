"""Batch historical validation of the built-in formula library, without placing orders."""
from array import array
from bisect import bisect_left
from pathlib import Path
import argparse
import csv
import hashlib
import json


class FormulaHistory:
    """Prefix counts reproduce predict() while avoiding rescanning every lookback."""

    def __init__(self, rows):
        self.ids = array('q', (r['id'] for r in rows))
        self.axes = {}
        for axis, first in (('size', '大'), ('parity', '单')):
            bits = array('b', (int(r[axis] == first) for r in rows))
            counts = array('i', [0])
            transitions = [array('i', [0]) for _ in range(4)]
            for i, bit in enumerate(bits):
                counts.append(counts[-1] + bit)
                pair = bits[i - 1] * 2 + bit if i else -1
                for k, prefix in enumerate(transitions):
                    prefix.append(prefix[-1] + int(pair == k))
            self.axes[axis] = bits, counts, transitions

    def predictions(self, definition, start=0):
        ids = self.ids
        bits, prefix, transitions = self.axes[definition['axis']]
        lookback, kind = definition['lookback'], definition['kind']
        for i in range(start, len(ids)):
            left = i - lookback
            if left < 0 or ids[i - 1] - ids[left] != lookback - 1 or ids[i] != ids[i - 1] + 1:
                yield i, None
                continue
            last = bits[i - 1]
            if kind == 'follow':
                choice = last
            elif kind == 'reverse':
                choice = 1 - last
            else:
                if kind == 'transition':
                    p0, p1 = transitions[last * 2:last * 2 + 2]
                    zero, one = p0[i] - p0[left + 1], p1[i] - p1[left + 1]
                else:
                    one = prefix[i] - prefix[left]
                    zero = lookback - one
                choice = last if one == zero else int(one < zero) if kind == 'minority' else int(one > zero)
            yield i, choice

    def windows(self, definition, windows):
        from .algorithm_hall import summarize_hits
        if not self.ids:
            empty = summarize_hits([], 0)
            return {'all': empty, **{str(w): empty for w in windows}}
        last, first = self.ids[-1], self.ids[0]
        bits = self.axes[definition['axis']][0]
        start = bisect_left(self.ids, last - max(windows) + 1)
        hits = [(self.ids[i], None if choice is None else int(choice == bits[i]))
                for i, choice in self.predictions(definition, start)]
        result = {}
        for window in windows:
            selected = [(target, hit) for target, hit in hits if target > last - window]
            result[str(window)] = summarize_hits(selected, min(window, last - first + 1))
        result['all'] = result[str(max(windows))]
        return result


def full_backtest(prepared, rows, definition, odds, stake=10000):
    """Fixed 100U per valid prediction, no bankroll stops or increasing stakes."""
    from .model import settlement
    bits = prepared.axes[definition['axis']][0]
    labels = ('小', '大') if definition['axis'] == 'size' else ('双', '单')
    paid = {(choice, total): settlement(stake, labels[choice], total, True, odds)
            for choice in (0, 1) for total in range(28)}
    count = wins = refunds = net = peak = drawdown = max_hit = max_miss = run_hit = run_miss = 0
    previous = None
    for i, choice in prepared.predictions(definition):
        target = prepared.ids[i]
        if previous is not None and target != previous + 1:
            run_hit = run_miss = 0
        previous = target
        if choice is None:
            run_hit = run_miss = 0
            continue
        hit = choice == bits[i]
        count += 1
        wins += hit
        if hit:
            outcome = paid[choice, rows[i]['total']]
            pnl = outcome['pnl']
            refunds += outcome['outcome'] == 'refund'
            run_hit += 1
            run_miss = 0
        else:
            pnl = -stake
            run_miss += 1
            run_hit = 0
        max_hit = max(max_hit, run_hit)
        max_miss = max(max_miss, run_miss)
        net += pnl
        peak = max(peak, net)
        drawdown = max(drawdown, peak - net)
    return {'count': count, 'wins': wins, 'refunds': refunds, 'win_rate': wins / count if count else None,
            'net_cents': net, 'investment_cents': count * stake, 'stake_cents': stake,
            'roi': net / (count * stake) if count else None, 'max_drawdown_cents': drawdown,
            'max_hit': max_hit, 'max_miss': max_miss,
            'observed': len(rows), 'missing_periods': prepared.ids[-1] - prepared.ids[0] + 1 - len(rows) if rows else 0,
            'skipped': len(rows) - count, 'first_id': prepared.ids[0] if rows else None,
            'last_id': prepared.ids[-1] if rows else None,
            'start': rows[0]['draw_utc'] if rows else None, 'end': rows[-1]['draw_utc'] if rows else None,
            'method': 'fixed-100U-prior-verified-v1', 'version': definition['version']}


def run(root):
    from .algorithm_hall import AlgorithmHall, DEFINITIONS
    from .config import load
    from .db import DB
    from .model import utcnow
    root = Path(root)
    cfg = load(root)
    db = DB(root / cfg['database'])
    AlgorithmHall(db)  # Ensure the durable result table exists.
    rows = db.query("""SELECT d.id,d.draw_utc,d.size,d.parity,d.total FROM draws d
        WHERE d.verification IN ('full_keno','crosschecked_abc') AND d.draw_utc IS NOT NULL
        AND julianday(d.draw_utc)<=julianday('now')
        AND NOT EXISTS(SELECT 1 FROM conflicts c WHERE c.draw_id=d.id AND c.resolved=0)
        ORDER BY d.id""")
    if not rows:
        raise ValueError('没有已核验历史开奖可供回测')
    prepared = FormulaHistory(rows)
    digest = hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()
    output = root / 'data/exports/algorithm-library'
    output.mkdir(parents=True, exist_ok=True)
    results = []
    created = utcnow()
    for i, definition in enumerate(DEFINITIONS, 1):
        result = full_backtest(prepared, rows, definition, cfg['odds'])
        result.update(id=definition['id'], name=definition['name'], created=created,
                      data_sha256=digest, odds=cfg['odds'])
        results.append(result)
        with db.tx() as c:
            c.execute('INSERT OR REPLACE INTO algorithm_library_backtests VALUES(?,?,?)',
                      (definition['id'], created, json.dumps(result, ensure_ascii=False)))
        progress = {'completed': i, 'total': len(DEFINITIONS), 'algorithm': definition['id'],
                    'cutoff': rows[-1]['id'], 'created': created}
        (output / 'progress.json').write_text(json.dumps(progress, ensure_ascii=False), encoding='utf-8')
        if i % 10 == 0 or i == len(DEFINITIONS):
            print(json.dumps(progress, ensure_ascii=False), flush=True)
    (output / 'results.json').write_text(json.dumps(results, ensure_ascii=False), encoding='utf-8')
    fields = ['id', 'name', 'first_id', 'last_id', 'observed', 'count', 'wins', 'win_rate',
              'net_cents', 'investment_cents', 'roi', 'max_drawdown_cents', 'skipped', 'method']
    with (output / 'results.csv').open('w', encoding='utf-8-sig', newline='') as file:
        writer = csv.DictWriter(file, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(results)
    return results


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path.cwd())
    run(parser.parse_args().root)
