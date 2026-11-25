"""Build a deterministic execution preview without mutating source orders.

Amounts are integer cents. Callers must supply one account/bot/draw batch.
"""
import re

PATTERN = re.compile(r'([abc][大小]|[大小][单双]|[大小单双])(\d+(?:\.\d{1,2})?)')


def preview(rows):
    from decimal import Decimal
    orders = []
    for row in rows:
        match = PATTERN.fullmatch(row['message'].strip())
        if not match:
            raise ValueError('无法识别对冲订单，禁止发送')
        amount = int(Decimal(match[2]) * 100)
        if type(row['push_cents']) is not int or amount <= 0 or amount != row['push_cents']:
            raise ValueError('对冲订单金额与快照不一致')
        orders.append({'outbox_id': row['id'], 'direction': match[1],
                       'original_cents': amount, 'execution_cents': amount})
    for positive, negative in (('大', '小'), ('单', '双')):
        remaining = min(sum(o['original_cents'] for o in orders if o['direction'] == positive),
                        sum(o['original_cents'] for o in orders if o['direction'] == negative))
        for direction in (positive, negative):
            offset = remaining
            for order in orders:
                if order['direction'] != direction:
                    continue
                used = min(offset, order['execution_cents'])
                order['execution_cents'] -= used
                offset -= used
    totals = {}
    for order in orders:
        order['offset_cents'] = order['original_cents'] - order['execution_cents']
        if order['execution_cents']:
            direction = order['direction']
            totals[direction] = totals.get(direction, 0) + order['execution_cents']
    def text(direction, amount):
        return direction + format(Decimal(amount) / 100, '.2f').rstrip('0').rstrip('.')
    return {'orders': orders, 'text': ' '.join(text(d, a) for d, a in totals.items()),
            'original_cents': sum(o['original_cents'] for o in orders),
            'execution_cents': sum(totals.values()),
            'fully_offset': not totals}


def effective(c, row):
    """Read the immutable execution amount; historical batches keep old behavior."""
    import json
    batch = c.execute('SELECT b.request FROM robot_batches b JOIN robot_batch_items i ON i.batch_id=b.id WHERE i.outbox_id=?', (row['id'],)).fetchone()
    if batch:
        plan = json.loads(batch[0]).get('netting')
        if plan:
            item = next(o for o in plan['orders'] if o['outbox_id'] == row['id'])
            result = dict(row)
            result['original_message'] = row['message']
            result['original_cents'] = row['push_cents']
            result['push_cents'] = item['execution_cents']
            from decimal import Decimal
            result['message'] = item['direction'] + format(Decimal(item['execution_cents']) / 100, '.2f')
            return result
    return dict(row)
