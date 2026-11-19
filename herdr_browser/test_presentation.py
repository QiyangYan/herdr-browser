import unittest

from .presentation import enhance_report, inline_text
from .reports import checked_patch, task_body, paragraphs


class PresentationTests(unittest.TestCase):
    def test_emphasis_is_author_selected_and_escapes_html(self):
        text = '**Wait for verification.** <img src=x onerror=bad()>'
        result = inline_text(text)
        self.assertIn('<strong>Wait for verification.</strong>', result)
        self.assertIn('&lt;img', result)
        self.assertNotIn('<img', result)
        self.assertEqual(paragraphs('**Evidence:** measured.\n\n**Limit:** one seed.'),
                         '<p><strong>Evidence:</strong> measured.</p><p><strong>Limit:</strong> one seed.</p>')
        self.assertEqual(inline_text('Unclosed **marker'), 'Unclosed **marker')

    def test_overview_preserves_the_full_claim_without_repeating_it_in_details(self):
        first = '**Decision:** ' + 'A complete claim. ' * 25 + 'The limitation stays visible.'
        state = {"title": "Task", "summary": "", "status": "working",
                 "results": first + "\n\nAdditional provenance.", "plan": "",
                 "next_action": "", "sections": [], "references": [], "revision": 1}
        body = task_body(state)
        self.assertIn('The limitation stays visible.', body)
        self.assertEqual(body.count('The limitation stays visible.'), 1)
        self.assertIn('<strong>Decision:</strong>', body)
        self.assertIn('<summary>Read the full progress update</summary><p>Additional provenance.</p>', body)

    def test_prose_comparison_does_not_bold_the_entire_first_column(self):
        table = {"title": "Wording", "caption": "Illustration.",
                 "columns": ["Before", "After"], "rows": [["Long explanation.", "**Wait.** Brief reason."]],
                 "row_headers": False}
        checked_patch({"tables": [table]})
        state = {"title": "Task", "summary": "", "status": "working",
                 "results": "", "plan": "", "next_action": "", "sections": [],
                 "references": [], "tables": [table], "revision": 1}
        body = task_body(state)
        self.assertIn('<td data-label="Before">Long explanation.</td>', body)
        self.assertIn('<strong>Wait.</strong>', body)
        with self.assertRaises(ValueError):
            checked_patch({"tables": [dict(table, row_headers="false")]})

    def test_semantic_enhancement_is_idempotent_and_preserves_runtime_code(self):
        script = '<script>const template="<table><tr><th>X</th></tr></table>";</script>'
        source = ('<html><style>p{color:black}</style><body><main><h1>Result</h1>'
                  '<table><tr><th>Policy</th><th>Success</th></tr>'
                  '<tr><td>A</td><td>Not measured</td></tr></table>'
                  '<p>Keep the source text.</p>' + script + '</main></body></html>')
        conversions = [{"match": "Keep the source text.", "table": {
            "title": "Source facts", "columns": ["Fact", "Value"],
            "rows": [["Literal markup", "<script>unsafe</script>"]],
            "caption": "Only author-supplied facts; no inference."}}]
        result = enhance_report(source, "http://127.0.0.1:9999/task/exact", "Exact task", conversions)
        self.assertEqual(enhance_report(result, "http://127.0.0.1:9999/task/exact",
                                        "Exact task", conversions), result)
        self.assertIn(script, result)
        self.assertIn('data-label="Success">Not measured', result)
        self.assertIn("&lt;script&gt;unsafe&lt;/script&gt;", result)
        self.assertEqual(result.count('class="clarity-task-return"'), 1)
        self.assertLess(result.index('<main>'), result.index('class="clarity-task-return"'))
        self.assertIn("<summary>Exact source wording</summary><p>Keep the source text.</p>", result)

    def test_changed_source_and_existing_return_link_are_retained(self):
        source = ('<main><p><a id="task-main-link" href="http://localhost:9000/exact">Return</a></p>'
                  '<h1>Result</h1><p>New measurement: 5.</p></main>')
        conversion = {"match": "Previous measurement: 3.", "table": {}}
        result = enhance_report(source, "http://localhost:9000/exact", conversions=[conversion])
        self.assertIn("New measurement: 5.", result)
        self.assertEqual(result.count('href="http://localhost:9000/exact"'), 1)
        self.assertNotIn('class="clarity-task-return"', result)

    def test_complex_table_is_not_flattened(self):
        source = '<main><table><tr><th colspan="2">Combined</th></tr><tr><td>A</td><td>B</td></tr></table></main>'
        result = enhance_report(source)
        self.assertNotIn("data-clarity-table", result.split('<main>')[1])

    def test_flows_are_safe_and_keep_order_and_unknowns_explicit(self):
        flow = {"title": "Proposed analysis", "caption": "Not measured; arrows show reading order.",
                "steps": [{"title": "<b>Evidence</b>", "text": "Recorded values"},
                          {"title": "Decision", "text": "Unknown until checks pass"}]}
        checked_patch({"flows": [flow]})
        state = {"title": "Task", "summary": "", "status": "planned", "flows": [flow],
                 "results": "", "plan": "", "next_action": "", "sections": [],
                 "references": [], "revision": 1}
        body = task_body(state)
        self.assertIn("&lt;b&gt;Evidence&lt;/b&gt;", body)
        self.assertLess(body.index("Recorded values"), body.index("Unknown until checks pass"))
        self.assertIn("Not measured; arrows show reading order.", body)
        for invalid in (dict(flow, steps=[]), dict(flow, steps=flow["steps"] * 3),
                        dict(flow, steps=[{"title": "A", "text": 2}, flow["steps"][1]])):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                checked_patch({"flows": [invalid]})
