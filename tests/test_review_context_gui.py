"""Earlier conversation proofs reach review workers through the GUI API."""
import json

from mathagent.gui import store
from tests.test_gui import GuiCase, text
from tests.test_review import WRONG, default_check


class ReviewContextGuiTests(GuiCase):
    extra_args = ('--ctx', '40960')

    def previous_proof_chat(self):
        chat = self.ok('POST', '/api/chats', {'mode': 'free', 'online': False})['id']
        store.commit_turn(self.root, chat, [
            {'role': 'user', 'content': 'Use the hypothesis GUI_CONTEXT_HYPOTHESIS_8134.'},
            {'role': 'assistant', 'content': WRONG}], [], [])
        return chat

    def test_selected_quick_review_checks_previous_proof_and_preserves_hypotheses(self):
        chat = self.previous_proof_chat()
        check = default_check('\nPROOF:\nL4: continuous, hence\n\n', {'properties': {}})
        confirm = {'assessment': 'valid', 'kind': 'invalid_inference',
                   'argument': 'Continuity does not imply compactness.', 'suggestion': 'Use Rellich.'}
        self.fake.replies = [text(json.dumps(check)), text(json.dumps(check)),
                             text(json.dumps(confirm)), text('The previous proof contains a gap.')]
        self.ok('POST', f'/api/chats/{chat}/route', {
            'content': '@quick_review Check the previous proof.',
            'mode_call': {'mode': 'quick_review', 'start': 0}})
        task = self.wait_task()
        self.assertEqual(task['state'], 'done', task)
        saved = self.ok('GET', f'/api/chats/{chat}')
        [card] = [item for item in saved['transcript'] if item['role'] == 'route']
        detail = self.ok('GET', f'/api/research/{card["job_id"]}')
        self.assertEqual((detail['status'], detail['verdict']), ('reviewed', 'issues_found'), detail)
        state = store.research_state(self.root, card['job_id'])
        self.assertEqual(state['sources'][0]['content'], WRONG)
        self.assertEqual(len(self.fake.requests), 4)
        for payload in self.fake.requests[:3]:
            self.assertIn('GUI_CONTEXT_HYPOTHESIS_8134', payload['messages'][1]['content'])
            self.assertIn('BEGIN EXACT REFERENCE CONTEXT', payload['messages'][1]['content'])

    def test_selected_explain_uses_previous_proof_and_preserves_hypotheses(self):
        chat = self.previous_proof_chat()
        explanation = {'says': 'A bounded sequence has a convergent subsequence.', 'idea': 'Use compactness.',
                       'steps': [{'start_line': 3, 'end_line': 5, 'what': 'Weak compactness followed by Rellich.',
                                  'why': 'The interval is bounded.', 'uses': 'Rellich compactness.'}],
                       'facts': [], 'hypotheses': 'A bounded interval.', 'gaps': 'Continuity alone is insufficient.'}
        self.fake.replies = [text(json.dumps(explanation)), text(json.dumps({'problems': []})),
                             text('Here is the explanation of the previous proof.')]
        self.ok('POST', f'/api/chats/{chat}/route', {
            'content': '@explain Explain the previous proof.', 'mode_call': {'mode': 'explain', 'start': 0}})
        task = self.wait_task()
        self.assertEqual(task['state'], 'done', task)
        saved = self.ok('GET', f'/api/chats/{chat}')
        [card] = [item for item in saved['transcript'] if item['role'] == 'route']
        detail = self.ok('GET', f'/api/research/{card["job_id"]}')
        self.assertEqual((detail['status'], detail['variant']), ('reviewed', 'explain'), detail)
        self.assertEqual(len(self.fake.requests), 3)
        for payload in self.fake.requests[:2]:
            self.assertIn('GUI_CONTEXT_HYPOTHESIS_8134', payload['messages'][1]['content'])
