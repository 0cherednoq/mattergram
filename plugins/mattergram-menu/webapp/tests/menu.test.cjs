"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");

const menu = require("../src/index.js");

const keyboard = {
    schema_version: 1,
    title: "Actions",
    rows: [[
        {id: "accounts", text: "Accounts", message: "/accounts", style: "primary", disabled: false},
        {id: "imports", text: "Imports", message: "/imports", style: "default", disabled: false},
    ]],
};

test("validates the versioned declarative keyboard", () => {
    assert.equal(menu.validKeyboard(keyboard), true);
    assert.equal(menu.validKeyboard({...keyboard, schema_version: 2}), false);
    assert.equal(menu.validKeyboard({...keyboard, rows: [[keyboard.rows[0][0], keyboard.rows[0][0]]]}), false);
    assert.equal(menu.shouldRenderMenu({id: "channel1", type: "O", delete_at: 0}, keyboard), true);
    assert.equal(menu.shouldRenderMenu({id: "channel1", type: "O", delete_at: 1}, keyboard), false);
    assert.equal(menu.shouldRenderMenu({id: "channel1", type: "O"}, null), false);
});

test("loads a channel keyboard from the authenticated plugin endpoint", async () => {
    let captured;
    const result = await menu.fetchKeyboard("channel1", async (url, options) => {
        captured = {url, options};
        return {
            ok: true,
            json: async () => ({channel_id: "channel1", revision: 3, keyboard}),
        };
    });
    assert.equal(captured.url, "/plugins/com.mattergram.menu/api/v1/channels/channel1/keyboard");
    assert.equal(captured.options.method, "GET");
    assert.equal(captured.options.credentials, "same-origin");
    assert.deepEqual(result, keyboard);

    const absent = await menu.fetchKeyboard("channel1", async () => ({
        ok: true,
        json: async () => ({keyboard: null}),
    }));
    assert.equal(absent, null);
});

test("posts the declared message as the current Mattermost user", async () => {
    let captured;
    const response = await menu.postMessage("channel1", keyboard.rows[0][0], async (url, options) => {
        captured = {url, options};
        return {ok: true, json: async () => ({id: "post1", message: "/accounts"})};
    });
    assert.equal(captured.url, "/api/v4/posts");
    assert.equal(captured.options.method, "POST");
    assert.equal(captured.options.credentials, "same-origin");
    assert.deepEqual(JSON.parse(captured.options.body), {channel_id: "channel1", message: "/accounts"});
    assert.equal(response.id, "post1");
});

test("does not retry an ambiguous failed message post", async () => {
    let calls = 0;
    await assert.rejects(menu.postMessage("channel1", keyboard.rows[0][0], async () => {
        calls += 1;
        return {ok: false, status: 503, json: async () => ({message: "temporarily unavailable"})};
    }), /temporarily unavailable/);
    assert.equal(calls, 1);
});

test("registers composer, websocket refresh and styles once", () => {
    let installedPlugin;
    let registeredComponent;
    let eventName;
    let eventHandler;
    let reconnectHandler;
    const styleNodes = new Map();
    const documentObject = {
        createElement: () => ({}),
        getElementById: (id) => styleNodes.get(id),
        head: {appendChild: (node) => styleNodes.set(node.id, node)},
    };
    const host = {
        React: {createElement: () => null, useEffect: () => {}, useState: () => [null, () => {}]},
        document: documentObject,
        fetch: async () => ({ok: true, json: async () => ({keyboard: null})}),
        registerPlugin: (id, plugin) => {
            assert.equal(id, menu.PLUGIN_ID);
            installedPlugin = plugin;
        },
    };
    menu.register(host);
    installedPlugin.initialize({
        registerChannelComposerBannerComponent: (component) => { registeredComponent = component; },
        registerWebSocketEventHandler: (name, handler) => { eventName = name; eventHandler = handler; },
        registerReconnectHandler: (handler) => { reconnectHandler = handler; },
    });

    assert.equal(typeof registeredComponent, "function");
    assert.equal(eventName, menu.UPDATE_EVENT);
    assert.equal(typeof eventHandler, "function");
    assert.equal(typeof reconnectHandler, "function");
    assert.equal(styleNodes.size, 1);
});
