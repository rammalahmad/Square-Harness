"""A whole review strategy requires usable answers for every planned proof part."""
import copy
import json
from pathlib import Path
from unittest.mock import patch

from mathagent.review import JournalRunner, QuickReviewRunner
from tests.test_review import FakeClient, PAPER, ReviewTestCase, WRONG


def clean_check(prompt, schema):
    value = {'verdict': 'no_issue_found', 'issues': [], 'explanation': 'The supplied steps are consistent.'}
    if 'external' in schema['properties']:
        value['external'] = []
    return value


def long_proof():
    steps = '\n'.join(f'Proof step {n}: For the fixed parameter, apply the previous identity to the next term '
                      'of the finite sum and use the triangle inequality to preserve the asserted bound.'
                      for n in range(1, 201))
    return ('\\begin{theorem}\\label{thm:long}\nThe assertion holds.\n\\end{theorem}\n'
            '\\begin{proof}\n' + steps + '\n\\end{proof}\n')


class ProofCoverageTests(ReviewTestCase):
    def quick(self, check=clean_check, **kwargs):
        (self.root / 'long.tex').write_text(long_proof())
        client = FakeClient({'check': check})
        runner = QuickReviewRunner(self.agent(client, ctx=8192, predict=512), concurrency=3)
        result = runner.start('Check the proof.', source_files=['long.tex'], max_rounds=3,
                              max_tokens=60000, max_input_tokens=500000, **kwargs)
        return runner, result, client

    def test_segmented_success_counts_strategies_and_discloses_no_whole_proof_pass(self):
        runner, result, _ = self.quick()
        uid = runner.state['unit']['id']
        coverage = runner._check_coverage(uid)
        self.assertEqual(result['status'], 'reviewed')
        self.assertEqual(runner.state['verdict'], 'no_issue_found')
        self.assertTrue(coverage['complete'])
        self.assertEqual(coverage['full_passes'], 2)
        self.assertGreater(coverage['parts'], 1)
        self.assertEqual(len(runner.state['checks']), 2 * coverage['parts'])
        self.assertIn('checked with 2 review strategies', result['report'])
        self.assertNotIn(f'{len(runner.state["checks"])} independent passes', result['report'])
        self.assertIn('no whole-proof pass was performed', result['report'])
        saved = json.loads((Path(result['directory']) / 'state.json').read_text())
        self.assertEqual(len(saved['check_plan'][uid]), len(saved['checks']))
        self.assertTrue(all(c['parts'] == coverage['parts'] and c['segment'] for c in saved['checks'].values()))

    def test_one_unusable_part_prevents_global_clearance_and_completion(self):
        def check(prompt, schema):
            if 'ADVERSARIAL' in prompt and 'Proof step 200:' in prompt:
                return 'not JSON'
            return clean_check(prompt, schema)
        runner, result, _ = self.quick(check)
        coverage = runner._check_coverage(runner.state['unit']['id'])
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(runner.state['verdict'], 'uncertain')
        self.assertFalse(coverage['complete'])
        self.assertEqual(coverage['full_passes'], 1)
        self.assertEqual(coverage['completed_lenses'], ['line'])
        self.assertIn('1 completed review strategy (line)', result['report'])
        self.assertEqual(len(coverage['failed_keys']), 1)
        self.assertEqual(coverage['usable_segments'], coverage['segments'] - 1)
        self.assertIn('Coverage is incomplete', result['report'])
        self.assertIn('1 unusable answers', result['report'])
        self.assertNotIn('**No issue found**', result['report'])

    def test_budget_skips_are_counted_even_when_many_early_chunks_succeeded(self):
        def affordable(runner, cap, input_tokens, **kwargs):
            return cap <= 3 * runner._check_cap()
        with patch.object(QuickReviewRunner, '_affordable', affordable):
            runner, result, _ = self.quick()
        coverage = runner._check_coverage(runner.state['unit']['id'])
        self.assertGreaterEqual(coverage['usable_segments'], 2)  # the old test accepted two chunks as two passes
        self.assertGreater(len(coverage['missing_keys']), 0)
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(runner.state['verdict'], 'uncertain')
        self.assertIn('checks not run', result['report'])
        self.assertIn('pending proof-part checks fit the budget', result['report'])

    def test_resume_keeps_the_saved_part_plan_after_context_size_changes(self):
        (self.root / 'long.tex').write_text(long_proof())
        client = FakeClient({'check': clean_check}, fail_on=lambda payload, count: count == 3)
        runner = QuickReviewRunner(self.agent(client, ctx=8192, predict=512))
        result = runner.start('Check the proof.', source_files=['long.tex'], max_rounds=3,
                              max_tokens=60000, max_input_tokens=500000)
        self.assertEqual(result['status'], 'paused')
        uid = runner.state['unit']['id']
        plan = copy.deepcopy(runner.state['check_plan'][uid])
        finished = len(runner.state['checks'])
        self.assertEqual(finished, 2)
        resumed_client = FakeClient({'check': clean_check})
        resumed = QuickReviewRunner(self.agent(resumed_client, ctx=65536, predict=512))
        result = resumed.resume(result['id'])
        self.assertEqual(result['status'], 'reviewed')
        self.assertEqual(resumed.state['check_plan'][uid], plan)
        self.assertEqual(len(resumed_client.requests), len(plan) - finished)
        self.assertEqual(resumed._passes_on(uid), 2)

    def test_legacy_resume_rechecks_parts_whose_saved_ranges_are_unknown(self):
        (self.root / 'long.tex').write_text(long_proof())
        client = FakeClient({'check': clean_check}, fail_on=lambda payload, count: count == 3)
        runner = QuickReviewRunner(self.agent(client, ctx=8192, predict=512))
        result = runner.start('Check the proof.', source_files=['long.tex'], max_rounds=3,
                              max_tokens=60000, max_input_tokens=500000)
        self.assertEqual(result['status'], 'paused')
        saved_path = Path(result['directory']) / 'state.json'
        saved = json.loads(saved_path.read_text())
        original_plan = saved.pop('check_plan')
        for check in saved['checks'].values():
            for key in ('pass_index', 'part', 'parts', 'segment'):
                check.pop(key, None)
        saved_path.write_text(json.dumps(saved))
        resumed_client = FakeClient({'check': clean_check})
        resumed = QuickReviewRunner(self.agent(resumed_client, ctx=8192, predict=512))
        result = resumed.resume(result['id'])
        uid = resumed.state['unit']['id']
        self.assertEqual(result['status'], 'reviewed')
        # All the old parts must be checked again: their keys alone do not establish ranges.
        self.assertEqual(len(resumed_client.requests), len(original_plan[uid]))
        self.assertTrue(all('segment' in c for c in resumed.state['checks'].values()))

    def test_budget_stop_during_legacy_rechecks_does_not_count_unknown_ranges(self):
        runner, result, _ = self.quick()
        saved_path = Path(result['directory']) / 'state.json'
        saved = json.loads(saved_path.read_text())
        saved.pop('check_plan')
        for check in saved['checks'].values():
            for key in ('pass_index', 'part', 'parts', 'segment'):
                check.pop(key, None)
        # A crash can leave completed answers checkpointed before verify advances its phase.
        saved.update(status='paused', phase='verify')
        saved_path.write_text(json.dumps(saved))
        client = FakeClient({'check': clean_check})
        resumed = QuickReviewRunner(self.agent(client, ctx=8192, predict=512))
        with patch.object(QuickReviewRunner, '_affordable', return_value=False):
            result = resumed.resume(result['id'])
        coverage = resumed._check_coverage(resumed.state['unit']['id'])
        self.assertEqual(len(client.requests), 1)
        self.assertEqual(coverage['usable_segments'], 1)
        self.assertEqual(len(coverage['failed_keys']), coverage['segments'] - 1)
        self.assertFalse(coverage['complete'])
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(resumed.state['verdict'], 'uncertain')
        self.assertIn('Coverage is incomplete', result['report'])
        self.assertNotIn('**No issue found**', result['report'])

    def test_legacy_unsegmented_answers_remain_readable(self):
        good = WRONG.replace('is continuous, hence', 'is compact (Rellich), hence')
        runner = QuickReviewRunner(self.agent(FakeClient({'check': clean_check})))
        runner.start(good, max_rounds=3)
        runner.state.pop('check_plan')
        for check in runner.state['checks'].values():
            for key in ('pass_index', 'part', 'parts', 'segment'):
                check.pop(key, None)
        self.assertTrue(runner._complete())
        self.assertEqual(runner._unit_verdict(runner.state['unit']['id'])[0], 'no_issue_found')

    def test_legacy_segmented_answers_do_not_invent_the_expected_last_part(self):
        runner, _, _ = self.quick()
        runner.state.pop('check_plan')
        self.assertFalse(runner._complete())
        self.assertEqual(runner._unit_verdict(runner.state['unit']['id'])[0], 'uncertain')
        body, _ = runner._render()
        self.assertIn('expected final part is unknown', '\n'.join(body))

    def test_journal_main_proof_is_incomplete_if_one_segment_fails(self):
        # Keep the other mapped lemma, real reading/scoping/writing calls, and a long main proof.
        text = PAPER.replace('Since the embedding is continuous,',
                             '\n'.join(long_proof().splitlines()[4:-1]) + '\nSince the embedding is continuous,')
        (self.root / 'paper.tex').write_text(text)
        def check(prompt, schema):
            return 'not JSON' if 'Proof step 200:' in prompt else clean_check(prompt, schema)
        # Conservative UTF-8 counting includes the complete review skill, schema
        # and dependencies; 16k still requires source segmentation here.
        runner = JournalRunner(self.agent(FakeClient({'check': check}), ctx=16384, predict=512))
        result = runner.start('Review this paper.', source_files=['paper.tex'], max_rounds=3,
                              max_tokens=60000, max_input_tokens=500000)
        main = runner.state['plan_units'][0]
        self.assertGreater(runner._check_coverage(main)['usable_segments'], 0)
        self.assertFalse(runner._checks_complete(main))
        self.assertIn(main, runner.state['unchecked'])
        self.assertEqual(result['status'], 'partial')
        self.assertIn('(provisional)', result['report'])
        self.assertIn('incomplete coverage', result['report'])
        self.assertIn('Proofs checked completely: 1 of', result['report'])
        self.assertEqual(runner._provisional()[0], ['Theorem 1.1'])
