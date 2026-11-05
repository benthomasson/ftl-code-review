"""Tests for concept extraction from diffs and concept-based belief gathering."""

from ftl_code_review.observations import extract_concepts_from_diff


class TestExtractConceptsFromDiff:
    """extract_concepts_from_diff parses identifiers from diff + lines."""

    def test_python_function_def(self):
        diff = """\
+++ b/src/validator.py
+def validate_variable_references(activities, edges, context):
+    pass
"""
        concepts = extract_concepts_from_diff(diff)
        assert "validate variable references" in concepts

    def test_python_class_def(self):
        diff = """\
+++ b/src/models.py
+class WorkflowValidator:
+    pass
"""
        concepts = extract_concepts_from_diff(diff)
        assert "workflow validator" in concepts

    def test_python_async_def(self):
        diff = """\
+++ b/src/engine.py
+    async def handle_loop_completion(self, node_id):
+        pass
"""
        concepts = extract_concepts_from_diff(diff)
        assert "handle loop completion" in concepts

    def test_python_import(self):
        diff = """\
+++ b/src/cli.py
+from syntara.workflows.validators.template_expressions import check_template_expressions
"""
        concepts = extract_concepts_from_diff(diff)
        assert "check template expressions" in concepts
        assert any("syntara" in c for c in concepts)
        assert any("validators" in c for c in concepts)

    def test_python_import_multiple(self):
        diff = """\
+++ b/src/models.py
+from syntara.workflows.models.validation_finding import ValidationCategory, ValidationSeverity
"""
        concepts = extract_concepts_from_diff(diff)
        assert "validation category" in concepts
        assert "validation severity" in concepts

    def test_typescript_function(self):
        diff = """\
+++ b/src/utils.ts
+function getLoopBodyIds(activities: Activity[], edges: EdgeConnection[]): Set<string> {
"""
        concepts = extract_concepts_from_diff(diff)
        assert "get loop body ids" in concepts or "getLoopBodyIds" in concepts.union({"get loop body ids"})

    def test_typescript_export_function(self):
        diff = """\
+++ b/src/validate.ts
+export function validateVariableReferences(activities, edges, context) {
"""
        concepts = extract_concepts_from_diff(diff)
        assert "validate variable references" in concepts

    def test_typescript_const_function(self):
        diff = """\
+++ b/src/utils.ts
+export const getUpstreamNodeIds = (nodeId: string, edges: EdgeConnection[]) => {
"""
        concepts = extract_concepts_from_diff(diff)
        assert "get upstream node ids" in concepts

    def test_typescript_type_def(self):
        diff = """\
+++ b/src/types.ts
+export interface EdgeConnection {
"""
        concepts = extract_concepts_from_diff(diff)
        assert "edge connection" in concepts

    def test_typescript_enum(self):
        diff = """\
+++ b/src/types.ts
+export enum ActivityTypeEnum {
"""
        concepts = extract_concepts_from_diff(diff)
        assert "activity type enum" in concepts

    def test_typescript_import(self):
        diff = """\
+++ b/src/validate.ts
+import { ActivityTypeEnum, EdgeHandleEnum, type Activity } from '@syntara/contracts'
"""
        concepts = extract_concepts_from_diff(diff)
        assert "activity type enum" in concepts
        assert "edge handle enum" in concepts

    def test_ignores_non_added_lines(self):
        diff = """\
+++ b/src/foo.py
-def old_function():
 def unchanged_function():
+def new_function():
"""
        concepts = extract_concepts_from_diff(diff)
        assert "new function" in concepts
        assert "old function" not in concepts
        assert "unchanged function" not in concepts

    def test_ignores_stop_words(self):
        diff = """\
+++ b/src/foo.py
+def __init__(self):
+    pass
"""
        concepts = extract_concepts_from_diff(diff)
        # __init__ and self are stop words
        assert not any("init" == c for c in concepts)

    def test_ignores_short_names(self):
        diff = """\
+++ b/src/foo.py
+def fn():
+    x = 1
"""
        concepts = extract_concepts_from_diff(diff)
        assert "fn" not in concepts

    def test_camel_case_splitting(self):
        diff = """\
+++ b/src/foo.ts
+function getLoopBodyIds() {
"""
        concepts = extract_concepts_from_diff(diff)
        # Should produce "get loop body ids" from camelCase
        assert any("loop" in c and "body" in c for c in concepts)

    def test_real_pr_1182_diff(self):
        """Concepts extracted from the actual PR #1182 diff."""
        diff = """\
+++ b/frontend/packages/syntara-ui/src/routes/builder/utils/validation/rules/validateVariableReferences.ts
+import { ActivityTypeEnum, EdgeHandleEnum, type Activity } from '@syntara/contracts'
+const KNOWN_NAMESPACES = new Set(['trigger', 'workflow_context', 'loop'])
+function getLoopBodyIds(activities: Activity[], edges: EdgeConnection[]): Set<string> {
+  const loopIds = new Set(
+    activities.filter((activity) => activity.type === ActivityTypeEnum.LOOP).map((loop) => loop.id)
+  )
+  const bodyIds = new Set<string>()
+  if (ref.namespace === 'loop') {
+    if (ctx.loopBodyIds.has(activity.id)) return null
"""
        concepts = extract_concepts_from_diff(diff)
        # Should find the key concepts a belief search would need
        assert any("loop" in c and "body" in c for c in concepts)
        assert "activity type enum" in concepts
        assert "edge handle enum" in concepts

    def test_empty_diff(self):
        assert extract_concepts_from_diff("") == set()

    def test_diff_with_only_deletions(self):
        diff = """\
+++ b/src/foo.py
-def removed_function():
-    pass
"""
        concepts = extract_concepts_from_diff(diff)
        assert "removed function" not in concepts
