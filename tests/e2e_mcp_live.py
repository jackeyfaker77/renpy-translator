# -*- coding: utf-8 -*-
"""MCP 端到端完整测试：对运行中的服务（默认 7900）。

用法: PORT=7900 .venv/Scripts/python.exe tests/e2e_mcp_live.py
不写 pytest（需真实服务与真实项目数据）。
"""
import json
import os
import sys
import uuid
import urllib.error
import urllib.request

BASE = f'http://127.0.0.1:{os.environ.get("PORT", "7900")}'
PASS: list[str] = []
FAIL: list[str] = []


def http(method, path, body=None, headers=None, timeout=90):
    h = {'content-type': 'application/json'}
    if headers:
        h.update(headers)
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method, headers=h)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            try:
                return r.status, (json.loads(raw) if raw else None), dict(r.headers)
            except ValueError:
                return r.status, raw.decode('utf-8', 'replace'), dict(r.headers)
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, (json.loads(raw) if raw else None), dict(e.headers)
        except ValueError:
            return e.code, raw.decode('utf-8', 'replace')[:300], dict(e.headers)


def ok(name, cond, detail=''):
    if cond:
        PASS.append(name)
        print(f'  PASS  {name}' + (f'  [{detail}]' if detail else ''))
    else:
        FAIL.append(name)
        print(f'  FAIL  {name}  {detail}')


def mcp_call(sid, method, params=None, id_=1, extra_headers=None):
    h = {
        'content-type': 'application/json',
        'accept': 'application/json, text/event-stream',
    }
    if sid:
        h['mcp-session-id'] = sid
    if extra_headers:
        h.update(extra_headers)
    body = {'jsonrpc': '2.0', 'id': id_, 'method': method}
    if params is not None:
        body['params'] = params
    status, data, headers = http('POST', '/mcp/', body, h, timeout=90)
    sid_out = (headers.get('mcp-session-id') or headers.get('Mcp-Session-Id')
               or sid)
    if isinstance(data, str) and 'data:' in data:
        for line in data.splitlines():
            if line.startswith('data: '):
                data = json.loads(line[6:])
                break
    return status, sid_out, data


def tool(sid, name, args=None, id_=1):
    status, sid, data = mcp_call(sid, 'tools/call',
                                 {'name': name, 'arguments': args or {}},
                                 id_=id_)
    res = (data or {}).get('result') or {}
    is_err = bool(res.get('isError'))
    text = ''
    if res.get('content'):
        text = res['content'][0].get('text', '')
    payload = None
    if text and not is_err:
        try:
            payload = json.loads(text)
        except ValueError:
            payload = text
    return is_err, payload, text, sid


def main():
    print(f'目标: {BASE}')
    print('== 0. 安全：未启用 / token / Origin ==')
    status, data, _ = http('GET', '/api/settings/mcp')
    ok('mcp_settings_readable',
       status == 200 and 'enabled' in data, str(data)[:80])
    was_enabled = data.get('enabled')
    was_mode = data.get('tool_mode')

    http('PUT', '/api/settings/mcp', {'enabled': False})
    h = {'content-type': 'application/json',
         'accept': 'application/json, text/event-stream'}
    status, body, _ = http('POST', '/mcp/', {
        'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
        'params': {'protocolVersion': '2025-06-18', 'capabilities': {},
                   'clientInfo': {'name': 'e2e', 'version': '0'}}}, h)
    ok('disabled_404',
       status == 404 and body.get('error', {}).get('code') == 'MCP_DISABLED',
       f'status={status}')
    # 未启用时 GET /mcp 也应 404（中间件先于 307 路由）
    class NoRedir(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None
    opener = urllib.request.build_opener(NoRedir)
    try:
        opener.open(urllib.request.Request(BASE + '/mcp', method='GET'), timeout=5)
        redir_status = 200
    except urllib.error.HTTPError as e:
        redir_status = e.code
    ok('disabled_get_mcp_404', redir_status == 404, f'status={redir_status}')

    status, data, _ = http('PUT', '/api/settings/mcp',
                           {'enabled': True, 'tool_mode': 'curated'})
    ok('enable_mcp', status == 200 and data['enabled'] is True)

    # 启用后无尾斜杠 → 307
    try:
        opener.open(urllib.request.Request(BASE + '/mcp', method='GET'), timeout=5)
        redir_status = 200
    except urllib.error.HTTPError as e:
        redir_status = e.code
    ok('slash_redirect_307', redir_status == 307, f'status={redir_status}')
    status, tok_body, _ = http('POST', '/api/settings/mcp/token')
    token = tok_body['token']
    ok('token_generated', status == 200 and len(token) >= 32)

    init_body = {
        'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
        'params': {'protocolVersion': '2025-06-18', 'capabilities': {},
                   'clientInfo': {'name': 'e2e', 'version': '0'}},
    }
    status, _, _ = http('POST', '/mcp/', init_body, h)
    ok('no_token_401', status == 401, f'status={status}')
    status, _, _ = http('POST', '/mcp/', init_body,
                        {**h, 'authorization': 'Bearer wrong'})
    ok('bad_token_401', status == 401, f'status={status}')
    status, _, _ = http('POST', '/mcp/', init_body,
                        {**h, 'authorization': f'Bearer {token}',
                         'origin': 'http://evil.test'})
    ok('evil_origin_403', status == 403, f'status={status}')
    status, health, _ = http('GET', '/api/health')
    ok('api_unaffected', status == 200 and health.get('ok') is True)
    http('DELETE', '/api/settings/mcp/token')
    status, data, _ = http('GET', '/api/settings/mcp')
    ok('token_cleared', data['has_token'] is False)

    print('== 1. 握手与会话 ==')
    status, sid, init = mcp_call(None, 'initialize', init_body['params'])
    ok('initialize_200', status == 200 and init.get('result', {}).get('serverInfo'),
       f"server={init.get('result', {}).get('serverInfo')}")
    ok('session_id', bool(sid), sid or '')
    instr = init.get('result', {}).get('instructions', '')
    ok('instructions_cn', 'wait_job' in instr and 'confirm' in instr)
    h2 = {'content-type': 'application/json',
          'accept': 'application/json, text/event-stream',
          'mcp-session-id': sid}
    status, _, _ = http('POST', '/mcp/',
                        {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
                        h2)
    ok('initialized_notify', status in (200, 202), f'status={status}')

    print('== 2. 精选工具列表 ==')
    status, sid, tools_data = mcp_call(sid, 'tools/list', {}, id_=2)
    tools = tools_data.get('result', {}).get('tools', [])
    names = [t['name'] for t in tools]
    ok('curated_count_30', len(names) == 30, f'count={len(names)}')
    expected = [
        'get_session', 'list_projects', 'open_project', 'create_project',
        'delete_project', 'list_texts', 'translate_text',
        'start_translate_texts', 'update_text', 'retry_failed_texts',
        'list_names', 'start_translate_names', 'list_glossary',
        'add_glossary_term', 'start_embedded_scan', 'embedded_refine',
        'start_graph_build', 'get_relations', 'start_export_game',
        'export_texts', 'import_texts_from_project', 'list_scheduled_tasks',
        'run_scheduled_task', 'wait_job', 'answer_job', 'cancel_job',
        'get_logs', 'list_model_configs', 'list_prompts', 'save_prompt',
    ]
    missing = set(expected) - set(names)
    ok('all_expected_present', not missing, f'missing={missing}')
    ok('tools_have_schema',
       all(('inputSchema' in t or 'input_schema' in t) for t in tools),
       f'keys sample={list(tools[0].keys())}')

    print('== 3. 逐个调用精选工具 ==')
    tid = [10]

    def T(name, args=None):
        tid[0] += 1
        is_err, payload, text, _sid = tool(sid, name, args, id_=tid[0])
        return is_err, payload, text

    # 只读
    is_err, p, t = T('get_session')
    ok('call_get_session', not is_err and isinstance(p, dict)
       and p.get('current_project'),
       f"project={p.get('current_project') if isinstance(p, dict) else '?'}")

    is_err, p, t = T('list_projects')
    n_proj = len(p.get('items', [])) if isinstance(p, dict) else (
        len(p) if isinstance(p, list) else 0)
    ok('call_list_projects', not is_err and n_proj >= 1,
       f'n={n_proj} type={type(p).__name__}')

    is_err, p, t = T('list_names')
    ok('call_list_names', not is_err and isinstance(p, dict) and 'rows' in p,
       f"total={p.get('total') if isinstance(p, dict) else t[:80]}")

    is_err, p, t = T('list_glossary', {'page': 1, 'size': 5})
    ok('call_list_glossary', not is_err and isinstance(p, dict),
       f'keys={list(p.keys())[:6] if isinstance(p, dict) else t[:80]}')

    is_err, p, t = T('list_texts', {'content_type': 'dialogue', 'page': 1,
                                    'size': 3})
    ok('call_list_texts_dialogue', not is_err and isinstance(p, dict),
       f'keys={list(p.keys())[:8] if isinstance(p, dict) else t[:100]}')

    is_err, p, t = T('list_texts', {'content_type': 'ui', 'page': 1, 'size': 3})
    ok('call_list_texts_ui', not is_err and isinstance(p, dict))

    is_err, p, t = T('get_relations')
    ok('call_get_relations', not is_err, t[:100] if is_err else 'ok')

    is_err, p, t = T('list_scheduled_tasks')
    ok('call_list_scheduled_tasks', not is_err and isinstance(p, dict)
       and 'tasks' in p,
       f"n={len(p.get('tasks', [])) if isinstance(p, dict) else '?'}")

    is_err, p, t = T('list_model_configs')
    n_cfg = len(p.get('items', [])) if isinstance(p, dict) else (
        len(p) if isinstance(p, list) else 0)
    ok('call_list_model_configs', not is_err and n_cfg >= 1,
       f'n={n_cfg} type={type(p).__name__}')

    is_err, p, t = T('list_prompts')
    ok('call_list_prompts', not is_err and p is not None,
       f'type={type(p).__name__}')

    is_err, p, t = T('get_logs', {'limit': 5})
    ok('call_get_logs', not is_err and isinstance(p, dict) and 'log' in p)

    # 危险守卫
    is_err, p, t = T('delete_project', {'name': 'Motherless-0.26.1.1-market'})
    ok('delete_no_confirm_blocked', is_err and 'confirm' in t, t[:100])

    # glossary 写入（小、可清理）
    en = f'e2e_mcp_{uuid.uuid4().hex[:6]}'
    is_err, p, t = T('add_glossary_term',
                     {'en_term': en, 'cn_term': '测试术语', 'term_type': 'term'})
    ok('call_add_glossary', not is_err, t[:120])
    is_err, p, t = T('list_glossary', {'search': en, 'size': 5, 'page': 0})
    blob = json.dumps(p, ensure_ascii=False) if p is not None else t
    ok('glossary_roundtrip', not is_err and en in blob, t[:100])

    # 导出文本（生成文件）
    is_err, p, t = T('export_texts',
                     {'types': ['names'], 'format': 'json',
                      'filename': 'e2e_mcp_test'})
    ok('call_export_texts', not is_err and isinstance(p, dict) and p.get('file'),
       f"file={p.get('file') if isinstance(p, dict) else t[:120]}")
    if isinstance(p, dict):
        ok('export_has_abs_path',
           bool(p.get('path')) and len(str(p.get('path'))) > 5,
           str(p.get('path'))[:80])
        ok('export_has_abs_url',
           str(p.get('download_url', '')).startswith('http'),
           str(p.get('download_url'))[:80])
        ok('export_file_exists', os.path.isfile(p.get('path', '')),
           p.get('path', ''))

    is_err, p, t = T('wait_job', {'job_id': 'nonexistent00', 'timeout_seconds': 0})
    ok('wait_job_404', is_err and 'JOB_NOT_FOUND' in t, t[:100])
    is_err, p, t = T('cancel_job', {'job_id': 'nonexistent00'})
    ok('cancel_job_404', is_err, t[:80])
    is_err, p, t = T('answer_job',
                     {'job_id': 'nonexistent00', 'question_id': 'q',
                      'answer': {}})
    ok('answer_job_404', is_err, t[:80])

    print('== 4. 定时任务经 MCP 全链路 ==')
    status, sched, _ = http('POST', '/api/scheduled', {
        'name': 'e2e-mcp-测试任务', 'kind': 'texts.failed-retry.dialogue',
        'project': 'Motherless-0.26.1.1-market',
        'params': {}, 'schedule_type': 'delay',
        'schedule': {'minutes': 60}, 'repeat_policy': 'once',
    })
    ok('create_scheduled_via_rest', status == 200 and sched.get('id'),
       str(sched)[:80])
    task_id = sched.get('id')

    is_err, p, t = T('list_scheduled_tasks')
    ok('scheduled_visible_in_mcp', not is_err and any(
        x.get('id') == task_id for x in (p or {}).get('tasks', [])),
       f"n={len((p or {}).get('tasks', []))}")

    is_err, p, t = T('run_scheduled_task', {'task_id': task_id})
    ok('run_scheduled_now', not is_err and (
        (isinstance(p, dict) and (p.get('job_id') or p.get('message')))
        or t), f'p={p} t={t[:100]}')
    job_id = (p or {}).get('job_id') if isinstance(p, dict) else None
    if job_id:
        is_err, p, t = T('wait_job', {'job_id': job_id, 'timeout_seconds': 20})
        ok('wait_job_returns', not is_err and isinstance(p, dict)
           and 'status' in p,
           f"status={p.get('status') if isinstance(p, dict) else t[:100]}")
        ok('wait_job_shape', not is_err and (
            p.get('status') in ('succeeded', 'failed', 'running',
                                'waiting_input')
            or p.get('timed_out')),
           f"status={p.get('status')} err={p.get('error')}")
    http('DELETE', f'/api/scheduled/{task_id}')
    ok('cleanup_scheduled', True)

    print('== 5. 单条真实翻译（1 条） ==')
    status, page, _ = http(
        'GET', '/api/current/texts/dialogue?page=1&size=5&filter_mode=untranslated')
    rows = []
    if isinstance(page, dict):
        for key in ('rows', 'items', 'list', 'data'):
            if isinstance(page.get(key), list):
                rows = page[key]
                break
        if not rows:
            for v in page.values():
                if isinstance(v, list) and v and isinstance(v[0], dict):
                    rows = v
                    break
    ok('untranslated_rows_available', len(rows) > 0,
       f'n={len(rows)} keys={list(page.keys())[:8] if isinstance(page, dict) else "?"}')
    if rows:
        item = rows[0]
        item_id = item.get('id') or item.get('item_id')
        is_err, p, t = T('translate_text',
                         {'content_type': 'dialogue', 'item_id': str(item_id)})
        ok('call_translate_text', not is_err, t[:150])
        if not is_err and isinstance(p, dict):
            ok('translate_has_text',
               bool(p.get('translated_text') or p.get('translated')
                    or p.get('cn') or p.get('text')),
               f'keys={list(p.keys())[:12]}')

    print('== 6. 全量模式 ==')
    status, data, _ = http('PUT', '/api/settings/mcp', {'tool_mode': 'full'})
    ok('switch_full', data.get('tool_mode') == 'full')

    status, sid, tools_data = mcp_call(sid, 'tools/list', {}, id_=200)
    full_names = [t['name'] for t in tools_data.get('result', {}).get('tools', [])]
    ok('full_has_api_get_session', 'api_get_session' in full_names)
    ok('full_has_wait_job', 'wait_job' in full_names)
    ok('full_not_curated_get_session', 'get_session' not in full_names)
    ok('full_no_sse_tools',
       not any(n.endswith('events') for n in full_names),
       f"events={[n for n in full_names if 'events' in n][:3]}")
    ok('full_count_reasonable', 50 <= len(full_names) <= 250,
       f'n={len(full_names)}')

    is_err, p, t = T('api_delete_projects_name', {'name': 'x'})
    ok('full_delete_no_confirm', is_err, t[:120])

    is_err, p, t = T('api_delete_projects_name',
                     {'name': 'e2e_no_such_project_xyz', 'confirm': True})
    ok('full_delete_missing_project', is_err, t[:120])

    is_err, p, t = T('api_get_session', {})
    ok('full_api_get_session_call', not is_err and isinstance(p, dict)
       and 'current_project' in p, t[:100])

    is_err, p, t = T('api_get_projects', {})
    n_proj2 = len(p.get('items', [])) if isinstance(p, dict) else (
        len(p) if isinstance(p, list) else 0)
    ok('full_api_get_projects', not is_err and n_proj2 >= 1,
       f'n={n_proj2} type={type(p).__name__}')

    is_err, p, t = T('api_get_current_texts_content_type',
                     {'content_type': 'dialogue', 'page': 0, 'size': 2})
    ok('full_api_list_texts', not is_err and isinstance(p, dict), t[:100])

    print('== 7. 恢复现场 ==')
    http('PUT', '/api/settings/mcp',
         {'enabled': bool(was_enabled), 'tool_mode': was_mode or 'curated'})
    ok('restore_settings', True, f'enabled={was_enabled} mode={was_mode}')

    print()
    print(f'======== 结果: {len(PASS)} passed, {len(FAIL)} failed ========')
    if FAIL:
        print('失败项:')
        for name in FAIL:
            print(' -', name)
        sys.exit(1)
    print('全部通过')


if __name__ == '__main__':
    main()
