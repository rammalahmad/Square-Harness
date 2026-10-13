import unittest
from unittest.mock import patch

from mathagent.gui.store import _live_context, _output_budget, _working_time, assistant_view


class UsageProjectionTests(unittest.TestCase):
    def test_live_reservation_is_not_displayed_as_generated_output(self):
        state = {'tokens_charged': 8904, 'settings': {'max_tokens': None},
                 'calls': [{'status': 'running', 'reserved_tokens': 8192}]}
        self.assertEqual(_output_budget(state, True), {'used': 712, 'reserved': 8192, 'limit': None})
        state.update(tokens_charged=900, calls=[{'status': 'complete', 'reserved_tokens': 8192, 'charged_tokens': 188}])
        self.assertEqual(_output_budget(state, True), {'used': 900, 'reserved': 0, 'limit': None})

    def test_interrupted_unknown_usage_stays_charged_without_a_live_reservation(self):
        state = {'tokens_charged': 8904, 'settings': {'max_tokens': 60000},
                 'calls': [{'status': 'running', 'reserved_tokens': 8192}]}
        self.assertEqual(_output_budget(state, False), {'used': 8904, 'reserved': 0, 'limit': 60000})

    def test_current_context_is_only_projected_from_recorded_call_measurements(self):
        self.assertEqual(_live_context(None), {})
        self.assertEqual(_live_context({'context': {'input_tokens': 800}}), {})
        self.assertEqual(_live_context({'allocation': {'input_tokens': 800, 'context_tokens': 40960}}),
                         {'context': {'used': 800, 'limit': 40960}})

    def test_working_time_advances_between_active_checkpoints_but_paused_time_does_not(self):
        state = {'seconds_used': 10, 'active_checkpoint_wall': 100, 'settings': {'max_seconds': 900}}
        with patch('mathagent.gui.store.time.time', return_value=125):
            self.assertEqual(_working_time(state, True), {'used': 35, 'limit': 900})
            self.assertEqual(_working_time(state, False), {'used': 10, 'limit': 900})

    def test_assistant_forwards_latest_saved_child_progress_and_actual_live_context(self):
        progress = {'phase': 'read', 'completed': 3, 'total': 8, 'unit': 'reading parts', 'scope': 'Main results', 'partial': True}
        state = {'status': 'running', 'children': [{'result': {'progress': {'completed': 1}}}, {'result': {'progress': progress}}],
                 'last_call': {'status': 'running', 'allocation': {'input_tokens': 8609, 'context_tokens': 40960}}}
        view = assistant_view({'assistant_state': state})
        self.assertEqual(view['assistant_progress'], progress)
        self.assertEqual(view['assistant_budget']['context'], {'used': 8609, 'limit': 40960})
        state['status'] = 'paused'
        self.assertNotIn('context', assistant_view({'assistant_state': state})['assistant_budget'])

    def test_parent_progress_takes_precedence_without_inventing_context(self):
        progress = {'phase': 'plan', 'completed': 0, 'total': 2, 'unit': 'tasks', 'scope': 'Parent scope', 'partial': False}
        view = assistant_view({'assistant_state': {'status': 'running', 'progress': progress, 'children': []}})
        self.assertEqual(view['assistant_progress'], progress)
        self.assertNotIn('context', view['assistant_budget'])


if __name__ == '__main__':
    unittest.main()
