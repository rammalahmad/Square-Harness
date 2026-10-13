"""Source routing and overview packets stay narrow, bounded and source-faithful."""
from pathlib import Path
import re
import tempfile
import unittest

from mathagent.source_tasks import automatic_source_action, source_overview
from mathagent.tools import Workspace


class SourceActionTests(unittest.TestCase):
    def test_whole_article_results_and_novelty_use_explore(self):
        requests = [
            'Please explain me the main results of this article and how they are new ?',
            'What are the main results of this paper?',
            'What is new in this article?',
            'Summarize the attached manuscript.',
            'Could you explain this article?',
            'Explain Theorem A and Theorem B of this article.',
            'Peux-tu m’expliquer les principaux résultats de cet article et leur nouveauté ?',
            'Résume cet article.',
            'En quoi les résultats de cet article sont-ils nouveaux ?',
        ]
        for query in requests:
            with self.subTest(query=query):
                result = automatic_source_action(query, ['paper.pdf'])
                self.assertIsNotNone(result)
                self.assertEqual(result['mode'], 'explore')
                self.assertEqual(result['request'], query)
                self.assertEqual(result['files'], ['paper.pdf'])
                self.assertEqual(result['effort'], 'medium')

    def test_one_statement_or_proof_uses_existing_explain(self):
        for query in ['Explain Lemma 3.2 in paper.pdf.',
                      'Please explain the proof in the attached notes.',
                      'Walk me through the proof of the main result in paper.pdf.',
                      'Explain the proof of the main result of this article.',
                      'Walk me through Theorem A, the main result of the article.',
                      'Explique-moi le théorème 2 de cet article.',
                      'Explique la preuve du résultat principal de cet article.']:
            with self.subTest(query=query):
                result = automatic_source_action(query, ['paper.pdf', 'notes.tex'])
                self.assertIsNotNone(result)
                self.assertEqual(result['mode'], 'explain')
                self.assertEqual(result['request'], query)
                self.assertEqual(result['files'], ['paper.pdf', 'notes.tex'])

    def test_named_source_is_explicit_even_without_attachment_list(self):
        result = automatic_source_action('Summarize `notes/paper.tex` and its main results.')
        self.assertEqual(result['files'], ['notes/paper.tex'])
        self.assertEqual(result['mode'], 'explore')

    def test_explicit_manuscript_reviews_keep_their_full_source_workflows(self):
        requests = [
            ('Please review this manuscript.', 'referee'),
            ('Could you referee paper.tex?', 'referee'),
            ('Give me a detailed review of the attached article.', 'detailed_review'),
            ('Please give a thorough review of paper.pdf.', 'detailed_review'),
            ('Check all the proofs of this article.', 'detailed_review'),
            ('Vérifie toutes les preuves de cet article.', 'detailed_review'),
            ('Review the proof of Lemma 3.2 in paper.pdf.', 'quick_review'),
            ('Check the proof of Theorem A in paper.pdf.', 'quick_review'),
            ('Is the proof of Lemma 2 in paper.pdf correct?', 'quick_review'),
        ]
        for query, mode in requests:
            with self.subTest(query=query):
                action = automatic_source_action(query, ['paper.pdf', 'appendix.tex'])
                self.assertIsNotNone(action)
                self.assertEqual(action['mode'], mode)
                self.assertEqual(action['request'], query)
                self.assertEqual(action['files'], ['paper.pdf', 'appendix.tex'])

    def test_quoted_negative_and_ordinary_mentions_do_not_select_review(self):
        for query in ['Do not review the manuscript.',
                      'The article asks "Check all proofs". Explain the word complete.',
                      'What is a referee review?',
                      'Review our conversation so far.',
                      'Check the compiler output; paper.pdf is attached.',
                      'Is the algorithm correct? paper.pdf is attached.']:
            with self.subTest(query=query):
                self.assertIsNone(automatic_source_action(query, ['paper.pdf']))

    def test_clear_additional_jobs_defer_to_the_controller(self):
        queries = [
            'Please explain the main results of this article and also prove Theorem 3.2.',
            'Explain Theorem 2 of paper.pdf and find references for related results.',
            'Please summarize paper.pdf and write the summary to summary.tex.',
            'Please review paper.pdf for typos and explain Theorem 2.',
            'Explain Theorem A and check the proof of Lemma 2 in paper.pdf.',
            'Summarize this article. Prove Theorem 3.2 under weaker assumptions.',
            'Summarize this article, then walk me through the proof of Theorem 3.2.',
            'Résume cet article puis démontre le théorème 2.',
            'Explique les résultats principaux de cet article et rédige un résumé en LaTeX.',
        ]
        for query in queries:
            with self.subTest(query=query):
                self.assertIsNone(automatic_source_action(query, ['paper.pdf']))

    def test_main_results_and_novelty_remain_one_article_objective(self):
        for query in ['Explain the main results of this article and how they are new.',
                      'Summarize this article and its contribution.',
                      'Explique les principaux résultats de cet article et leur nouveauté.']:
            with self.subTest(query=query):
                self.assertEqual(automatic_source_action(query, ['paper.pdf'])['mode'], 'explore')

    def test_source_existence_or_quoted_requests_do_not_choose_workflow(self):
        requests = [
            'Hi there.', 'Is Rellich compactness true?', 'Prove the main theorem.',
            'Explain how the token counter works; paper.pdf is attached.',
            'The article says "explain the main results". What does compact mean?',
            '"Explain the main results of this article"',
            '`Explain the main results of this article`',
            '> Explain the main results of this article',
            'Do not explain the article; only list its authors.',
            'Please don’t explain the proof.',
            'Résume cet article sans expliquer les résultats.',
            'N’explique pas ce théorème.',
            'Please explain why weak convergence does not imply strong convergence.',
            'What is a PDE? I attached an article.',
        ]
        for query in requests:
            with self.subTest(query=query):
                self.assertIsNone(automatic_source_action(query, ['paper.pdf']))
        self.assertIsNone(automatic_source_action('Explain the main results of this article.'))

    def test_invalid_or_oversized_queries_leave_controller_unchanged(self):
        self.assertIsNone(automatic_source_action(None, ['paper.pdf']))
        self.assertIsNone(automatic_source_action('Explain this article.' + ' ' * 8000, ['paper.pdf']))
        self.assertIsNone(automatic_source_action('Explain this article.', [None]))


class SourceOverviewTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.workspace = Workspace(self.root)

    def test_long_source_gives_exact_located_results_and_explicit_partial_coverage(self):
        lines = ['A paper', 'Abstract', 'We determine a new threshold.', '1. Introduction',
                 'Previous results required separation.', 'Theorem A. Under hypothesis H, estimate E holds.',
                 'Proof. A long proof follows.']
        lines += [f'Proof detail {n}: x_n = y_n, with every hypothesis preserved.' for n in range(1500)]
        lines += ['2. Applications', 'Theorem 2. Another precise result.', 'Proof. Details.',
                  'References', '[1] First Author. A primary source. 2020.', '[2] Second Author. A source. 2021.']
        (self.root / 'paper.md').write_text('\n'.join(lines))
        packet = source_overview(self.workspace, ['paper.md'], max_chars=12000)
        self.assertLessEqual(len(packet), 12000)
        self.assertIn('L6: Theorem A. Under hypothesis H, estimate E holds.', packet)
        self.assertIn('First Author. A primary source. 2020.', packet)
        self.assertIn('Section at L1508: Applications', packet)
        self.assertIn('partial source', packet)
        self.assertIn('not a full reading', packet)
        self.assertIn('independently verified prior work', packet)
        coverage = re.search(r'Coverage supplied here: (\d+)/(\d+) lines', packet)
        self.assertIsNotNone(coverage)
        self.assertLess(int(coverage[1]), int(coverage[2]))
        for number, line in re.findall(r'^L(\d+): (.*)$', packet, re.M):
            self.assertEqual(line, lines[int(number) - 1])

    def test_tex_preamble_does_not_displace_abstract_and_main_statement(self):
        lines = [r'\documentclass{article}', r'\newtheorem{theorem}{Theorem}']
        lines += [r'\newcommand{\unused' + str(n) + r'}{x}' for n in range(300)]
        lines += [r'\begin{document}', r'\begin{abstract}', 'An exact abstract.', r'\end{abstract}',
                  r'\section{Introduction}', 'The new contribution is here.', r'\begin{theorem}',
                  'Under assumptions H1 and H2, the bound is uniform.', r'\end{theorem}',
                  r'\begin{proof}', 'Proof details.', r'\end{proof}', r'\end{document}']
        (self.root / 'paper.tex').write_text('\n'.join(lines))
        packet = source_overview(self.workspace, ['paper.tex'], max_chars=5000)
        self.assertIn('An exact abstract.', packet)
        self.assertIn('Under assumptions H1 and H2, the bound is uniform.', packet)
        self.assertNotIn(r'\unused', packet)

    def test_pdf_glued_headings_still_supply_real_main_theorems(self):
        lines = ['A paper', 'Abstract', 'The paper has two main results.', '1 Introduction and main results']
        lines += ['Background line with mathematical context.'] * 200
        lines += ['Theorem A.Let H hold. Then the exact threshold is T.',
                  'Both assumptions H1 and H2 are essential.',
                  'Remark1.1 (Scope). This theorem has a limitation.']
        lines += ['Background detail.'] * 100
        lines += ['Theorem B.Let the bracket step be larger. Then there is no finite time.',
                  'Theorem A. For the obstruction, we use a separate argument.',
                  '2 Applications', 'References', '[1] A primary reference.']
        (self.root / 'paper.md').write_text('\n'.join(lines))
        packet = source_overview(self.workspace, ['paper.md'], max_chars=9000)
        self.assertIn('Theorem A.Let H hold. Then the exact threshold is T.', packet)
        self.assertIn('Theorem B.Let the bracket step be larger.', packet)
        self.assertIn('Section at L4: Introduction and main results', packet)
        self.assertIn('Theorem A at L205-L206', packet)

    def test_disabled_file_group_stays_disabled_until_exact_file_pinned(self):
        (self.root / 'paper.tex').write_text('Theorem A. Complete statement.')
        workspace = Workspace(self.root, read_types=[])
        denied = source_overview(workspace, ['paper.tex'])
        self.assertNotIn('Complete statement.', denied)
        self.assertIn('No source excerpt supplied', denied)
        workspace.pin_files(['paper.tex'])
        allowed = source_overview(workspace, ['paper.tex'])
        self.assertIn('Complete statement.', allowed)
        self.assertEqual(workspace.readable, set())

    def test_parent_traversal_and_hidden_sources_are_never_read(self):
        (self.root / '.hidden.md').write_text('HIDDEN SOURCE')
        packet = source_overview(self.workspace, ['../outside.md', '.hidden.md'])
        self.assertNotIn('HIDDEN SOURCE', packet)
        self.assertEqual(packet.count('No source excerpt supplied'), 2)

    def test_long_original_lines_are_omitted_whole_with_bound_preserved(self):
        long = 'AN_UNSPLIT_FORMULA_' * 1000
        (self.root / 'paper.md').write_text('Abstract\n' + long + '\nShort original line.\n')
        packet = source_overview(self.workspace, ['paper.md'], max_chars=1400)
        self.assertLessEqual(len(packet), 1400)
        self.assertNotIn('AN_UNSPLIT_FORMULA_', packet)
        self.assertIn('omitted', packet)

    def test_multiple_sources_share_one_bound(self):
        for name in ['one.md', 'two.md']:
            (self.root / name).write_text('Abstract\nAn exact statement.\n' * 100)
        packet = source_overview(self.workspace, ['one.md', 'two.md'], max_chars=6000)
        self.assertLessEqual(len(packet), 6000)
        self.assertIn('SOURCE: one.md', packet)
        self.assertIn('SOURCE: two.md', packet)

    def test_local_pdf_uses_same_extraction_and_permission_boundary_as_workspace(self):
        class LocalPDF:
            def __init__(self):
                self.paths = []

            def import_local(self, path):
                self.paths.append(path)
                return {'document_id': 'local-doc'}

            def _document(self, ident):
                return {}, ['Abstract', 'A source-faithful PDF statement.']

        (self.root / 'paper.pdf').write_bytes(b'%PDF-fake')
        library = LocalPDF()
        workspace = Workspace(self.root, literature=library, read_types=[])
        workspace.pin_files(['paper.pdf'])
        packet = source_overview(workspace, ['paper.pdf'])
        self.assertEqual(library.paths, ['paper.pdf'])
        self.assertIn('L2: A source-faithful PDF statement.', packet)

    def test_empty_files_and_invalid_bound(self):
        self.assertEqual(source_overview(self.workspace, []), '')
        with self.assertRaises(ValueError):
            source_overview(self.workspace, ['paper.md'], max_chars=100)


if __name__ == '__main__':
    unittest.main()
