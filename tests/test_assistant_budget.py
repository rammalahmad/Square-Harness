import math
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor

from mathagent.assistant_budget import AssistantBudget, BudgetClient
from mathagent.gui.store import assistant_view


class Client:
    timeout = 10

    def __init__(self, used=12):
        self.used = used

    def count_input_tokens(self, payload):
        return 20

    def stream(self, payload):
        yield {'done': True, 'eval_count': self.used, 'prompt_eval_count': 20}


def payload(cap=40):
    return {'messages': [{'role': 'user', 'content': 'Exact statement.'}],
            'options': {'num_predict': cap, 'num_ctx': 2048}}


class AssistantBudgetTests(unittest.TestCase):
    def test_default_parent_has_no_cumulative_token_caps_but_workers_stay_bounded(self):
        budget = BudgetClient(Client())
        self.assertIsNone(budget.snapshot()['max_tokens'])
        self.assertIsNone(budget.snapshot()['max_input_tokens'])
        self.assertTrue(math.isinf(budget.remaining_tokens))
        budget.pool.state['tokens'] = 60000
        budget.pool.state['input_tokens'] = 240000
        list(budget.stream(payload()))
        self.assertEqual(budget.snapshot()['tokens'], 60012)
        self.assertEqual(budget.snapshot()['input_tokens'], 240020)
        child = budget.fork(Client(), allowance=40, usage={'tokens': 0, 'input_tokens': 0})
        list(child.stream(payload()))
        with self.assertRaises(AssistantBudget):
            list(child.stream(payload()))
        resumed = BudgetClient(Client(), state=budget.snapshot())
        self.assertTrue(math.isinf(resumed.remaining_tokens))

    def test_visible_usage_is_monotonic_and_inflight_reservations_are_separate(self):
        views = []
        budget = BudgetClient(Client(), checkpoint=lambda: views.append(assistant_view({'assistant_state': {'budget': budget.snapshot()}})['assistant_budget']))
        list(budget.stream(payload()))
        list(budget.stream(payload()))
        self.assertEqual([view['tokens']['used'] for view in views], [0, 12, 12, 12, 24, 24])
        self.assertEqual([view['tokens']['reserved'] for view in views], [40, 0, 0, 40, 0, 0])
        self.assertEqual(views[0]['input_tokens'], {'used': 20, 'limit': None})
        self.assertIsNone(views[0]['tokens']['limit'])

    def test_legacy_numeric_caps_and_unlimited_defaults_survive_resume(self):
        legacy = {'tokens': 42, 'input_tokens': 100, 'seconds': 1,
                  'max_tokens': 60000, 'max_input_tokens': 240000, 'max_seconds': 900}
        resumed = BudgetClient(Client(), state=legacy)
        self.assertEqual(resumed.remaining_tokens, 59958)
        self.assertEqual(assistant_view({'assistant_state': {'budget': legacy}})['assistant_budget']['tokens'],
                         {'used': 42, 'limit': 60000, 'reserved': 0})

    def test_terminal_controller_failure_is_not_presented_as_resumable(self):
        self.assertFalse(assistant_view({'assistant_state': {'status': 'failed'}})['assistant_resumable'])
        for status in ('interrupted', 'incomplete', 'running'):
            self.assertTrue(assistant_view({'assistant_state': {'status': status}})['assistant_resumable'])

    def test_interrupted_and_orphaned_reservations_remain_charged(self):
        class Interrupted(Client):
            def stream(self, payload):
                yield {'message': {'content': 'partial'}}
                raise KeyboardInterrupt
        budget = BudgetClient(Interrupted(), max_tokens=100)
        stream = budget.stream(payload())
        next(stream)
        active = budget.snapshot()
        self.assertEqual((active['tokens'], active['reserved_tokens']), (40, 40))
        # Restoring a checkpoint cannot retain an active reservation indefinitely.
        restored = BudgetClient(Client(), state=active)
        self.assertEqual((restored.snapshot()['tokens'], restored.snapshot()['reserved_tokens']), (40, 0))
        self.assertEqual(restored.remaining_tokens, 60)
        with self.assertRaises(KeyboardInterrupt):
            next(stream)
        self.assertEqual((budget.snapshot()['tokens'], budget.snapshot()['reserved_tokens']), (40, 0))

    def test_explicit_input_cap_still_blocks_dispatch(self):
        budget = BudgetClient(Client(), max_input_tokens=30)
        list(budget.stream(payload()))
        with self.assertRaisesRegex(AssistantBudget, 'input budget exhausted'):
            list(budget.stream(payload()))
        self.assertEqual(budget.snapshot()['input_tokens'], 20)

    def test_legacy_adaptive_input_ceiling_can_fit_next_complete_request(self):
        saved = {'tokens': 70, 'input_tokens': 90, 'seconds': 1,
                 'max_tokens': 100, 'max_input_tokens': 100, 'max_seconds': 900,
                 'adaptive_limits': {'max_tokens': 200, 'max_input_tokens': 150}}
        budget = BudgetClient(Client(), state=saved)
        list(budget.stream(payload()))
        self.assertEqual((budget.snapshot()['max_tokens'], budget.snapshot()['max_input_tokens']), (110, 110))
        self.assertEqual((budget.snapshot()['tokens'], budget.snapshot()['input_tokens']), (82, 110))
        budget.pool.state['input_tokens'] = 140
        with self.assertRaisesRegex(AssistantBudget, 'input budget exhausted'):
            list(budget.stream(payload()))
        self.assertEqual(budget.snapshot()['max_input_tokens'], 150)
        self.assertEqual(budget.snapshot()['input_tokens'], 140)

    def test_adaptive_promotion_preserves_spending_and_saved_caps_on_resume(self):
        budget = BudgetClient(Client(), max_tokens=100, max_input_tokens=100, max_seconds=10,
                              adaptive_limits={'max_tokens': 200, 'max_seconds': 20})
        list(budget.stream(payload()))
        budget.ensure_limits(max_tokens=300, max_input_tokens=300, max_seconds=30)
        self.assertEqual((budget.snapshot()['tokens'], budget.snapshot()['input_tokens']), (12, 20))
        self.assertEqual((budget.snapshot()['max_tokens'], budget.snapshot()['max_input_tokens'], budget.snapshot()['max_seconds']),
                         (200, 100, 20))
        resumed = BudgetClient(Client(), state=budget.snapshot(), adaptive_limits={'max_tokens': 1000})
        resumed.ensure_limits(max_tokens=1000)
        self.assertEqual(resumed.snapshot()['max_tokens'], 200)
        self.assertEqual(resumed.remaining_tokens, 188)

    def test_legacy_saved_budget_cannot_be_promoted_on_resume(self):
        old = BudgetClient(Client(), max_tokens=100).snapshot()
        resumed = BudgetClient(Client(), state=old, adaptive_limits={'max_tokens': 1000})
        resumed.ensure_limits(max_tokens=1000)
        self.assertEqual(resumed.snapshot()['max_tokens'], 100)

    def test_completed_calls_charge_measured_usage_across_independent_clients(self):
        budget = BudgetClient(Client(), max_tokens=100)
        child = budget.fork(Client(15), allowance=40, usage={'tokens': 0, 'input_tokens': 0})
        list(budget.stream(payload()))
        list(child.stream(payload()))
        self.assertEqual(budget.snapshot()['tokens'], 27)
        self.assertEqual(child.usage['tokens'], 15)
        self.assertEqual(budget.snapshot()['input_tokens'], 40)

    def test_interrupted_call_retains_reservation_on_resume(self):
        class Interrupted(Client):
            def stream(self, payload):
                yield {'message': {'content': 'partial'}}
                raise KeyboardInterrupt
        budget = BudgetClient(Interrupted(), max_tokens=100)
        with self.assertRaises(KeyboardInterrupt):
            list(budget.stream(payload()))
        resumed = BudgetClient(Client(), state=budget.snapshot())
        self.assertEqual(resumed.remaining_tokens, 60)

    def test_cancelled_before_dispatch_does_not_charge_or_call_client(self):
        class Cancelled(Client):
            cancel = threading.Event()

            def stream(self, payload):
                raise AssertionError('Cancelled inference must not be dispatched')
        client = Cancelled()
        client.cancel.set()
        budget = BudgetClient(client, max_tokens=100)
        with self.assertRaises(KeyboardInterrupt):
            list(budget.stream(payload()))
        self.assertEqual(budget.snapshot()['tokens'], 0)
        self.assertEqual(budget.snapshot()['input_tokens'], 0)

    def test_unreported_usage_retains_reservation(self):
        class Unknown(Client):
            def stream(self, payload):
                yield {'done': True}
        budget = BudgetClient(Unknown(), max_tokens=100)
        list(budget.stream(payload()))
        self.assertEqual(budget.snapshot()['tokens'], 40)
        self.assertEqual(budget.snapshot()['reserved_tokens'], 0)

    def test_dispatch_exception_keeps_charge_and_releases_active_reservation(self):
        class Failed(Client):
            def stream(self, payload):
                raise RuntimeError('Connection failed after dispatch')
        client = Failed()
        budget = BudgetClient(client, max_tokens=100)
        with self.assertRaisesRegex(RuntimeError, 'after dispatch'):
            list(budget.stream(payload()))
        self.assertEqual((budget.snapshot()['tokens'], budget.snapshot()['reserved_tokens']), (40, 0))
        self.assertEqual(client.timeout, 10)

    def test_local_allowance_cannot_borrow_another_workers_reservation(self):
        budget = BudgetClient(Client(), max_tokens=100)
        child = budget.fork(Client(), allowance=20, usage={'tokens': 0, 'input_tokens': 0})
        with self.assertRaises(AssistantBudget):
            list(child.stream(payload()))
        self.assertEqual(budget.snapshot()['tokens'], 0)

    def test_concurrent_reservations_cannot_overspend(self):
        entered, release = threading.Event(), threading.Event()
        class Waiting(Client):
            def stream(self, payload):
                entered.set()
                release.wait(2)
                yield {'done': True, 'eval_count': 40}
        budget = BudgetClient(Client(), max_tokens=60)
        first = budget.fork(Waiting(), allowance=60, usage={'tokens': 0, 'input_tokens': 0})
        second = budget.fork(Client(), allowance=60, usage={'tokens': 0, 'input_tokens': 0})
        with ThreadPoolExecutor(max_workers=2) as pool:
            future = pool.submit(lambda: list(first.stream(payload())))
            self.assertTrue(entered.wait(2))
            with self.assertRaises(AssistantBudget):
                list(second.stream(payload()))
            view = assistant_view({'assistant_state': {'budget': budget.snapshot()}})['assistant_budget']['tokens']
            self.assertEqual(view, {'used': 0, 'limit': 60, 'reserved': 40})
            release.set()
            future.result()
        self.assertEqual(budget.snapshot()['tokens'], 40)

    def test_bad_server_usage_is_not_refunded(self):
        budget = BudgetClient(Client(41), max_tokens=100)
        with self.assertRaises(AssistantBudget):
            list(budget.stream(payload()))
        self.assertEqual(budget.snapshot()['tokens'], 40)

    def test_repeated_completion_event_cannot_double_refund(self):
        class Duplicate(Client):
            def stream(self, payload):
                yield {'done': True, 'eval_count': 12}
                yield {'done': True, 'eval_count': 12}
        budget = BudgetClient(Duplicate(), max_tokens=100)
        with self.assertRaises(AssistantBudget):
            list(budget.stream(payload()))
        self.assertEqual(budget.snapshot()['tokens'], 12)
