import asyncio
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from TripWeave.intelligence.runtime import build_engine
from TripWeave.intelligence.session import parse_turn
from TripWeave.services.data import TravelStore


class ConversationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = build_engine()
        self.addCleanup(self.engine.transport.temp.cleanup)

    async def tickets(self):
        return await self.engine.run('查询北京到上海2026-10-01的火车票')

    async def test_date_city_inherit_and_missing_quantity(self):
        await self.engine.run('北京2026-10-01的天气')
        run = await self.engine.run('查同一天去上海的火车票')
        self.assertEqual(len(self.engine.session.candidates), 2)
        self.assertIn('北京到上海2026-10-01', run.plan.steps[0].query)
        run = await self.engine.run('订第二个')
        self.assertIsNone(run.pending_token)
        self.assertIn('数量', run.render())
        run = await self.engine.run('1张')
        q = run.results[-1].data['quote']
        self.assertEqual((q['ticket']['id'], q['quantity'], q['amount']), ('DEMO-TRAIN-002', 1, 650))
        self.assertEqual((await self.engine.confirm(run.pending_token)).status, 'success')

    async def test_missing_query_fields_followup(self):
        run = await self.engine.run('查火车票')
        self.assertIn('日期', run.message)
        run = await self.engine.run('北京到上海')
        self.assertIn('日期', run.message)
        run = await self.engine.run('2026-10-01')
        self.assertEqual(run.results[0].status, 'success')

    async def test_city_only_reply_fills_missing_destination(self):
        await self.engine.run('查2026-10-01从北京出发的火车票')
        run = await self.engine.run('上海')
        self.assertEqual(run.results[0].status, 'success')
        self.assertEqual(self.engine.session.slots['arrival_city'], '上海')

    async def test_ambiguous_and_out_of_range_selection(self):
        await self.tickets()
        for query in ('帮我预订刚才那张，1张', '订第九个，1张'):
            run = await self.engine.run(query)
            self.assertIsNone(run.pending_token)
            self.assertEqual(run.results[-1].status, 'input_required')

    async def test_multiple_queries_and_order_dependency(self):
        run = await self.engine.run('查询北京2026-10-01的天气和北京到上海2026-10-01的火车票，订第二个，1张')
        self.assertEqual([r.status for r in run.results], ['success', 'success', 'awaiting_confirmation'])
        self.assertEqual(run.plan.steps[-1].depends_on, ['tickets'])
        self.assertEqual(run.results[-1].data['quote']['ticket']['id'], 'DEMO-TRAIN-002')

    async def test_new_quantity_invalidates_old_quote(self):
        old = await self.engine.run('预订 DEMO-TRAIN-001，1张')
        new = await self.engine.run('改成2张')
        self.assertNotEqual(old.pending_token, new.pending_token)
        self.assertEqual((await self.engine.confirm(old.pending_token)).status, 'blocked')
        self.assertEqual(new.results[-1].data['quote']['quantity'], 2)

    async def test_new_search_clears_previous_candidates_and_selection(self):
        await self.tickets()
        await self.engine.run('订第二个')
        result = await self.engine.run('查询北京到上海2026-10-02的火车票')
        self.assertEqual(result.results[0].status, 'input_required')
        self.assertEqual(self.engine.session.candidates, [])
        result = await self.engine.run('订第二个，1张')
        self.assertIsNone(result.pending_token)

    async def test_candidate_expiry_requires_new_search(self):
        await self.tickets()
        self.engine.session.candidate_at -= 601
        result = await self.engine.run('订第二个，1张')
        self.assertIsNone(result.pending_token)
        self.assertIn('10分钟', result.render())

    async def test_explicit_ticket_precheck_and_no_early_order(self):
        result = await self.engine.run('预订 DEMO-TRAIN-001，2张')
        with self.engine.transport.store.connection() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM orders').fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT remaining FROM tickets WHERE id='DEMO-TRAIN-001'").fetchone()[0], 20)
        self.assertTrue(result.pending_token)

    async def test_invalid_quantity_and_unknown_ticket_no_confirmation(self):
        for query in ('预订 DEMO-TRAIN-001，0张', '预订 DEMO-TRAIN-001，6张', '预订 DEMO-TRAIN-999，1张'):
            result = await self.engine.run(query)
            self.assertIsNone(result.pending_token)

    async def test_cancel_and_new_topic_abort_pending_booking(self):
        run = await self.engine.run('预订 DEMO-TRAIN-001，1张')
        await self.engine.run('不订了')
        self.assertEqual((await self.engine.confirm(run.pending_token)).status, 'blocked')
        run = await self.engine.run('预订 DEMO-TRAIN-001，1张')
        await self.engine.run('北京2026-10-01天气')
        self.assertEqual((await self.engine.confirm(run.pending_token)).status, 'blocked')
        result = await self.engine.run('2张')
        self.assertIsNone(result.pending_token)

    async def test_token_expiry_double_confirm_and_isolation(self):
        other = build_engine()
        self.addCleanup(other.transport.temp.cleanup)
        run = await self.engine.run('预订 DEMO-TRAIN-001，1张')
        self.assertEqual((await other.confirm(run.pending_token)).status, 'blocked')
        self.engine.pending[run.pending_token].created_at -= 301
        self.assertEqual((await self.engine.confirm(run.pending_token)).status, 'blocked')
        run = await self.engine.run('预订 DEMO-TRAIN-001，1张')
        self.assertEqual((await self.engine.confirm(run.pending_token)).status, 'success')
        self.assertEqual((await self.engine.confirm(run.pending_token)).status, 'blocked')

    async def test_reset_removes_all_context(self):
        await self.tickets()
        self.engine.reset()
        self.assertEqual(self.engine.session.slots, {})
        self.assertEqual(self.engine.session.candidates, [])
        result = await self.engine.run('订第二个，1张')
        self.assertIsNone(result.pending_token)

    async def test_no_document_rag_capability(self):
        result = await self.engine.run('项目票务数据是真的吗')
        self.assertEqual(result.results, [])
        self.assertNotIn('knowledge', self.engine.registry.items)

    async def test_model_cannot_fabricate_booking_arguments(self):
        class BadModel:
            async def ask(self, *args):
                return {'intents': ['order'], 'ticket_id': 'DEMO-TRAIN-001', 'quantity': 1}
        self.engine.model = BadModel()
        result = await self.engine.run('帮我预订')
        self.assertIsNone(result.pending_token)
        self.assertEqual(result.results, [])

    async def test_failed_ticket_query_cannot_reuse_old_candidates(self):
        await self.tickets()
        async def fail(*args, **kwargs):
            raise ConnectionError('private-error')
        self.engine.transport.call = fail
        result = await self.tickets()
        self.assertEqual(result.results[0].status, 'failed')
        self.assertEqual(self.engine.session.candidates, [])
        self.assertNotIn('private-error', result.render())

    def test_ordinal_is_not_quantity_and_relative_date(self):
        from datetime import date
        from TripWeave.intelligence.session import parse_date
        self.assertIsNone(parse_turn('订第二张').quantity)
        self.assertEqual(parse_turn('订第12张，2张').quantity, 2)
        self.assertEqual(parse_date('明天', date(2026, 12, 31)), '2027-01-01')
        with self.assertRaises(ValueError):
            parse_date('2026-10-01天气，2026-10-02车票')


class QuoteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = TravelStore(Path(self.tmp.name) / 'travel.db')

    def quote(self, quantity=1):
        return self.store.prepare('DEMO-TRAIN-001', quantity)['quote']['quote_id']

    def test_same_quote_concurrent_different_requests_single_order(self):
        quote = self.quote(2)
        with ThreadPoolExecutor(max_workers=8) as pool:
            replies = list(pool.map(lambda n: self.store.book(quote, f'req-{n}'), range(24)))
        self.assertEqual(len({r['order_id'] for r in replies}), 1)
        self.assertEqual(sum(not r['replayed'] for r in replies), 1)
        self.assertEqual(self.store.tickets('train', '北京', '上海', '2026-10-01')['data'][0]['remaining'], 18)

    def test_inventory_changed_between_quote_and_confirm(self):
        quote = self.quote(2)
        with self.store.connection(write=True) as db:
            db.execute("UPDATE tickets SET remaining=1 WHERE id='DEMO-TRAIN-001'")
        self.assertEqual(self.store.book(quote, 'r')['status'], 'no_data')
        with self.store.connection() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM orders').fetchone()[0], 0)

    def test_concurrent_different_quotes_cannot_oversell(self):
        quotes = [self.quote(5) for _ in range(6)]
        with ThreadPoolExecutor(max_workers=6) as pool:
            replies = list(pool.map(lambda q: self.store.book(q, q), quotes))
        self.assertEqual(sum(r['status'] == 'success' for r in replies), 4)
        with self.store.connection() as db:
            self.assertEqual(db.execute("SELECT remaining FROM tickets WHERE id='DEMO-TRAIN-001'").fetchone()[0], 0)

    def test_price_change_requires_new_confirmation(self):
        quote = self.quote()
        with self.store.connection(write=True) as db:
            db.execute("UPDATE tickets SET price=501 WHERE id='DEMO-TRAIN-001'")
        self.assertIn('变化', self.store.book(quote, 'r')['message'])

    def test_expiry_and_unknown_quote(self):
        quote = self.quote()
        with self.store.connection(write=True) as db:
            db.execute('UPDATE booking_quotes SET expires_at=? WHERE id=?', (time.time()-1, quote))
        self.assertIn('过期', self.store.book(quote, 'r')['message'])
        self.assertEqual(self.store.book('missing', 'r')['status'], 'no_data')

    def test_request_id_conflict_and_replay(self):
        first, second = self.quote(), self.quote()
        reply = self.store.book(first, 'same')
        self.assertEqual(self.store.book(first, 'same')['order_id'], reply['order_id'])
        with self.assertRaises(ValueError):
            self.store.book(second, 'same')

    def test_write_failure_rolls_back_inventory_and_quote(self):
        quote = self.quote()
        with self.store.connection() as db:
            db.execute("CREATE TRIGGER fail_order BEFORE INSERT ON orders BEGIN SELECT RAISE(ABORT,'test'); END")
        with self.assertRaises(Exception):
            self.store.book(quote, 'fault')
        with self.store.connection() as db:
            self.assertEqual(db.execute("SELECT remaining FROM tickets WHERE id='DEMO-TRAIN-001'").fetchone()[0], 20)
            self.assertIsNone(db.execute('SELECT used_order_id FROM booking_quotes WHERE id=?',(quote,)).fetchone()[0])

    def test_parameterized_query_does_not_expand_scope(self):
        result = self.store.tickets('train', "北京' OR 1=1 --", '上海', '2026-10-01')
        self.assertEqual(result['status'], 'no_data')
