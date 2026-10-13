"""Physical allocation is separate from per-role intent and optional cost caps."""
import copy
import json
import unittest

from mathagent.call_policy import CONTEXT_MARGIN, CallContextError, allocate_call, count_input


class Client:
    def count_input_tokens(self, payload):
        self.counted = copy.deepcopy(payload)
        return 1000


def request():
    return {'messages': [{'role': 'user', 'content': 'Exact hypotheses and source passages.'}],
            'tools': [{'type': 'function', 'function': {'name': 'read', 'parameters': {}}}],
            'format': {'type': 'object'}, 'options': {'num_ctx': 40960, 'num_predict': 4096}}


class AllocationTests(unittest.TestCase):
    def test_role_allowance_ignores_ordinary_predict_and_keeps_full_payload(self):
        payload = request()
        client = Client()
        fitted, allocation = allocate_call(client, payload, 32768)
        self.assertEqual(fitted['options']['num_predict'], 32768)
        self.assertEqual(payload['options']['num_predict'], 4096)
        self.assertEqual(client.counted['tools'], payload['tools'])
        self.assertEqual(client.counted['format'], payload['format'])
        self.assertEqual(fitted['messages'], payload['messages'])
        self.assertEqual(allocation['method'], 'server token count')
        self.assertEqual(allocation['context_source'], 'configured')
        self.assertIsNone(allocation['server_context_tokens'])
        json.dumps(allocation, allow_nan=False)

    def test_exact_context_fit_can_shrink_output_without_clipping_input(self):
        payload = request()
        payload['options']['num_ctx'] = 8192
        fitted, allocation = allocate_call(Client(), payload, 32768, minimum=1024)
        self.assertEqual(fitted['options']['num_predict'], 8192 - 1000 - CONTEXT_MARGIN)
        self.assertEqual(fitted['messages'], payload['messages'])
        self.assertEqual(allocation['requested_output_tokens'], 32768)

    def test_explicit_cap_is_enforced_and_required_floor_is_never_lowered(self):
        fitted, allocation = allocate_call(Client(), request(), 32768, explicit_cap=2048)
        self.assertEqual(fitted['options']['num_predict'], 2048)
        with self.assertRaises(CallContextError) as caught:
            allocate_call(Client(), request(), 32768, minimum=4096, explicit_cap=2048)
        self.assertEqual(caught.exception.details['output_tokens'], 2048)

    def test_fallback_counts_tools_and_schema_as_well_as_complete_messages(self):
        class NoCounter:
            pass
        payload = request()
        count, method = count_input(NoCounter(), payload)
        reduced = {key: value for key, value in payload.items() if key not in ('tools', 'format')}
        self.assertGreater(count, count_input(NoCounter(), reduced)[0])
        self.assertIn('byte estimate', method)

    def test_too_little_physical_room_has_actionable_metadata_and_no_clipping(self):
        payload = request()
        payload['options']['num_ctx'] = 1600
        with self.assertRaises(CallContextError) as caught:
            allocate_call(Client(), payload, 4096)
        self.assertEqual(caught.exception.details['available_output_tokens'], 88)
        self.assertEqual(payload['messages'], request()['messages'])

    def test_discovered_server_capacity_reconciles_configured_context(self):
        class SmallerServer(Client):
            def count_input_tokens(self, payload):
                self.assert_preflight = 'num_ctx' not in payload['options']
                self.observed_context_tokens = 8192
                return super().count_input_tokens(payload)
        client = SmallerServer()
        payload = request()
        fitted, allocation = allocate_call(client, payload, 32768)
        self.assertTrue(client.assert_preflight)
        self.assertEqual(fitted['options']['num_ctx'], 8192)
        self.assertEqual(fitted['options']['num_predict'], 6680)
        self.assertEqual(allocation['configured_context_tokens'], 40960)
        self.assertEqual(allocation['server_context_tokens'], 8192)
        self.assertEqual(allocation['context_source'], 'server_and_configured')
        self.assertEqual(payload['options']['num_ctx'], 40960)
