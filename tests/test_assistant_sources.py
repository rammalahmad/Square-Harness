"""Document routing, source-grounded final recovery and terminal failure behavior."""
import copy
import json
import unittest

from mathagent.agent import Agent
from mathagent.mode_calls import ModeOrchestrator
from mathagent.orchestrator import Orchestrator
from mathagent.gui import store
from tests.test_gui import GuiCase, text
from tests import test_orchestrator as controller_tests
from tests.test_orchestrator import Client, call, calls, final


class SourceRecoveryTests(unittest.TestCase):
    setUp = controller_tests.OrchestratorTests.setUp
    worker = controller_tests.OrchestratorTests.worker
    runner = controller_tests.OrchestratorTests.runner

    def test_empty_after_local_read_gets_one_grounded_final_retry(self):
        (self.root / 'paper.md').write_text('Theorem A. The estimate is uniform under hypothesis H.\n')
        runner, client = self.runner([
            calls(call('read_file', {'path': 'paper.md', 'start_line': 1, 'end_line': 1})),
            final(''), final('Theorem A is uniform under H [paper.md:1].')])
        result = runner.run('What does the paper establish?', files=['paper.md'])
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(result['actions'], 1)
        self.assertEqual(len(client.requests), 3)
        retry = client.requests[-1]
        self.assertNotIn('tools', retry)
        self.assertEqual(retry['messages'][-1]['role'], 'user')
        self.assertIn('CONTROLLER FINAL ANSWER REQUEST', retry['messages'][-1]['content'])
        self.assertIn('uniform under hypothesis H', json.dumps(retry))

    def test_legacy_exhausted_source_checkpoint_has_one_final_rescue(self):
        runner, client = self.runner([final('The saved excerpt establishes A under H.')], max_actions=12, max_children=4)
        runner._new_state('Explain the main results.', ['paper.md'], [])
        runner.state['messages'] += [
            {'role': 'assistant', 'content': '', 'tool_calls': [call('read_file', {'path': 'paper.md'})]},
            {'role': 'tool', 'tool_name': 'read_file', 'tool_call_id': 'tool_1',
             'content': json.dumps({'source_excerpt': 'paper.md:1: Theorem A under H.'})},
            {'role': 'assistant', 'content': ''}]
        runner.state.update(status='incomplete', actions=12, main_calls=15,
                            last_call={'partial': {'role': 'assistant', 'content': ''},
                                       'stats': {'done_reason': 'stop'}, 'tools_enabled': False})
        saved = copy.deepcopy(runner.state)
        result = runner.run('Explain the main results.', files=['paper.md'], state=saved)
        self.assertEqual(result['status'], 'complete')
        self.assertEqual((result['actions'], result['main_calls']), (12, 16))
        self.assertEqual(len(client.requests), 1)
        self.assertNotIn('tools', client.requests[0])
        self.assertEqual(runner.state['messages'][:len(saved['messages'])], saved['messages'])
        self.assertTrue(runner.state['empty_final_retried'])

    def test_failed_empty_rescue_cannot_repeat_on_resume(self):
        runner, client = self.runner([final(''), final('')])
        result = runner.run('Hello')
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(len(client.requests), 2)
        resumed = Orchestrator(runner.agent, runner.delegate)
        self.assertEqual(resumed.run('Hello', state=runner.state)['status'], 'failed')
        self.assertEqual(len(client.requests), 2)
        self.assertFalse(store.assistant_view({'assistant_state': runner.state})['assistant_resumable'])

    def test_local_tool_errors_are_not_source_findings(self):
        runner, _ = self.runner([])
        runner._new_state('Read the source.', [], [])
        for excerpt in ('Tool error: File is outside the workspace', 'No matches found.', ''):
            runner.state['messages'] = [{'role': 'tool', 'tool_name': 'read_file',
                                         'content': json.dumps({'source_excerpt': excerpt})}]
            self.assertFalse(runner._has_findings())

    def test_generic_explore_packet_does_not_force_unrelated_jobs_to_finish(self):
        runner, _ = self.runner([])
        runner._new_state('Summarize and prove another theorem.', [], [])
        pending = {'call': call(), 'status': 'running'}
        runner._append_result(pending, {'mode': 'explore', 'result': {'source_excerpt': 'An exact excerpt.'}})
        self.assertFalse(runner.state['force_final'])

    def test_automatic_article_worker_runs_before_main_without_read_loop(self):
        (self.root / 'paper.md').write_text('Theorem A. Hypothesis H implies C.\n')
        client = Client([final('The paper establishes C under H.')])
        runner = ModeOrchestrator(Agent(client, self.workspace, ctx=32768), self.worker)
        query = 'Please explain me the main results of this article and how they are new ?'
        result = runner.run(query, files=['paper.md'])
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(result['actions'], 1)
        self.assertEqual(len(client.requests), 1)
        self.assertEqual(self.children[0]['action']['mode'], 'explore')
        self.assertEqual(self.children[0]['action']['request'], query)
        self.assertEqual(self.children[0]['action']['files'], ['paper.md'])

    def test_explicit_document_mode_takes_precedence_over_overview_guess(self):
        (self.root / 'paper.md').write_text('Theorem A. Hypothesis H implies C.\n')
        client = Client([final('The requested explanation.')])
        runner = ModeOrchestrator(Agent(client, self.workspace, ctx=32768), self.worker, required_mode='explain')
        runner.run('Explain the main results of this article.', files=['paper.md'])
        self.assertEqual(self.children[0]['action']['mode'], 'explain')
        self.assertEqual(runner.state['required_mode'], 'explain')

    def test_explicit_explain_synthesizes_its_plan_without_another_source_scan(self):
        (self.root / 'paper.md').write_text('Theorem A. Hypothesis H implies C.\n')
        client = Client([final('The mechanism is C under H.')])
        runner = ModeOrchestrator(Agent(client, self.workspace, ctx=32768), self.worker, required_mode='explain')
        runner.run('Explain Theorem A.', files=['paper.md'])
        self.assertEqual(len(self.children), 1)
        self.assertNotIn('tools', client.requests[0])

    def test_permanent_document_execution_error_is_reported_without_fallback_loop(self):
        def failed(action, child, save):
            return {'status': 'failed', 'answer': '', 'error': {'code': 'worker_execution_error',
                'message': 'A document operation failed.', 'retryable': False, 'scope': 'execution'}}
        client = Client([final('The saved document operation failed; no explanation was established.')])
        runner = ModeOrchestrator(Agent(client, self.workspace, ctx=32768), failed, required_mode='explain')
        result = runner.run('Explain the previous proof.')
        self.assertEqual(len(result['children']), 1)
        self.assertNotIn('tools', client.requests[0])


class ArticleGuiTests(GuiCase):
    extra_args = ('--ctx', '40960')

    def test_article_overview_reads_source_delegates_and_answers_in_two_calls(self):
        (self.root / 'paper.md').write_text(
            '# Article\n## Abstract\nWe prove Theorem A under H.\n## Introduction\n'
            'Our contribution is C; earlier work assumed K.\n'
            'Theorem A. Under H, conclusion C holds.\nProof. Apply estimate E.\n'
            + 'Technical proof details.\n' * 500)
        self.fake.replies = [text('Theorem A proves C under H [paper.md:L6].'),
                             text('The main result is C under H. Novelty is claimed relative to K.')]
        chat = self.ok('POST', '/api/chats', {'mode': 'free'})['id']
        self.ok('POST', f'/api/chats/{chat}/route',
                {'content': 'Explain the main results of this article and its novelty.', 'files': ['paper.md']})
        self.assertEqual(self.wait_task()['state'], 'done')
        saved = self.ok('GET', f'/api/chats/{chat}')
        self.assertEqual(saved['assistant_status'], 'complete')
        self.assertEqual(len(self.fake.requests), 2)
        first = json.dumps(self.fake.requests[0], ensure_ascii=False)
        self.assertIn('SELECTED SOURCE EXCERPTS', first)
        self.assertIn('Theorem A. Under H', first)
        self.assertFalse(self.fake.requests[0]['think'])
        self.assertNotIn('tools', self.fake.requests[0])
        self.assertNotIn('tools', self.fake.requests[1])
        self.assertIsNone(saved['assistant_budget']['tokens']['limit'])
        state = store.load_chat(self.root, chat)['assistant_state']
        self.assertEqual(state['actions'], 1)
        self.assertEqual(state['children'][0]['action']['mode'], 'explore')
        self.assertIn('source_overview', state['children'][0])
        self.assertIn('source_excerpt', state['children'][0]['result'])


if __name__ == '__main__':
    unittest.main()
