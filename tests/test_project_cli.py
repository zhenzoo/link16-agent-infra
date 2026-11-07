"""Project maintenance decision guards; no online mutations."""
import importlib.util
import unittest
import tempfile
from pathlib import Path

spec = importlib.util.spec_from_file_location('project_cli', Path(__file__).resolve().parents[1] / 'feishu/project_cli.py')
project = importlib.util.module_from_spec(spec)
spec.loader.exec_module(project)


class ProjectGuards(unittest.TestCase):
    def setUp(self):
        self.config = {'space': 'space', 'iteration_id': 7, 'project_id': 8,
                       'project_field': 'project', 'iteration_field': 'iteration',
                       'advance_actions': {'planning': 'complete'}}

    def plan(self, *ops):
        return {'space': 'space', 'iteration_id': 7, 'authorization': 'User requested scope update', 'operations': list(ops)}

    def create(self):
        return {'key': 'parent', 'kind': 'create', 'reason': 'approved module',
                'fields': {'project': 8, 'iteration': [7], 'group_type': 'disabled'}}

    def test_wrong_iteration_rejected(self):
        plan = self.plan(); plan['iteration_id'] = 9
        with self.assertRaisesRegex(ValueError, 'another space or iteration'):
            project.validate(self.config, plan)

    def test_technical_handoff_cannot_be_completed(self):
        op = {'key': 'go', 'kind': 'advance', 'id': 10, 'node': 'technical', 'reason': 'too far'}
        with self.assertRaisesRegex(ValueError, 'outside authorized'):
            project.validate(self.config, self.plan(op))

    def test_child_before_parent_rejected(self):
        op = self.create(); op['parent_ref'] = 'missing'
        with self.assertRaisesRegex(ValueError, 'parent before child'):
            project.validate(self.config, self.plan(op))

    def test_group_creation_rejected(self):
        op = self.create(); op['fields']['group_type'] = 'enabled'
        with self.assertRaisesRegex(ValueError, 'chat groups'):
            project.validate(self.config, self.plan(op))

    def test_delete_without_expected_name_rejected(self):
        op = {'key': 'remove', 'kind': 'delete', 'id': 10, 'reason': 'removed from final scope'}
        with self.assertRaisesRegex(ValueError, 'exact expected name'):
            project.validate(self.config, self.plan(op))

    def test_same_display_name_does_not_hide_wrong_person(self):
        got = [{'role': 'tech_owner', 'owners': [{'username': 'wrong', 'name': '王冲'}]}]
        want = [{'role': 'tech_owner', 'owners': [{'username': 'right', 'name': '王冲'}]}]
        self.assertFalse(project.field_equal('role_owners', got, want))

    def test_missing_required_role_detected(self):
        self.assertFalse(project.field_equal('role_owners', [], [{'role': 'tech_owner', 'owners': [{'username': 'right'}]}]))

    def test_created_id_is_reused_after_interruption(self):
        state = {'created': {'parent': 123}}
        self.assertEqual(project.resolve({'kind': 'create', 'key': 'parent'}, state), 123)
        self.assertEqual(project.resolve({'kind': 'fields', 'ref': 'parent'}, state), 123)

    def test_unknown_create_reference_rejected(self):
        op = {'key': 'fill', 'kind': 'fields', 'ref': 'missing', 'reason': 'unknown item'}
        with self.assertRaisesRegex(ValueError, 'earlier create'):
            project.validate(self.config, self.plan(op))

    def test_second_writer_rejected_until_first_closes(self):
        with tempfile.TemporaryDirectory() as folder:
            first = project.acquire_batch_lock(Path(folder))
            try:
                with self.assertRaisesRegex(ValueError, 'Another process'):
                    project.acquire_batch_lock(Path(folder))
            finally:
                first.close()
            project.acquire_batch_lock(Path(folder)).close()

    def test_shared_iteration_item_cannot_be_deleted_globally(self):
        d = {'name': '6.2', 'project': 8, 'iteration': [7, 9]}
        op = {'key': 'remove', 'kind': 'delete', 'id': 10, 'expected_name': '6.2'}
        with self.assertRaisesRegex(ValueError, 'shared with another iteration'):
            project.apply_one(None, self.config, op, d, {'created': {}, 'journal': []})


if __name__ == '__main__':
    unittest.main()
