import copy
import json
import unittest

from mathagent.conversation_sources import conversation_sources
from mathagent.worker_errors import WorkerInputError
from tests import test_orchestrator as controller_tests
from tests.test_orchestrator import call, calls, final


class ConversationSourcesTests(unittest.TestCase):
    def test_numbered_followup_inherits_human_attachment(self):
        transcript = [{'role': 'user', 'files': ['article.tex']},
                      {'role': 'assistant', 'files': ['worker-report.md']}]
        self.assertEqual(conversation_sources('Explain the mechanism of Theorem 1.', [], transcript), ['article.tex'])

    def test_new_attachment_and_named_source_take_precedence(self):
        transcript = [{'role': 'user', 'files': ['old.pdf']}]
        self.assertEqual(conversation_sources('Explain Theorem 1.', ['new.tex'], transcript), ['new.tex'])
        self.assertEqual(conversation_sources('Explain Theorem 1 in named.tex.', [], transcript), ['named.tex'])

    def test_ambiguous_sources_require_selection(self):
        with self.assertRaises(WorkerInputError) as caught:
            conversation_sources('Explain Theorem 1.', [], [{'role': 'user', 'files': ['a.pdf', 'b.pdf']}])
        self.assertEqual(caught.exception.code, 'ambiguous_source')

    def test_plural_source_request_inherits_group(self):
        self.assertEqual(conversation_sources('Compare these articles.', [],
            [{'role': 'user', 'files': ['a.pdf', 'b.pdf']}]), ['a.pdf', 'b.pdf'])

    def test_ordinary_question_does_not_inherit_article(self):
        self.assertEqual(conversation_sources('Why is a real square nonnegative?', [],
            [{'role': 'user', 'files': ['a.pdf']}]), [])

    def test_french_numbered_followup(self):
        self.assertEqual(conversation_sources('Explique le mécanisme du Théorème 2.', [],
            [{'role': 'user', 'files': ['a.tex']}]), ['a.tex'])


class ArchivedConversationTests(unittest.TestCase):
    setUp = controller_tests.OrchestratorTests.setUp
    worker = controller_tests.OrchestratorTests.worker
    runner = controller_tests.OrchestratorTests.runner

    def test_context_pressure_leaves_room_for_answer_instead_of_another_read_loop(self):
        (self.root / 'paper.md').write_text('The supported result assumes H.\n')
        runner, client = self.runner([calls(call('read_file', {'path': 'paper.md'})),
            final('The supported result holds under H; the remaining source is unread.')])
        client.count_input_tokens = lambda payload: 1000 if not client.requests else runner.agent.ctx - 512 - 5000
        result = runner.run('Explain the available result.')
        self.assertEqual(result['status'], 'complete')
        self.assertNotIn('tools', client.requests[-1])
        self.assertEqual(client.requests[-1]['options']['num_predict'], 5000)
        self.assertIn('unread or unresolved', client.requests[-1]['messages'][0]['content'])

    def test_archived_tool_evidence_is_retrievable_verbatim(self):
        evidence = 'Exact hypothesis H, equation E.\n' * 1500
        history = [{'role': 'user', 'content': 'Assume H and retain every quantifier.'},
                   {'role': 'assistant', 'content': '', 'tool_calls': [call('read_file', {'path': 'paper.md'})]},
                   {'role': 'tool', 'tool_name': 'read_file', 'content': evidence},
                   {'role': 'assistant', 'content': 'The result holds under H.'}]
        original = copy.deepcopy(history)
        runner, client = self.runner([calls(call('read_conversation',
            {'index': 2, 'start_line': 1, 'end_line': 2})), final('The exact source assumes H.')])
        result = runner.run('Explain the previous result.', history=history)
        self.assertEqual(result['status'], 'complete')
        self.assertEqual(history, original)
        self.assertEqual(runner.state['history'], original)
        self.assertEqual(runner.agent.history[:len(original)], original)
        self.assertEqual(runner.conversation_history()[:len(original)], original)
        self.assertIn('Assume H and retain every quantifier.', str(client.requests[0]))
        self.assertNotIn(evidence, str(client.requests[0]))
        self.assertIn('ARCHIVED CONVERSATION SOURCES', str(client.requests[0]))
        self.assertIn('1: Exact hypothesis H, equation E.\n2: Exact hypothesis H, equation E.',
                      json.loads(client.requests[1]['messages'][-1]['content'])['source_excerpt'])
