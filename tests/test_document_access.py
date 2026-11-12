import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'feishu'))
import document_access as access


POLICY = dict(owner_id='human', group_id='group', visibility='tenant')
BEFORE = dict(metadata=dict(owner_id='bot'), public=dict(external_access_entity='open',
    link_share_entity='anyone_readable', security_entity='only_full_access'), members=[])
AFTER = dict(metadata=dict(owner_id='human'), public=dict(external_access_entity='closed',
    link_share_entity='tenant_readable', security_entity='only_full_access'),
    members=[dict(member_type='openchat', member_id='group', perm='edit')])


class DocumentAccessTests(unittest.TestCase):
    def run_policy(self, before, after, apply=True):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        receipt = Path(temp.name) / 'receipt.json'
        reads = patch.object(access, 'snapshot', side_effect=[before, after]); reads.start(); self.addCleanup(reads.stop)
        writes = patch.object(access, 'request', return_value={'auth_result': True})
        request = writes.start(); self.addCleanup(writes.stop)
        result = access.enforce('token', 'resource', 'docx', POLICY, apply=apply,
            allowed_owner='bot', receipt=receipt)
        return result, json.loads(receipt.read_text()), request

    def test_order_preserves_location_creator_and_other_settings(self):
        result, journal, calls = self.run_policy(BEFORE, AFTER)
        self.assertTrue(result['policy_verified'])
        writes = [c for c in calls.call_args_list if c.args[1] != 'GET']
        self.assertEqual([c.args[1] for c in writes], ['PATCH', 'POST', 'POST'])
        self.assertEqual(writes[0].kwargs['body'], access.PRESETS['tenant'])
        self.assertEqual(writes[1].kwargs['body']['member_type'], 'openchat')
        self.assertEqual(writes[2].kwargs['params']['stay_put'], 'true')
        self.assertEqual(writes[2].kwargs['params']['old_owner_perm'], 'full_access')
        self.assertEqual(journal['status'], 'verified')

    def test_dry_run_never_writes_and_repeated_run_never_retransfers(self):
        result, journal, calls = self.run_policy(BEFORE, AFTER, apply=False)
        calls.assert_not_called()
        self.assertTrue(result['changes']['transfer_owner'])
        result, journal, calls = self.run_policy(AFTER, AFTER)
        calls.assert_not_called()
        self.assertTrue(result['policy_verified'])

    def test_unrelated_person_is_not_transferred(self):
        before = copy.deepcopy(BEFORE); before['metadata']['owner_id'] = 'colleague'
        with self.assertRaisesRegex(access.AccessPolicyError, '拒绝转移'):
            self.run_policy(before, AFTER)

    def test_failed_readback_is_not_success(self):
        with self.assertRaisesRegex(access.AccessPolicyError, '回读未全部通过'):
            self.run_policy(BEFORE, BEFORE)

    def test_personal_link_scope_is_separate_from_external_invitation_switch(self):
        personal = dict(POLICY, tenant_kind='personal')
        state = copy.deepcopy(AFTER);state['public']['external_access_entity']='open'
        self.assertTrue(access.verified(state,personal)['link'])
        self.assertFalse(access.verified(state,POLICY)['link'])
        state['public']['link_share_entity']='anyone_readable'
        self.assertFalse(access.verified(state,personal)['link'])

    def test_external_requires_explicit_evidence_before_identity_or_creation(self):
        with self.assertRaisesRegex(access.AccessPolicyError, 'external_reason'):
            access.resolve_policy('app', 'token', visibility='external')
        with self.assertRaises(access.AccessPolicyError):
            access.resolve_policy('app', 'token', visibility='none')

    def test_missing_claim_or_cross_tenant_group_fails_before_creation(self):
        agent = dict(name='test', tenant_key='tenant')
        with patch('registry.load_agents', return_value=[agent]), patch('send_feishu_msg._creds_for', return_value=('app','secret')), \
             patch('bot_names.local_name', return_value='test'), patch.object(access,'owner_claim', return_value=None):
            with self.assertRaisesRegex(access.AccessPolicyError,'缺认主'):
                access.resolve_policy('app','token')
        with patch('registry.load_agents', return_value=[agent]), patch('send_feishu_msg._creds_for', return_value=('app','secret')), \
             patch('bot_names.local_name', return_value='test'), patch.object(access,'owner_claim', return_value='human'), \
             patch('registry.load_registry', return_value={'tenants':[dict(tenant_key='tenant',group_chat_id='group')]}), \
             patch.object(access,'request', return_value=dict(tenant_key='wrong',external=False)):
            with self.assertRaisesRegex(access.AccessPolicyError,'跨租户'):
                access.resolve_policy('app','token')


if __name__ == '__main__':
    unittest.main()
