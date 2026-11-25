import unittest
from pc28.order_netting import preview


class NettingTests(unittest.TestCase):
    def rows(self, *items):
        return [{'id': i, 'message': d + str(a / 100), 'push_cents': a}
                for i, (d, a) in enumerate(items)]

    def test_full_offset(self):
        result = preview(self.rows(('单', 10000), ('双', 10000)))
        self.assertEqual(result['text'], '')
        self.assertTrue(result['fully_offset'])
        self.assertEqual(result['original_cents'], 20000)

    def test_partial_and_both_axes(self):
        rows = self.rows(('大', 10000), ('小', 6000), ('单', 8000), ('双', 3000))
        original = [dict(row) for row in rows]
        result = preview(rows)
        self.assertEqual(result['text'], '大40 单50')
        self.assertEqual(result['execution_cents'], 9000)
        self.assertEqual(rows, original)

    def test_preserve_other_markets_and_precision(self):
        result = preview(self.rows(('单', 101), ('双', 100), ('a大', 501), ('大单', 250)))
        self.assertEqual(result['text'], '单0.01 a大5.01 大单2.5')

    def test_invalid_snapshot_fails_closed(self):
        with self.assertRaises(ValueError):
            preview([{'id': 1, 'message': '单100', 'push_cents': 50}])

    def test_multiple_sources(self):
        result = preview(self.rows(('小', 3000), ('大', 2000), ('大', 6000)))
        self.assertEqual(result['text'], '大50')
        self.assertEqual(sum(o['offset_cents'] for o in result['orders']), 6000)
