"""Checked Miaoda owner, tenant link access and group editing through lark-cli.

HTML pages use Drive apps permissions. Frontend/full_stack apps use Spark access
scope and collaborators. A finished release alone is never an access receipt.
"""
from datetime import datetime, timezone
import json
from pathlib import Path
import re

import document_access as access
import docio_cli
import feishu_rest
from send_feishu_msg import _creds_for


def call(profile, args):
    result = docio_cli.run_lark([*args, '--as', 'user'], profile=profile)
    payload = docio_cli._json_out(result)
    if result.returncode or payload.get('ok') is not True:
        raise access.AccessPolicyError(json.dumps(payload.get('error') or payload, ensure_ascii=False))
    return payload.get('data') or {}


class UserDrive:
    def __init__(self, profile):
        self.profile = profile

    def request(self, method, path, *, body=None, params=None):
        params = params or {}
        if path == '/drive/v1/metas/batch_query':
            return call(self.profile, ['drive', 'metas', 'batch_query', '--user-id-type', 'open_id', '--data', json.dumps(body)])
        match = re.fullmatch(r'/drive/v[12]/permissions/([A-Za-z0-9]+)/(.+)', path)
        if not match:
            raise access.AccessPolicyError('unsupported user permission route')
        resource, operation = match.groups()
        common = ['--token', resource, '--type', params['type']]
        if operation == 'public' and method == 'GET':
            return call(self.profile, ['drive', '+permission-get-setting', *common])
        if operation == 'members' and method == 'GET':
            return call(self.profile, ['drive', '+member-list', *common])
        if operation == 'members/auth':
            return call(self.profile, ['drive', 'permission.members', 'auth', *common, '--action', params['action']])
        if operation == 'members/transfer_owner':
            return call(self.profile, ['drive', 'permission.members', 'transfer_owner', *common,
                '--data', json.dumps(body), '--old-owner-perm', 'full_access',
                '--params', json.dumps({'stay_put':True,'need_notification':False,'remove_old_owner':False}), '--yes'])
        if operation == 'members' and method == 'POST' or operation.startswith('members/') and method == 'PUT':
            return call(self.profile, ['drive', '+member-add' if method == 'POST' else '+member-update', *common,
                '--member-type', body['member_type'], '--member-id', body['member_id'], '--perm', body['perm'], '--yes'])
        if operation == 'public' and method == 'PATCH':
            # The typed public.patch method is v1 and lacks apps / v2 fields.
            payload = call(self.profile, ['api', 'PATCH', '/open-apis'+path,
                '--params', json.dumps(params), '--data', json.dumps(body)])
            if 'code' in payload:
                if payload['code'] != 0:
                    raise access.AccessPolicyError(str(payload))
                return payload.get('data') or {}
            return payload
        raise access.AccessPolicyError('unsupported user permission operation')


def list_apps(profile):
    rows, cursor, seen = [], None, set()
    while True:
        args = ['apps', '+list', '--page-size', '100']
        if cursor: args += ['--page-token', cursor]
        data = call(profile, args)
        rows.extend(data.get('items') or [])
        if not data.get('has_more'): return rows
        cursor = data.get('page_token') or data.get('next_page_token')
        if not cursor or cursor in seen: raise access.AccessPolicyError('Miaoda app listing incomplete')
        seen.add(cursor)


def numeric_owner(profile, apps, owner_id, user_drive):
    """Prove the numeric Spark owner through one same-account HTML asset.

    This avoids guessing from a display name, creator_id or full_access role.
    If no such asset is available, use the official ID conversion endpoint.
    """
    for row in apps:
        match = re.search(r'/page/([A-Za-z0-9]+)', row.get('online_url') or '')
        if row.get('app_type') == 'html' and match:
            data = user_drive.request('POST', '/drive/v1/metas/batch_query',
                body={'request_docs':[{'doc_type':'apps','doc_token':match[1]}], 'with_url':True})
            if any(m.get('owner_id') == owner_id for m in data.get('metas') or []):
                return str(row['owner_id'])
    converted = call(profile, ['apps', '+user-id-convert', '--convert-type', 'open-id-to-miaoda', '--ids', owner_id])
    matches = [item for item in converted.get('items', [])
               if item.get('index') == 0 and item.get('source_id') == owner_id
               and re.fullmatch(r'\d+', str(item.get('target_id') or ''))]
    if converted.get('convert_type') == 'open-id-to-miaoda' and len(matches) == 1 and not converted.get('missed'):
        return str(matches[0]['target_id'])
    raise access.AccessPolicyError('官方 ID 转换未唯一命中已认主人，不能核 owner')


def group_edit(members, group):
    return any(m.get('member_type') in ('chat','openchat') and m.get('member_id') == group
        and (m.get('role') or m.get('perm')) in ('edit','full_access') for m in members)


def govern(app_id, profile=None, *, apply=False, external_reason='', receipt=None):
    if not re.fullmatch(r'app_[A-Za-z0-9]+', app_id): raise ValueError('invalid Miaoda app ID')
    profile, _ = docio_cli.user_read_profile(profile or 'auto')
    app, secret = _creds_for(profile)
    policy = access.resolve_policy(app, feishu_rest.tenant_token(app, secret),
        visibility='external' if external_reason else 'tenant', external_reason=external_reason)
    status = docio_cli._json_out(docio_cli.run_lark(['auth','status'],profile=profile))
    logged = (status.get('identities') or {}).get('user') or {}
    if logged.get('openId') != policy['owner_id']:
        raise access.AccessPolicyError('当前妙搭登录用户不是已认主人，拒绝继续')
    info = call(profile, ['apps','+get','--app-id',app_id])['app']
    user_drive = UserDrive(profile)
    if info['app_type'] == 'html':
        result = access.enforce(user_drive, info['meta_token'], 'apps', policy, apply=apply, receipt=receipt)
        return dict(result, artifact_type='miaoda_access', app_id=app_id, app_type='html',
                    status='verified' if result['policy_verified'] else 'not_verified')
    if info['app_type'] not in ('frontend','full_stack'):
        raise access.AccessPolicyError('unsupported Miaoda app type')
    apps = list_apps(profile)
    target = next((a for a in apps if a['app_id'] == app_id), None)
    owner = numeric_owner(profile, apps, policy['owner_id'], user_drive)
    if not target or str(target.get('owner_id')) != owner:
        raise access.AccessPolicyError('妙搭 owner 不是主人；当前 CLI 不支持此类应用转移，不能用 full_access 冒充 owner')
    scope = call(profile, ['apps','+access-scope-get','--app-id',app_id])
    members = call(profile, ['apps','+member-list','--app-id',app_id]).get('items') or []
    wanted = 'All' if external_reason else 'Tenant'
    checks = dict(owner=True, link=scope.get('scope') == wanted, group_edit=group_edit(members,policy['group_id']))
    receipt = Path(receipt) if receipt else access.journal_path(app_id)
    if receipt.exists(): raise access.AccessPolicyError('保留已有回执；请用新路径核验同一应用')
    record = dict(artifact_type='miaoda_access',app_id=app_id,app_type=info['app_type'],policy=policy,
        before=dict(owner_id=owner,scope=scope,members=members),checks=checks,steps=[],status='prepared',
        created_at=datetime.now(timezone.utc).isoformat())
    def save():
        receipt.parent.mkdir(parents=True,exist_ok=True)
        receipt.write_text(json.dumps(record,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    def change(name,args):
        record['steps'].append(dict(action=name,status='started'));save()
        call(profile,args)
        record['steps'][-1]['status']='returned_success';save()
    save()
    try:
        if apply:
            if not checks['link']:
                change('link_scope',['apps','+access-scope-set','--app-id',app_id,'--scope',
                    'public' if external_reason else 'tenant', *(['--require-login'] if external_reason else [])])
            if not checks['group_edit']:
                existing=any(m.get('member_id')==policy['group_id'] for m in members)
                change('group_edit',['apps','+member-update' if existing else '+member-add','--app-id',app_id,
                    '--member-type','openchat','--member-id',policy['group_id'],'--perm','edit','--yes'])
            after_scope=call(profile,['apps','+access-scope-get','--app-id',app_id])
            after_members=call(profile,['apps','+member-list','--app-id',app_id]).get('items') or []
            after_app=next((a for a in list_apps(profile) if a['app_id']==app_id),{})
            checks=dict(owner=str(after_app.get('owner_id'))==owner,link=after_scope.get('scope')==wanted,
                group_edit=group_edit(after_members,policy['group_id']))
            record['after']=dict(owner_id=after_app.get('owner_id'),scope=after_scope,members=after_members)
            if not all(checks.values()): raise access.AccessPolicyError('妙搭 owner/link/group 回读未全部通过')
        record.update(status='verified' if all(checks.values()) else 'not_verified',checks=checks);save()
        return dict(artifact_type='miaoda_access',app_id=app_id,app_type=info['app_type'],
            status=record['status'],policy_verified=all(checks.values()),checks=checks,receipt=str(receipt))
    except Exception as exc:
        record.update(status='incomplete',error=str(exc));save()
        raise access.AccessPolicyError(f'妙搭权限未完成；应用 {app_id}；回执 {receipt}；{exc}') from exc
