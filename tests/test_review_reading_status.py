"""Reading coverage must count usable answers, including after an interrupted job resumes."""
import json
from unittest.mock import patch

from mathagent.review import JournalRunner, SummaryReviewRunner
from tests.test_review import FakeClient, PAPER, ReviewTestCase


class ReadingStatusTests(ReviewTestCase):
    def setUp(self):
        super().setUp()
        (self.root / 'paper.tex').write_text(PAPER)

    def run_review(self, client, runner_type=SummaryReviewRunner):
        runner = runner_type(self.agent(client))
        result = runner.start('Review the paper.', source_files=['paper.tex'], max_rounds=3)
        return runner, result

    @staticmethod
    def read_requests(client):
        return [p for p in client.requests if 'comments' in p.get('format', {}).get('properties', {})]

    @staticmethod
    def writing_findings(client):
        request = next(p for p in client.requests if 'recommendation' in p.get('format', {}).get('properties', {}))
        return json.loads(request['messages'][-1]['content'].split('FINDINGS:\n', 1)[1])

    def test_all_unusable_readings_are_partial_and_not_counted_as_read(self):
        client = FakeClient({'read': lambda prompt, schema: 'This is not JSON.'})
        runner, result = self.run_review(client)
        self.assertEqual(result['status'], 'partial')
        self.assertTrue(runner.state['write'])
        self.assertTrue(runner.state['reading'])
        self.assertTrue(all(not r['usable'] for r in runner.state['reading'].values()))
        self.assertEqual(self.writing_findings(client)['parts_not_read'], len(runner.state['read_parts']))
        self.assertIn('Read for understanding and presentation: 0 of ', result['report'])
        self.assertIn('The paper was not read for presentation.', result['report'])
        self.assertNotIn('None found.', result['report'])
        self.assertIn('manuscript reading answer(s) were unusable or truncated', result['report'])
        self.assertIn('(provisional)', result['report'])

    def test_one_failed_part_preserves_successful_coverage(self):
        def read(prompt, schema):
            if '\nL1:' in prompt:
                return 'bad answer'
            return {'summary': 'This part introduces the setting and presents its result.', 'comments': []}
        client = FakeClient({'read': read})
        runner, result = self.run_review(client)
        failed = runner.state['read_parts'][0]
        read_lines = sum(b - a + 1 for a, b in runner.state['read_parts'][1:])
        total_lines = sum(b - a + 1 for a, b in runner.state['read_parts'])
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(self.writing_findings(client)['parts_not_read'], 1)
        self.assertFalse(runner.state['reading'][f'L{failed[0]}']['usable'])
        self.assertIn(f'Read for understanding and presentation: {read_lines} of {total_lines} lines', result['report'])
        self.assertIn('1 manuscript reading answer(s)', result['report'])
        self.assertIn('None found in the parts read.', result['report'])

    def test_truncated_readings_are_unusable_even_with_valid_json(self):
        client = FakeClient({'read': lambda prompt, schema: {'summary': 'TRUNCATED READING SUMMARY', 'comments': []}})
        original = client.stream
        def stream(payload):
            for event in original(payload):
                if 'comments' in payload.get('format', {}).get('properties', {}):
                    event['done_reason'] = 'length'
                yield event
        client.stream = stream
        runner, result = self.run_review(client)
        self.assertEqual(result['status'], 'partial')
        self.assertTrue(all(not r['usable'] and not r['complete'] for r in runner.state['reading'].values()))
        self.assertTrue(all(c['status'] == 'truncated' for c in runner.state['calls'] if c['role'] == 'read'))
        self.assertEqual(self.writing_findings(client)['parts_not_read'], len(runner.state['read_parts']))
        self.assertNotIn('TRUNCATED READING SUMMARY', result['report'])
        self.assertNotIn('TRUNCATED READING SUMMARY', json.dumps(self.writing_findings(client)))

    def test_incomplete_reading_objects_do_not_count_as_success(self):
        for value in ({}, {'summary': 'A summary without comments.'}, {'summary': '', 'comments': []},
                      {'summary': 'A summary with malformed comments.', 'comments': {}}):
            with self.subTest(value=value):
                client = FakeClient({'read': lambda prompt, schema, value=value: value})
                runner, result = self.run_review(client)
                self.assertEqual(result['status'], 'partial')
                self.assertTrue(all(not r['usable'] for r in runner.state['reading'].values()))

    def test_valid_empty_comment_lists_complete_the_review(self):
        client = FakeClient({'read': lambda prompt, schema: {'summary': 'This part states its setting and result.', 'comments': []}})
        runner, result = self.run_review(client)
        self.assertEqual(result['status'], 'reviewed')
        self.assertTrue(all(r['usable'] and r['complete'] for r in runner.state['reading'].values()))
        self.assertEqual(self.writing_findings(client)['parts_not_read'], 0)
        self.assertIn('None found.', result['report'])
        self.assertNotIn('manuscript reading answer(s)', result['report'])

    def test_resume_retries_a_saved_unusable_part_and_keeps_successes(self):
        client = FakeClient({'read': lambda prompt, schema: {'summary': 'This part states its setting and result.', 'comments': []}})
        runner, _ = self.run_review(client)
        key = next(iter(runner.state['reading']))
        runner.state['reading'][key].update(usable=False, complete=False, summary='FAILED SAVED SUMMARY')
        runner.state.update(status='paused', phase='read')
        runner._save()
        resumed_client = FakeClient({'read': lambda prompt, schema: {'summary': 'Recovered reading summary.', 'comments': []}})
        resumed = SummaryReviewRunner(self.agent(resumed_client))
        result = resumed.resume(runner.state['id'])
        self.assertEqual(result['status'], 'reviewed')
        self.assertEqual(len(self.read_requests(resumed_client)), 1)
        self.assertEqual(resumed.state['reading'][key]['summary'], 'Recovered reading summary.')
        self.assertNotIn('FAILED SAVED SUMMARY', result['report'])

    def test_saved_failed_entry_is_excluded_when_resume_starts_after_reading(self):
        runner, _ = self.run_review(FakeClient())
        key = next(iter(runner.state['reading']))
        runner.state['reading'][key].update(usable=False, summary='FAILED SAVED SUMMARY')
        runner.state.update(status='paused', phase='scope')
        runner._save()
        client = FakeClient()
        resumed = SummaryReviewRunner(self.agent(client))
        result = resumed.resume(runner.state['id'])
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(self.writing_findings(client)['parts_not_read'], 1)
        self.assertNotIn('FAILED SAVED SUMMARY', result['report'])
        self.assertNotIn('FAILED SAVED SUMMARY', json.dumps(client.requests))
        self.assertIn('1 manuscript reading answer(s)', result['report'])

    def test_legacy_summary_entries_without_flags_still_complete_on_resume(self):
        runner, _ = self.run_review(FakeClient())
        for entry in runner.state['reading'].values():
            entry.pop('usable', None)
            entry.pop('complete', None)
        runner.state.update(status='paused', phase='done')
        runner._save()
        result = SummaryReviewRunner(self.agent(FakeClient())).resume(runner.state['id'])
        self.assertEqual(result['status'], 'reviewed')
        self.assertNotIn('manuscript reading answer(s)', result['report'])

    def test_detailed_review_is_partial_when_reading_failed_even_with_checked_proofs(self):
        runner, result = self.run_review(FakeClient({'read': lambda prompt, schema: 'bad answer'}), JournalRunner)
        self.assertTrue(runner.state['checks'])
        self.assertTrue(all(runner._checks_complete(uid) for uid in runner.state['plan_units']))
        self.assertFalse(runner.state['unchecked'])
        self.assertEqual(result['status'], 'partial')
        self.assertIn('(provisional)', result['report'])
        self.assertIn('part(s) were not read for understanding and presentation', result['report'])
        self.assertIn('Read for understanding and presentation: 0 of ', result['report'])

    def test_budget_skipped_reading_is_partial_and_warned(self):
        client = FakeClient()
        # Default cumulative caps are now absent. Exercise an explicit admission
        # refusal rather than relying on the removed 60k default ceiling.
        with patch.object(SummaryReviewRunner, '_affordable', return_value=False):
            runner, result = self.run_review(client)
        self.assertEqual(result['status'], 'partial')
        self.assertFalse(self.read_requests(client))
        self.assertFalse(runner.state['reading'])
        self.assertIn('no reading answer is available', result['report'])
        self.assertIn('Read for understanding and presentation: 0 of ', result['report'])
