// Maintained stateful REST and delayed-command response boundaries.
// default.js preserves the original CLI-generated replay for comparison.
// Control routes are test harness utilities, not Mattermost API endpoints.
const http = require('http');

const send = (res, status, body, headers = {}) => {
  res.writeHead(status, {'Content-Type': 'application/json', ...headers});
  res.end(JSON.stringify(body));
};

http.createServer((req, res) => {
  (async () => {
    const path = new URL(req.url, 'http://localhost').pathname;
    if (path === '/__control/reset' && req.method === 'POST') {
      await pm.state.set('mattermost.posts', []);
      await pm.state.set('mattermost.calls', []);
      await pm.state.set('mattermost.delayed', []);
      await pm.state.set('mattermost.dialogs', []);
      await pm.state.set('mattermost.commands', []);
      await pm.state.set('mattermost.scenario', req.body.scenario || 'normal');
      return send(res, 200, {ok: true});
    }
    if (path === '/__control/state' && req.method === 'GET') {
      return send(res, 200, {
        posts: await pm.state.get('mattermost.posts') || [],
        calls: await pm.state.get('mattermost.calls') || [],
        delayed: await pm.state.get('mattermost.delayed') || [],
        dialogs: await pm.state.get('mattermost.dialogs') || [],
        commands: await pm.state.get('mattermost.commands') || [],
      });
    }
    if (path === '/__control/scenario' && req.method === 'POST') {
      await pm.state.set('mattermost.scenario', req.body.scenario);
      return send(res, 200, {ok: true});
    }
    if (path === '/api/v4/commands' || path.startsWith('/api/v4/commands/')) {
      const calls = await pm.state.get('mattermost.calls') || [];
      calls.push({method: req.method, path, authorization: Boolean(req.headers.authorization), cookie: Boolean(req.headers.cookie)});
      await pm.state.set('mattermost.calls', calls);
      if (req.headers.authorization !== 'Bearer fixture-setup-secret') return send(res, 401, {id: 'fixture.invalid_setup'});
      const scenario = await pm.state.get('mattermost.scenario') || 'normal';
      if (scenario === 'forbidden') return send(res, 403, {id: 'fixture.command_permissions'});
      if (scenario === 'rate-limit') return send(res, 429, {id: 'fixture.command_limit'}, {'Retry-After': '2'});
      if (scenario === 'redirect') return send(res, 302, {}, {'Location': '/__control/must-not-follow'});
      let commands = await pm.state.get('mattermost.commands') || [];
      const identifier = path.split('/')[4];
      if (req.method === 'GET') {
        if (path === '/api/v4/commands') return send(res, 200, [{trigger: 'builtin'}, ...commands]);
        const found = commands.find(c => c.id === identifier);
        return found ? send(res, 200, found) : send(res, 404, {id: 'fixture.command_not_found'});
      }
      let result;
      let status = 200;
      if (req.method === 'POST' && path === '/api/v4/commands') {
        const body = req.body;
        if (!body || !body.team_id || typeof body.trigger !== 'string' || !['P', 'G'].includes(body.method) || typeof body.url !== 'string') return send(res, 400, {id: 'fixture.invalid_command'});
        if (commands.some(c => c.team_id === body.team_id && c.trigger === body.trigger)) return send(res, 400, {id: 'fixture.trigger_exists'});
        // Mock-only IDs/secrets. Native acceptance is established separately.
        result = {...body, id: 'fixturecommand', token: 'fixture-issued-command', creator_id: 'fixture-setup'};
        commands.push(result);
        status = 201;
      } else if (req.method === 'PUT' && identifier) {
        const previous = commands.find(c => c.id === identifier);
        if (!previous) return send(res, 404, {id: 'fixture.command_not_found'});
        if (req.body.id !== identifier || req.body.team_id !== previous.team_id) return send(res, 400, {id: 'fixture.command_mismatch'});
        result = {...previous, ...req.body};
        commands = commands.map(c => c.id === identifier ? result : c);
      } else if (req.method === 'DELETE' && identifier) {
        if (!commands.some(c => c.id === identifier)) return send(res, 404, {id: 'fixture.command_not_found'});
        commands = commands.filter(c => c.id !== identifier);
        result = {status: 'OK'};
      } else {
        return send(res, 501, {id: 'fixture.command_method_not_modeled'});
      }
      await pm.state.set('mattermost.commands', commands);
      if (scenario === 'write-then-delay') await new Promise(resolve => setTimeout(resolve, 1500));
      return send(res, status, result);
    }
    if (path === '/hooks/commands/fixture-response' && req.method === 'POST') {
      const calls = await pm.state.get('mattermost.calls') || [];
      calls.push({method: req.method, path, authorization: Boolean(req.headers.authorization), cookie: Boolean(req.headers.cookie)});
      await pm.state.set('mattermost.calls', calls);
      if (req.headers.authorization || req.headers.cookie) return send(res, 400, {id: 'fixture.credential_leak'});
      const scenario = await pm.state.get('mattermost.scenario') || 'normal';
      if (scenario === 'unauthorized') return send(res, 401, {id: 'fixture.expired_hook'});
      if (scenario === 'forbidden') return send(res, 403, {id: 'fixture.forbidden_hook'});
      if (scenario === 'rate-limit') return send(res, 429, {id: 'fixture.hook_limit'}, {'Retry-After': '2'});
      if (scenario === 'redirect') return send(res, 302, {}, {'Location': '/__control/redirect-must-not-be-followed'});
      const delayed = await pm.state.get('mattermost.delayed') || [];
      delayed.push(req.body);
      await pm.state.set('mattermost.delayed', delayed);
      if (scenario === 'write-then-delay') await new Promise(resolve => setTimeout(resolve, 1500));
      return send(res, 200, {});
    }
    if (!['/api/v4/users/me', '/api/v4/posts', '/api/v4/actions/dialogs/open'].includes(path)) {
      return send(res, 501, {id: 'fixture.endpoint_not_modeled'});
    }
    const calls = await pm.state.get('mattermost.calls') || [];
    calls.push({method: req.method, path});
    await pm.state.set('mattermost.calls', calls);
    if (req.headers.authorization !== 'Bearer fixture-secret') {
      return send(res, 401, {id: 'api.context.invalid_token', request_id: 'fixture-401'});
    }
    const scenario = await pm.state.get('mattermost.scenario') || 'normal';
    if (req.method === 'GET' && path === '/api/v4/users/me') {
      return send(res, 200, {id: 'fixture-bot', username: 'fixture'});
    }
    if (req.method === 'POST' && path === '/api/v4/actions/dialogs/open') {
      if (scenario === 'forbidden') return send(res, 403, {id: 'fixture.dialog.permissions'});
      if (scenario === 'rate-limit') return send(res, 429, {id: 'fixture.dialog.rate_limit'}, {'Retry-After': '2'});
      if (scenario === 'redirect') return send(res, 302, {}, {'Location': '/__control/must-not-follow'});
      const body = req.body;
      if (!body || typeof body.trigger_id !== 'string' || !body.trigger_id || typeof body.url !== 'string' || !body.dialog || typeof body.dialog.title !== 'string' || !Array.isArray(body.dialog.elements)) {
        return send(res, 400, {id: 'fixture.dialog.invalid_request'});
      }
      const dialogs = await pm.state.get('mattermost.dialogs') || [];
      dialogs.push(body);
      await pm.state.set('mattermost.dialogs', dialogs);
      if (scenario === 'write-then-delay') await new Promise(resolve => setTimeout(resolve, 1500));
      return send(res, 200, {status: 'OK'});
    }
    if (req.method !== 'POST' || path !== '/api/v4/posts') {
      return send(res, 501, {id: 'fixture.method_not_modeled'});
    }
    if (scenario === 'forbidden') return send(res, 403, {id: 'api.post.permissions'});
    if (scenario === 'rate-limit') return send(res, 429, {id: 'api.rate_limit'}, {'Retry-After': '2'});
    const body = req.body;
    if (!body || typeof body.channel_id !== 'string' || !body.channel_id || typeof body.message !== 'string' || Object.keys(body).some(key => !['channel_id', 'message', 'root_id', 'props'].includes(key)) || ('root_id' in body && (typeof body.root_id !== 'string' || !body.root_id)) || ('props' in body && (!body.props || typeof body.props !== 'object' || Array.isArray(body.props)))) {
      return send(res, 400, {id: 'fixture.contract.invalid_request'});
    }
    const posts = await pm.state.get('mattermost.posts') || [];
    const post = {id: 'fixture-post-' + (posts.length + 1), user_id: 'fixture-bot', create_at: 1700000000123, root_id: '', ...body};
    posts.push(post);
    await pm.state.set('mattermost.posts', posts);
    if (scenario === 'write-then-delay') await new Promise(resolve => setTimeout(resolve, 500));
    return send(res, 201, post);
  })().catch(() => send(res, 500, {id: 'fixture.handler_failed'}));
}).listen(process.env.PORT || 4500);
