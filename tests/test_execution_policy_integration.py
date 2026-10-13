"""Uncapped orchestration still stops repetition and preserves source identities."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from mathagent.agent import Agent
from mathagent.orchestrator import Orchestrator
from mathagent.tools import Workspace
from tests.test_orchestrator import Client, action, call, calls, final


class ExecutionPolicyIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.workspace = Workspace(self.root)

    def runner(self, replies, worker=lambda *args: {'status': 'complete', 'answer': 'A finding.'}):
        client = Client(replies)
        runner = Orchestrator(Agent(client, self.workspace, ctx=131072), worker)
        return runner, client

    def test_distinct_source_progress_is_not_cut_off_at_twelve_actions(self):
        (self.root / 'paper.md').write_text(''.join(f'Exact independent fact {i}.\n' for i in range(14)))
        replies = [calls(call('read_file', {'path': 'paper.md', 'start_line': i, 'end_line': i}, f'read_{i}'))
                   for i in range(1, 15)] + [final('The fourteen supplied facts have been read.')]
        runner, client = self.runner(replies)
        result = runner.run('Read the distinct source facts.', files=['paper.md'])
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(result['actions'], 14)
        self.assertIsNone(runner.state['limits']['actions'])
        self.assertIsNone(runner.state['limits']['children'])
        self.assertEqual(len(client.requests), 15)

    def test_duplicate_reads_are_reused_then_force_a_supported_final(self):
        (self.root / 'paper.md').write_text('Exact fact under hypothesis H.\n')
        replies = [calls(call('read_file', {'path': 'paper.md', 'start_line': 1, 'end_line': 1}, f'read_{i}'))
                   for i in range(4)] + [final('The fact holds under H.')]
        runner, client = self.runner(replies)
        with patch.object(self.workspace, 'execute', wraps=self.workspace.execute) as execute:
            result = runner.run('Read the fact.', files=['paper.md'])
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(execute.call_count, 1)
        self.assertNotIn('tools', client.requests[-1])

    def test_changed_source_hash_invalidates_the_read_cache(self):
        paper = self.root / 'paper.md'
        paper.write_text('Original hypothesis H.\n')
        replies = [calls(call('read_file', {'path': 'paper.md', 'start_line': 1, 'end_line': 1}, f'read_{i}'))
                   for i in range(2)] + [final('The current source uses K.')]
        runner, client = self.runner(replies)
        original_stream = client.stream
        def stream(payload):
            if len(client.requests) == 1:
                paper.write_text('Revised hypothesis K.\n')
            yield from original_stream(payload)
        client.stream = stream
        with patch.object(self.workspace, 'execute', wraps=self.workspace.execute) as execute:
            result = runner.run('Read the current source.', files=['paper.md'])
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(execute.call_count, 2)
        self.assertIn('Revised hypothesis K', client.requests[-1]['messages'][-1]['content'])

    def test_operational_worker_failures_do_not_become_progress_through_new_ids(self):
        replies = [calls(call(args=action(request=f'Investigate the unchanged obligation, phrasing {i}.'), call_id=f'worker_{i}'))
                   for i in range(3)] + [final('The model backend failed; no findings were obtained.')]
        def failed_worker(*args):
            return {'status': 'failed', 'answer': '', 'error': {
                'code': 'worker_operation_failed', 'message': 'Backend temporarily unavailable.',
                'retryable': True, 'scope': 'operation'}}
        runner, client = self.runner(replies, failed_worker)
        result = runner.run('Investigate this obligation.')
        self.assertEqual(result['status'], 'complete')
        self.assertNotIn('tools', client.requests[-1])
        self.assertEqual(runner.state['stagnant_actions'], 3)

    def test_distinct_local_tool_errors_do_not_become_findings(self):
        replies = [calls(call('read_file', {'path': f'missing_{i}.md', 'start_line': 1, 'end_line': 1}, f'bad_{i}'))
                   for i in range(3)] + [final('Those source files are unavailable.')]
        runner, client = self.runner(replies)
        result = runner.run('Read the needed source.')
        self.assertEqual(result['status'], 'complete')
        self.assertNotIn('tools', client.requests[-1])
        self.assertEqual(runner.state['stagnant_actions'], 3)

    def test_workspace_search_sees_changes_in_an_unattached_allowed_source(self):
        (self.root / 'pinned.md').write_text('Pinned unchanged source.\n')
        discovered = self.root / 'notes.md'
        discovered.write_text('Original hypothesis H.\n')
        replies = [calls(call('search_text', {'pattern': 'hypothesis'}, f'search_{i}'))
                   for i in range(2)] + [final('The current hypothesis is K.')]
        runner, client = self.runner(replies)
        original_stream = client.stream
        def stream(payload):
            if len(client.requests) == 1:
                discovered.write_text('Revised hypothesis K.\n')
            yield from original_stream(payload)
        client.stream = stream
        result = runner.run('Find the current hypothesis.', files=['pinned.md'])
        self.assertEqual(result['status'], 'complete')
        self.assertIn('Revised hypothesis K', client.requests[-1]['messages'][-1]['content'])

    def test_identical_worker_findings_do_not_become_progress_through_new_ids(self):
        replies = [calls(call(args=action(request=f'Check equivalent presentation {i}.'), call_id=f'worker_{i}'))
                   for i in range(4)] + [final('The unchanged finding is A under H.')]
        runner, client = self.runner(replies)
        result = runner.run('Check this obligation.')
        self.assertEqual(result['status'], 'complete')
        self.assertNotIn('tools', client.requests[-1])
        self.assertEqual(runner.state['stagnant_actions'], 3)

    def test_final_recovery_can_grow_beyond_a_legacy_role_ceiling_when_physical_room_exists(self):
        runner, client = self.runner([
            final('Unfinished explanation.', reason='length', eval_count=8192),
            final('Still unfinished explanation.', reason='length', eval_count=16384),
            final('Complete explanation.', eval_count=20000)])
        runner.agent.ctx = 40960
        client.count_input_tokens = lambda payload: 1000
        query = 'Give the requested complete explanation.'
        first = runner.run(query)
        self.assertEqual(first['status'], 'incomplete')
        second = runner.run(query, state=runner.state)
        self.assertEqual(second['status'], 'incomplete')
        third = runner.run(query, state=runner.state)
        self.assertEqual(third['status'], 'complete')
        self.assertEqual([p['options']['num_predict'] for p in client.requests], [8192, 16384, 32768])
