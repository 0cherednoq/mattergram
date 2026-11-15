// Stateful Mattermost provisioning boundary used by the Bootstrap fault harness.
// Native response acceptance is established separately against the owned 12.0.2 stand.
const http = require('http');

const send = (res, status, body, headers = {}) => {
  res.writeHead(status, {'Content-Type': 'application/json', ...headers});
  res.end(JSON.stringify(body));
};

const fresh = scenario => ({
  scenario: scenario || 'normal',
  flags: {commands: true, bot_creation: true, access_tokens: true},
  users: [
    {id: 'ordinary-user', username: 'ordinary', roles: 'system_user', is_bot: false, delete_at: 0},
  ],
  bots: [],
  teams: [],
  channels: [],
  teamMembers: [],
  channelMembers: [],
  commands: [],
  tokens: [],
  calls: [],
  counters: {bot: 0, team: 0, channel: 0, command: 0, token: 0},
});

const getState = async () => await pm.state.get('bootstrap.state') || fresh('normal');
const saveState = async state => await pm.state.set('bootstrap.state', state);

const roles = {
  system_admin: [
    'manage_system', 'create_bot', 'create_user_access_token', 'manage_bots',
    'manage_others_bots', 'create_team', 'manage_team', 'create_private_channel',
    'create_public_channel', 'add_user_to_team', 'manage_private_channel_members',
    'manage_public_channel_members', 'manage_private_channel_properties',
    'manage_public_channel_properties', 'view_team', 'manage_own_slash_commands',
    'manage_others_slash_commands',
  ],
  system_user: [],
  team_admin: [
    'view_team', 'manage_team', 'create_private_channel', 'create_public_channel',
    'add_user_to_team', 'manage_private_channel_members', 'manage_public_channel_members',
    'manage_private_channel_properties', 'manage_public_channel_properties',
    'manage_own_slash_commands', 'manage_others_slash_commands',
  ],
  team_user: ['view_team'],
  channel_admin: ['read_channel', 'create_post'],
  channel_user: ['read_channel', 'create_post'],
};

const actor = (req, state) => {
  const value = req.headers.authorization || '';
  if (value === 'Bearer fixture-bootstrap-setup') {
    return {id: 'setup-actor', username: 'provisioner', roles: 'system_admin', is_bot: false, delete_at: 0};
  }
  if (value === 'Bearer fixture-limited-setup') {
    return {id: 'limited-actor', username: 'limited', roles: 'system_user', is_bot: false, delete_at: 0};
  }
  const issued = state.tokens.find(item => value === `Bearer ${item.token}`);
  if (issued) return state.users.find(item => item.id === issued.user_id) || null;
  return null;
};

const allUsers = (state, principal) => [
  {id: 'setup-actor', username: 'provisioner', roles: 'system_admin', is_bot: false, delete_at: 0},
  {id: 'limited-actor', username: 'limited', roles: 'system_user', is_bot: false, delete_at: 0},
  ...state.users,
  ...(principal && !state.users.some(item => item.id === principal.id) ? [principal] : []),
];

const writeFailure = (state, path, method) => {
  if (!['POST', 'PUT', 'PATCH', 'DELETE'].includes(method)) return null;
  if (state.scenario === 'forbidden') return [403, {id: 'fixture.bootstrap_forbidden'}, {}];
  if (state.scenario === 'rate-limit') {
    return [429, {id: 'fixture.bootstrap_rate_limit'}, {'Retry-After': '2'}];
  }
  if (state.scenario === 'redirect') return [302, {}, {Location: '/__control/must-not-follow'}];
  if (state.scenario === 'forbid-team' && path === '/api/v4/teams') {
    return [403, {id: 'fixture.team_forbidden'}, {}];
  }
  return null;
};

http.createServer((req, res) => {
  (async () => {
    const url = new URL(req.url, 'http://localhost');
    const path = url.pathname;
    if (path === '/__control/reset' && req.method === 'POST') {
      await saveState(fresh(req.body && req.body.scenario));
      return send(res, 200, {ok: true});
    }
    if (path === '/__control/scenario' && req.method === 'POST') {
      const state = await getState();
      state.scenario = req.body.scenario;
      await saveState(state);
      return send(res, 200, {ok: true});
    }
    if (path === '/__control/features' && req.method === 'POST') {
      const state = await getState();
      state.flags = {...state.flags, ...req.body};
      await saveState(state);
      return send(res, 200, {ok: true});
    }
    if (path === '/__control/seed-limited' && req.method === 'POST') {
      const state = fresh(req.body && req.body.scenario);
      const team = {id: 'limited-team', name: 'engineering', display_name: 'Engineering', type: 'O', delete_at: 0};
      const user = {id: 'limited-bot', username: 'existing-bot', roles: 'system_user', is_bot: true, delete_at: 0};
      state.teams.push(team);
      state.users.push(user);
      state.bots.push({user_id: user.id, username: user.username, owner_id: 'setup-actor', display_name: '', description: '', delete_at: 0});
      state.tokens.push({id: 'limited-pat', user_id: user.id, token: 'fixture-existing-runtime', description: 'existing', is_active: true});
      state.teamMembers.push({team_id: team.id, user_id: 'limited-actor', roles: req.body.team_role || 'team_admin', delete_at: 0});
      state.teamMembers.push({team_id: team.id, user_id: user.id, roles: 'team_user', delete_at: 0});
      state.counters = {bot: 1, team: 1, channel: 0, command: 0, token: 1};
      await saveState(state);
      return send(res, 200, {ok: true});
    }
    if (path === '/__control/state' && req.method === 'GET') {
      const state = await getState();
      return send(res, 200, {
        scenario: state.scenario,
        calls: state.calls,
        counts: {
          bots: state.bots.length,
          teams: state.teams.length,
          channels: state.channels.length,
          commands: state.commands.length,
          tokens: state.tokens.length,
          team_members: state.teamMembers.length,
          channel_members: state.channelMembers.length,
        },
        ids: {
          bots: state.bots.map(item => item.user_id),
          teams: state.teams.map(item => item.id),
          channels: state.channels.map(item => item.id),
          commands: state.commands.map(item => item.id),
          tokens: state.tokens.map(item => item.id),
        },
        command_metadata: state.commands.map(item => ({
          id: item.id,
          trigger: item.trigger,
          team_id: item.team_id,
          description: item.description,
          auto_complete_hint: item.auto_complete_hint,
        })),
        channel_metadata: state.channels.map(item => ({
          id: item.id,
          team_id: item.team_id,
          name: item.name,
          header: item.header,
        })),
      });
    }

    const state = await getState();
    const principal = actor(req, state);
    state.calls.push({method: req.method, path, actor: principal ? principal.id : null});
    await saveState(state);
    if (!principal) return send(res, 401, {id: 'fixture.invalid_token'});
    const failure = writeFailure(state, path, req.method);
    if (failure) return send(res, failure[0], failure[1], failure[2]);

    if (path === '/api/v4/users/me' && req.method === 'GET') return send(res, 200, principal);
    if (path === '/api/v4/config/client' && req.method === 'GET') {
      return send(res, 200, {
        Version: '12.0.2',
        EnableCommands: String(state.flags.commands),
        EnableBotAccountCreation: String(state.flags.bot_creation),
        EnableUserAccessTokens: String(state.flags.access_tokens),
      });
    }
    if (path === '/api/v4/config/patch' && req.method === 'PUT') {
      if (principal.id !== 'setup-actor') return send(res, 403, {id: 'fixture.config_forbidden'});
      const patch = req.body && req.body.ServiceSettings || {};
      if ('EnableCommands' in patch) state.flags.commands = Boolean(patch.EnableCommands);
      if ('EnableBotAccountCreation' in patch) state.flags.bot_creation = Boolean(patch.EnableBotAccountCreation);
      if ('EnableUserAccessTokens' in patch) state.flags.access_tokens = Boolean(patch.EnableUserAccessTokens);
      await saveState(state);
      return send(res, 200, {status: 'OK'});
    }

    const roleMatch = path.match(/^\/api\/v4\/roles\/name\/([^/]+)$/);
    if (roleMatch && req.method === 'GET') {
      const name = decodeURIComponent(roleMatch[1]);
      return name in roles
        ? send(res, 200, {name, permissions: roles[name], delete_at: 0})
        : send(res, 404, {id: 'fixture.role_not_found'});
    }

    const usernameMatch = path.match(/^\/api\/v4\/users\/username\/([^/]+)$/);
    if (usernameMatch && req.method === 'GET') {
      const username = decodeURIComponent(usernameMatch[1]);
      const user = allUsers(state, principal).find(item => item.username === username);
      return user ? send(res, 200, user) : send(res, 404, {id: 'fixture.user_not_found'});
    }
    const userMatch = path.match(/^\/api\/v4\/users\/([^/]+)$/);
    if (userMatch && req.method === 'GET') {
      const user = allUsers(state, principal).find(item => item.id === userMatch[1]);
      return user ? send(res, 200, user) : send(res, 404, {id: 'fixture.user_not_found'});
    }

    if (path === '/api/v4/bots' && req.method === 'POST') {
      state.counters.bot += 1;
      const user = {
        id: `bootstrap-bot-${state.counters.bot}`,
        username: req.body.username,
        roles: 'system_user',
        is_bot: true,
        delete_at: 0,
      };
      const bot = {
        user_id: user.id,
        username: user.username,
        owner_id: principal.id,
        display_name: req.body.display_name || '',
        description: req.body.description || '',
        delete_at: 0,
      };
      state.users.push(user);
      state.bots.push(bot);
      await saveState(state);
      return send(res, 201, bot);
    }
    const botMatch = path.match(/^\/api\/v4\/bots\/([^/]+)$/);
    if (botMatch && req.method === 'GET') {
      const bot = state.bots.find(item => item.user_id === botMatch[1]);
      return bot ? send(res, 200, bot) : send(res, 404, {id: 'fixture.bot_not_found'});
    }
    const tokenMatch = path.match(/^\/api\/v4\/users\/([^/]+)\/tokens$/);
    if (tokenMatch && req.method === 'POST') {
      state.counters.token += 1;
      const token = {
        id: `bootstrap-pat-${state.counters.token}`,
        user_id: tokenMatch[1],
        token: `bootstrap-runtime-secret-${state.counters.token}`,
        description: req.body.description,
        is_active: true,
      };
      state.tokens.push(token);
      await saveState(state);
      return send(res, 200, token);
    }

    if (path === '/api/v4/teams' && req.method === 'POST') {
      state.counters.team += 1;
      const team = {id: `bootstrap-team-${state.counters.team}`, ...req.body, delete_at: 0};
      state.teams.push(team);
      state.teamMembers.push({team_id: team.id, user_id: principal.id, roles: 'team_admin', delete_at: 0});
      await saveState(state);
      if (state.scenario === 'team-write-delay') {
        await new Promise(resolve => setTimeout(resolve, 1500));
      }
      return send(res, 201, team);
    }
    const teamByName = path.match(/^\/api\/v4\/teams\/name\/([^/]+)$/);
    if (teamByName && req.method === 'GET') {
      const team = state.teams.find(item => item.name === decodeURIComponent(teamByName[1]));
      return team ? send(res, 200, team) : send(res, 404, {id: 'fixture.team_not_found'});
    }
    const teamPatch = path.match(/^\/api\/v4\/teams\/([^/]+)\/patch$/);
    if (teamPatch && req.method === 'PUT') {
      const index = state.teams.findIndex(item => item.id === teamPatch[1]);
      if (index < 0) return send(res, 404, {id: 'fixture.team_not_found'});
      state.teams[index] = {...state.teams[index], ...req.body};
      await saveState(state);
      return send(res, 200, state.teams[index]);
    }
    const teamMember = path.match(/^\/api\/v4\/teams\/([^/]+)\/members\/([^/]+)$/);
    if (teamMember && req.method === 'GET') {
      const member = state.teamMembers.find(item => item.team_id === teamMember[1] && item.user_id === teamMember[2]);
      return member ? send(res, 200, member) : send(res, 404, {id: 'fixture.team_member_not_found'});
    }
    const teamMembers = path.match(/^\/api\/v4\/teams\/([^/]+)\/members$/);
    if (teamMembers && req.method === 'POST') {
      const member = {team_id: teamMembers[1], user_id: req.body.user_id, roles: 'team_user', delete_at: 0};
      if (!state.teamMembers.some(item => item.team_id === member.team_id && item.user_id === member.user_id)) {
        state.teamMembers.push(member);
      }
      await saveState(state);
      return send(res, 201, member);
    }
    const teamMatch = path.match(/^\/api\/v4\/teams\/([^/]+)$/);
    if (teamMatch && req.method === 'GET') {
      const team = state.teams.find(item => item.id === teamMatch[1]);
      return team ? send(res, 200, team) : send(res, 404, {id: 'fixture.team_not_found'});
    }

    if (path === '/api/v4/channels' && req.method === 'POST') {
      state.counters.channel += 1;
      const channel = {id: `bootstrap-channel-${state.counters.channel}`, ...req.body, delete_at: 0};
      state.channels.push(channel);
      state.channelMembers.push({channel_id: channel.id, user_id: principal.id, roles: 'channel_admin'});
      await saveState(state);
      return send(res, 201, channel);
    }
    const channelByName = path.match(/^\/api\/v4\/teams\/([^/]+)\/channels\/name\/([^/]+)$/);
    if (channelByName && req.method === 'GET') {
      const channel = state.channels.find(item => item.team_id === channelByName[1] && item.name === decodeURIComponent(channelByName[2]));
      return channel ? send(res, 200, channel) : send(res, 404, {id: 'fixture.channel_not_found'});
    }
    const channelPatch = path.match(/^\/api\/v4\/channels\/([^/]+)\/patch$/);
    if (channelPatch && req.method === 'PUT') {
      const index = state.channels.findIndex(item => item.id === channelPatch[1]);
      if (index < 0) return send(res, 404, {id: 'fixture.channel_not_found'});
      state.channels[index] = {...state.channels[index], ...req.body};
      await saveState(state);
      return send(res, 200, state.channels[index]);
    }
    const channelMember = path.match(/^\/api\/v4\/channels\/([^/]+)\/members\/([^/]+)$/);
    if (channelMember && req.method === 'GET') {
      const member = state.channelMembers.find(item => item.channel_id === channelMember[1] && item.user_id === channelMember[2]);
      return member ? send(res, 200, member) : send(res, 404, {id: 'fixture.channel_member_not_found'});
    }
    const channelMembers = path.match(/^\/api\/v4\/channels\/([^/]+)\/members$/);
    if (channelMembers && req.method === 'POST') {
      const member = {channel_id: channelMembers[1], user_id: req.body.user_id, roles: 'channel_user'};
      if (!state.channelMembers.some(item => item.channel_id === member.channel_id && item.user_id === member.user_id)) {
        state.channelMembers.push(member);
      }
      await saveState(state);
      return send(res, 201, member);
    }
    const channelMatch = path.match(/^\/api\/v4\/channels\/([^/]+)$/);
    if (channelMatch && req.method === 'GET') {
      const channel = state.channels.find(item => item.id === channelMatch[1]);
      return channel ? send(res, 200, channel) : send(res, 404, {id: 'fixture.channel_not_found'});
    }

    if (path === '/api/v4/commands' && req.method === 'GET') {
      const teamId = url.searchParams.get('team_id');
      const foreign = state.scenario === 'foreign-command'
        ? [{id: 'foreign-command', team_id: teamId, trigger: 'help', creator_id: 'foreign-user'}]
        : [];
      return send(res, 200, [{trigger: 'builtin'}, ...foreign, ...state.commands.filter(item => item.team_id === teamId)]);
    }
    if (path === '/api/v4/commands' && req.method === 'POST') {
      state.counters.command += 1;
      const command = {
        ...req.body,
        id: `bootstrap-command-${state.counters.command}`,
        creator_id: principal.id,
        token: `bootstrap-command-secret-${state.counters.command}`,
      };
      state.commands.push(command);
      await saveState(state);
      return send(res, 201, command);
    }
    const commandMatch = path.match(/^\/api\/v4\/commands\/([^/]+)$/);
    if (commandMatch && req.method === 'GET') {
      const command = state.commands.find(item => item.id === commandMatch[1]);
      return command ? send(res, 200, command) : send(res, 404, {id: 'fixture.command_not_found'});
    }
    if (commandMatch && req.method === 'PUT') {
      const index = state.commands.findIndex(item => item.id === commandMatch[1]);
      if (index < 0) return send(res, 404, {id: 'fixture.command_not_found'});
      const previous = state.commands[index];
      const token = state.scenario === 'rotate-command-token'
        ? `bootstrap-command-rotated-${state.counters.command}`
        : previous.token;
      state.commands[index] = {...previous, ...req.body, token};
      await saveState(state);
      return send(res, 200, state.commands[index]);
    }

    return send(res, 501, {id: 'fixture.bootstrap_endpoint_not_modeled'});
  })().catch(error => send(res, 500, {id: 'fixture.bootstrap_handler_failed', kind: error && error.name}));
}).listen(process.env.PORT || 4501);
