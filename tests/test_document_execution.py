"""Physical document calls, exact sources and usable coverage regressions."""
import json
from unittest.mock import patch

from mathagent.review import ExplainRunner, JournalRunner, QuickReviewRunner, SummaryReviewRunner
from mathagent.research import ResearchRunner, _working_reference_prompt
from mathagent.writeup import WriteupRunner
from mathagent.litreview import ReviewRunner as LiteratureRunner
from mathagent.worker_errors import WorkerInputError
from tests.test_review import ReviewTestCase, FakeClient, WRONG


def explanation(prompt, schema):
    import re
    proof = prompt.split('\nPROOF', 1)[-1]
    numbered = [int(n) for n in re.findall(r'^L(\d+):', proof, re.M)]
    return {'says': 'The supplied statement under its original hypotheses.', 'idea': 'Apply the given estimate.',
            'steps': [{'start_line': min(numbered), 'end_line': max(numbered), 'what': 'Use the estimate.',
                       'why': 'This is the source argument.', 'uses': 'The stated assumptions.'}] if numbered else [],
            'facts': [], 'hypotheses': 'The original hypotheses are retained.', 'gaps': ''}


class DocumentExecutionTests(ReviewTestCase):
    def test_explain_ignores_ordinary_predict_and_has_no_cumulative_default_caps(self):
        client = FakeClient({'explain': explanation})
        def count(payload):
            self.assertEqual(payload['model'], agent.model)
            return 1000
        client.count_input_tokens = count
        agent = self.agent(client, ctx=40960)
        agent.predict = 4096
        runner = ExplainRunner(agent)
        result = runner.start(WRONG, max_rounds=1)
        self.assertEqual(result['status'], 'reviewed', result)
        calls = [r for r in client.requests if {'says', 'idea'} <= set(r.get('format', {}).get('properties', {}))]
        self.assertGreater(calls[0]['options']['num_predict'], 4096)
        self.assertIsNone(runner.state['settings']['max_tokens'])
        self.assertIsNone(runner.state['settings']['max_input_tokens'])
        self.assertEqual(runner.state['progress']['completed'], runner.state['progress']['total'])

    def test_reading_scope_preflight_supplies_the_actual_model_to_the_tokenizer(self):
        from tests.test_review import PAPER
        (self.root / 'paper.tex').write_text(PAPER)
        client = FakeClient()
        agent = self.agent(client, ctx=40960)
        def count(payload):
            self.assertEqual(payload['model'], agent.model)
            return 1000
        client.count_input_tokens = count
        runner = SummaryReviewRunner(agent)
        result = runner.start('Review this paper.', source_files=['paper.tex'], max_rounds=1)
        self.assertEqual(result['status'], 'reviewed', result)
        self.assertTrue(any('summary' in r.get('format', {}).get('properties', {}) for r in client.requests))

    def test_proofless_out_of_source_steps_cannot_crash_renderer(self):
        client = FakeClient({'explain': lambda p, s: {'says': 'An identity.', 'idea': '',
            'steps': [{'start_line': 378, 'end_line': 380, 'what': 'Unsupported step.', 'why': '', 'uses': ''}],
            'facts': [], 'hypotheses': '', 'gaps': ''}})
        runner = ExplainRunner(self.agent(client, ctx=40960))
        result = runner.start('Lemma. Every real x satisfies x=x.', max_rounds=1)
        self.assertEqual(result['status'], 'reviewed', result)
        self.assertEqual(runner.state['explanation']['steps'], [])
        self.assertNotIn('IndexError', result['report'])
        self.assertNotIn('Unsupported step.', result['report'])

    def test_instruction_without_a_source_returns_structured_needs_source(self):
        context = {'query': 'Explain Theorem 1.', 'files': [], 'history': [],
                   'messages': [{'role': 'user', 'content': 'Explain Theorem 1.'}]}
        client = FakeClient()
        runner = ExplainRunner(self.agent(client))
        with self.assertRaises(WorkerInputError) as caught:
            runner.start('Explain Theorem 1.', reference_context=context)
        self.assertEqual(caught.exception.code, 'needs_source')
        self.assertEqual(client.requests, [])

    def test_cited_statement_tail_is_preserved_in_every_proof_packet(self):
        text = ('\\newtheorem{lemma}{Lemma}\n\\newtheorem{theorem}{Theorem}\n\\begin{document}\n'
                '\\begin{lemma}\\label{lem:dependency}\n' + '\n'.join('Assumption line ' + str(i) for i in range(40)) +
                '\nESSENTIAL_LAST_HYPOTHESIS\n\\end{lemma}\n\\begin{theorem}\\label{thm:target}\n'
                'The bound holds.\n\\end{theorem}\n\\begin{proof}\n'
                'Apply Lemma~\\ref{lem:dependency} with every hypothesis.\n\\end{proof}\n\\end{document}')
        (self.root / 'paper.tex').write_text(text)
        client = FakeClient({'explain': explanation})
        client.count_input_tokens = lambda payload: 1000
        runner = ExplainRunner(self.agent(client, ctx=40960))
        result = runner.start('Explain the mechanism of Theorem 1.', source_files=['paper.tex'], target='thm:target', max_rounds=1)
        self.assertEqual(result['status'], 'reviewed', result)
        prompts = [r['messages'][1]['content'] for r in client.requests if 'STATEMENTS OF RESULTS USED' in r['messages'][1]['content']]
        self.assertTrue(prompts)
        self.assertTrue(all('ESSENTIAL_LAST_HYPOTHESIS' in p for p in prompts))

    def test_length_recovery_increases_physical_output_instead_of_effort_label(self):
        class LengthOnce(FakeClient):
            def stream(self, payload):
                events = list(super().stream(payload))
                explains = [r for r in self.requests if {'says', 'idea'} <= set(r.get('format', {}).get('properties', {}))]
                if {'says', 'idea'} <= set(payload.get('format', {}).get('properties', {})) and len(explains) == 1:
                    events[-1].update(done_reason='length', eval_count=payload['options']['num_predict'])
                yield from events
        client = LengthOnce({'explain': explanation})
        client.count_input_tokens = lambda payload: 1000
        runner = ExplainRunner(self.agent(client, ctx=40960))
        result = runner.start(WRONG, max_rounds=1)
        self.assertEqual(result['status'], 'reviewed', result)
        caps = [r['options']['num_predict'] for r in client.requests if {'says', 'idea'} <= set(r.get('format', {}).get('properties', {}))]
        self.assertEqual(len(caps), 2)
        self.assertGreater(caps[1], caps[0])
        self.assertEqual(runner.state['level'], 'low')

    def test_working_reference_retains_user_constraints_and_indexes_big_old_tool_output(self):
        reference = {'query': 'Explain Theorem 1.', 'files': ['paper.tex'], 'history': [], 'messages': [
            {'role': 'user', 'content': 'Keep the exact condition UNIFORM_IN_EPSILON.'},
            {'role': 'tool', 'tool_call_id': 'old-native-id', 'content': 'Old report. ' * 4000}]}
        rendered = _working_reference_prompt(reference, ['paper.tex'])
        self.assertIn('UNIFORM_IN_EPSILON', rendered)
        self.assertIn('old-native-id', rendered)
        self.assertIn('SHA256', rendered)
        self.assertNotIn('Old report. Old report.', rendered)
        self.assertEqual(len(reference['messages'][1]['content']), len('Old report. ' * 4000))

    def test_writeup_outline_does_not_drop_source_after_24_sections(self):
        runner = WriteupRunner(self.agent(FakeClient(), ctx=40960))
        with patch.object(ResearchRunner, '_run', return_value={}):
            runner.start('Write these notes.', notes='\n'.join(f'Exact note {n}.' for n in range(2200)))
        outline = runner._outline('{}')
        self.assertGreater(len(outline['sections']), 24)
        self.assertTrue(any('L2200' in ref for section in outline['sections'] for ref in section['sources']))

    def test_writeup_source_range_past_200_lines_is_not_silently_cut(self):
        runner = WriteupRunner(self.agent(FakeClient(), ctx=40960))
        with patch.object(ResearchRunner, '_run', return_value={}):
            runner.start('Write these notes.', notes='\n'.join(f'Exact note {n}.' for n in range(1, 301)))
        text, clipped = runner._excerpt(['M0:L1-L300'], 20000)
        self.assertIn('L300: Exact note 300.', text)
        self.assertFalse(clipped)
        with self.assertRaises(WorkerInputError):
            runner._excerpt(['M0:L1-L300'], 30)

    def test_literature_unusable_json_is_not_a_relevance_exclusion(self):
        runner = LiteratureRunner(self.agent(FakeClient(), ctx=40960))
        with patch.object(ResearchRunner, '_run', return_value={}):
            runner.start('Find useful references.')
        runner.state.update(scope={'topic': 'PDE', 'intent': 'learn'}, pool=[{'n': 1, 'title': 'A paper'}], screen_order=[1], phase='screen')
        with patch.object(runner, '_remaining', return_value=True), patch.object(runner, '_line', return_value='1 | A paper'), \
             patch.object(runner, '_call', return_value={'text': 'invalid JSON', 'complete': False}):
            runner._screen()
            runner._screen()
            runner._screen()
        self.assertEqual(runner.state['screened'], {})
        self.assertIn('1', runner.state['screen_failed'])
        self.assertEqual(runner.state['screen_attempts']['1'], 2)

    def test_explicit_second_source_is_selected_without_rewriting_its_content(self):
        (self.root / 'a.md').write_text(WRONG)
        second = WRONG.replace('This proves the lemma.', 'EXPLICIT_SOURCE_B proves the lemma.')
        (self.root / 'b.md').write_text(second)
        client = FakeClient({'explain': explanation})
        runner = ExplainRunner(self.agent(client, ctx=40960))
        result = runner.start('Explain the lemma in b.md.', source_files=['a.md', 'b.md'], max_rounds=1)
        self.assertEqual(result['status'], 'reviewed', result)
        self.assertEqual(runner.state['sources'][0]['path'], 'b.md')
        self.assertEqual(runner.state['sources'][0]['content'], second)
        self.assertIn('EXPLICIT_SOURCE_B', json.dumps(client.requests))

    def test_duplicate_result_in_two_sources_requires_a_source_choice_before_inference(self):
        for name in ('a.md', 'b.md'):
            (self.root / name).write_text(WRONG)
        client = FakeClient({'explain': explanation})
        runner = ExplainRunner(self.agent(client, ctx=40960))
        result = runner.start('Explain the lemma.', source_files=['a.md', 'b.md'], max_rounds=1)
        self.assertEqual(result['status'], 'error', result)
        self.assertEqual(runner.state['worker_error']['code'], 'ambiguous_source')
        self.assertEqual(client.requests, [])

    def test_a_uniquely_named_result_can_select_its_source(self):
        from tests.test_review import PAPER
        (self.root / 'a.tex').write_text(PAPER)
        (self.root / 'b.tex').write_text(PAPER.replace('thm:main', 'thm:chosen'))
        client = FakeClient({'explain': explanation})
        runner = ExplainRunner(self.agent(client, ctx=40960))
        result = runner.start('Explain thm:chosen.', source_files=['a.tex', 'b.tex'], target='thm:chosen', max_rounds=1)
        self.assertEqual(result['status'], 'reviewed', result)
        self.assertEqual(runner.state['sources'][0]['path'], 'b.tex')

    def test_a_math_only_proof_line_cannot_be_omitted_from_line_coverage(self):
        def missing_math(prompt, schema):
            return {'says': 'The identity holds.', 'idea': 'Use the identity.',
                    'steps': [{'start_line': a, 'end_line': a, 'what': 'Source prose.', 'why': '', 'uses': ''} for a in (4, 6)],
                    'facts': [], 'hypotheses': '', 'gaps': ''}
        runner = ExplainRunner(self.agent(FakeClient({'explain': missing_math, 'fill': lambda p, s: {'steps': []}}), ctx=40960))
        result = runner.start('Lemma. The identity holds.\n\nProof.\nUse the displayed identity.\n$x=x$.\nThis proves the claim.', max_rounds=1)
        self.assertEqual(result['status'], 'partial', result)
        self.assertEqual(runner._missing(), [[5, 5]])
        self.assertNotIn('Every line of the proof is explained.', result['report'])

    def test_a_part_citation_is_rejected_instead_of_silently_clipped(self):
        runner = ExplainRunner(self.agent(FakeClient(), ctx=40960))
        with patch.object(ResearchRunner, '_run', return_value={}):
            runner.start(WRONG, max_rounds=1)
        runner._map()
        runner._pick_unit()
        value = explanation('', {})
        value['steps'] = [{'start_line': 3, 'end_line': 5, 'what': 'Claims the entire proof.', 'why': '', 'uses': ''}]
        self.assertIsNone(runner._clean_explanation(value, segment=[[4, 4]]))
        self.assertTrue(any('instead of changing its citation' in w for w in runner.state['warnings']))

    def test_scope_preserves_all_selected_setting_lines_in_check_packets(self):
        from tests.test_review import PAPER
        setting = '\n'.join('Setting assumption ' + str(i) + '.' for i in range(1, 151))
        text = PAPER.replace('\\begin{document}', '\\begin{document}\n' + setting + '\nLATE_UNIFORM_HYPOTHESIS')
        (self.root / 'paper.tex').write_text(text)
        client = FakeClient({'scope': lambda p, s: {'overview': 'Overview.', 'field': '', 'contribution': '', 'main': [],
            'notation': [{'start_line': 1, 'end_line': len(text.splitlines())}], 'queries': [], 'closest': []}})
        client.count_input_tokens = lambda payload: 1000
        runner = JournalRunner(self.agent(client, ctx=40960))
        result = runner.start('Review this paper.', source_files=['paper.tex'], max_rounds=1)
        self.assertEqual(result['status'], 'reviewed', result)
        self.assertEqual(runner.state['scope']['notation'], [[1, len(text.splitlines())]])
        check_packets = [r['messages'][1]['content'] for r in client.requests
                         if 'SETTING AND NOTATION OF THE MANUSCRIPT' in r['messages'][1]['content']]
        self.assertTrue(check_packets)
        self.assertTrue(all('LATE_UNIFORM_HYPOTHESIS' in prompt for prompt in check_packets))

    def test_unknown_finish_or_missing_usage_cannot_be_a_usable_explanation(self):
        for stats in ({'eval_count': 50, 'prompt_eval_count': 200},
                      {'done_reason': 'unknown', 'eval_count': 50, 'prompt_eval_count': 200},
                      {'done_reason': 'stop', 'prompt_eval_count': 200}):
            with self.subTest(stats=stats):
                client = FakeClient()
                client.stream = lambda payload: iter([{'message': {'content': json.dumps(explanation('', {}))}, 'done': True, **stats}])
                runner = ExplainRunner(self.agent(client, ctx=40960))
                with patch.object(ResearchRunner, '_run', return_value={}):
                    runner.start(WRONG, max_rounds=1)
                result = runner._ask('explain', 'Explain the source.', cap=2000, schema={'type': 'object'})
                self.assertFalse(result['complete'])
                self.assertIsNone(result['value'])
                self.assertEqual(runner.state['calls'][-1]['status'], 'incomplete')

    def test_generic_document_call_does_not_accept_unknown_finish(self):
        client = FakeClient()
        client.stream = lambda payload: iter([{'message': {'content': 'Unfinished report.'}, 'done': True,
            'done_reason': 'unknown', 'eval_count': 50, 'prompt_eval_count': 200}])
        runner = ResearchRunner(self.agent(client, ctx=40960))
        with patch.object(ResearchRunner, '_run', return_value={}):
            runner.start('Review the topic.')
        result = runner._call('draft', 'Write the report.', cap=2000)
        self.assertFalse(result['complete'])
        self.assertEqual(runner.state['calls'][-1]['status'], 'incomplete')

    def test_writeup_recovery_grows_from_the_actual_first_allocation(self):
        runner = WriteupRunner(self.agent(FakeClient(), ctx=40960))
        with patch.object(ResearchRunner, '_run', return_value={}):
            runner.start('Write the notes.', notes='Exact source line.')
        runner.state['sections'] = [{'title': 'Section', 'sources': ['M0:L1-L1'], 'goal': 'Preserve the source.',
                                     'complete': False, 'latex': ''}]
        calls = []
        def call(role, prompt, *, cap, **kwargs):
            calls.append(kwargs)
            runner.state['calls'].append({'reserved_tokens': 512 if len(calls) == 1 else 1024})
            return {'text': '\\section{Section}\n% src: [M0:L1]\nExact source line.',
                    'complete': len(calls) > 1, 'output_limit': 512 if len(calls) == 1 else 1024}
        with patch.object(runner, '_call', side_effect=call):
            runner._section()
        self.assertEqual(calls[1]['minimum_output'], 513)
        self.assertTrue(runner.state['sections'][0]['complete'])

    def test_copying_a_single_section_is_not_a_fidelity_review(self):
        runner = WriteupRunner(self.agent(FakeClient(), ctx=40960))
        with patch.object(ResearchRunner, '_run', return_value={}):
            runner.start('Write the notes.', notes='Exact source line.')
        latex = '\\section{Section}\n% src: [M0:L1]\nExact source line.'
        runner.state['sections'] = [{'title': 'Section', 'sources': ['M0:L1-L1'], 'latex': latex, 'complete': True}]
        with patch.object(runner, '_call', return_value={'text': latex, 'complete': True}):
            runner._review()
        self.assertFalse(runner.state['review_complete'])
        self.assertEqual(runner.state['review'], '')
        self.assertTrue(any('returned LaTeX instead of a review' in w for w in runner.state['warnings']))
