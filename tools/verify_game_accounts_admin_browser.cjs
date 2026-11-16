"use strict";

const fs = require("node:fs");
const path = require("node:path");


function loadPlaywright() {
    try {
        return require("playwright");
    } catch (error) {
        const cacheRoot = path.resolve(".npm-cache", "_npx");
        if (!fs.existsSync(cacheRoot)) throw error;
        for (const entry of fs.readdirSync(cacheRoot)) {
            const candidate = path.join(cacheRoot, entry, "node_modules", "playwright");
            if (fs.existsSync(candidate)) return require(candidate);
        }
        throw error;
    }
}


function valueHasText(value, expected) {
    if (typeof value === "string") return value.includes(expected);
    if (Array.isArray(value)) return value.some((item) => valueHasText(item, expected));
    if (value && typeof value === "object") {
        return Object.values(value).some((item) => valueHasText(item, expected));
    }
    return false;
}


function findBlock(value, predicate) {
    if (Array.isArray(value)) {
        for (const item of value) {
            const found = findBlock(item, predicate);
            if (found) return found;
        }
        return null;
    }
    if (!value || typeof value !== "object") return null;
    if (predicate(value)) return value;
    for (const item of Object.values(value)) {
        const found = findBlock(item, predicate);
        if (found) return found;
    }
    return null;
}


function postHasText(post, expected) {
    return (post.message || "").includes(expected) || valueHasText(post.props?.mm_blocks || [], expected);
}


async function main() {
    const {chromium} = loadPlaywright();
    const output = path.resolve("output/playwright/game-accounts-admin");
    fs.mkdirSync(output, {recursive: true});
    const state = JSON.parse(fs.readFileSync("build/ui-stand-state.json", "utf8"));
    const baseUrl = (process.env.MM_ADMIN_BASE_URL || state.base_url).replace(/\/$/, "");
    const team = process.env.MM_ADMIN_TEAM_SLUG || "mattergram-ui";
    const channel = process.env.MM_ADMIN_CHANNEL_SLUG || "ui-scenarios";
    const channelId = process.env.MM_ADMIN_CHANNEL_ID || state.channel_id;
    const channelUrl = process.env.MM_ADMIN_CHANNEL_URL || `${baseUrl}/${team}/channels/${channel}`;
    const apiWithToken = async (token, route, options = {}) => {
        const response = await fetch(`${baseUrl}/api/v4${route}`, {
            ...options,
            headers: {Authorization: `Bearer ${token}`, ...(options.headers || {})},
        });
        if (!response.ok) throw new Error(`Mattermost API ${route} returned ${response.status}`);
        return response.status === 204 ? null : response.json();
    };
    const api = (route, options = {}) => apiWithToken(state.admin_token, route, options);
    const actorApi = (route, options = {}) => apiWithToken(state.actor_token, route, options);
    const recentPostIds = async () => {
        const posts = await api(`/channels/${channelId}/posts?page=0&per_page=30`);
        return new Set(posts.order);
    };
    const launchOptions = {headless: true};
    const chrome = "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe";
    if (fs.existsSync(chrome)) launchOptions.executablePath = chrome;
    const browser = await chromium.launch(launchOptions);
    const context = await browser.newContext({viewport: {width: 1440, height: 1000}});
    const page = await context.newPage();
    let testPostId = null;
    let initialPostIds = new Set();
    const keyboardPostIds = [];
    const botPostIds = [];
    const actorPostIds = [];
    const browserErrors = [];
    page.on("console", (message) => {
        if (message.type() === "error") browserErrors.push(`console: ${message.text()}`);
    });
    page.on("pageerror", (error) => browserErrors.push(`page: ${error.message}`));

    try {
        const chooseBrowser = async (targetPage = page) => {
            const viewInBrowser = targetPage.getByText("View in Browser", {exact: true}).first();
            const visible = await viewInBrowser.waitFor({state: "visible", timeout: 5000})
                .then(() => true)
                .catch(() => false);
            if (visible) {
                await viewInBrowser.click();
                await targetPage.waitForURL((url) => !url.pathname.includes("/landing"), {timeout: 15000});
            }
        };
        await page.goto(channelUrl, {waitUntil: "domcontentloaded"});
        await chooseBrowser();
        const login = page.locator('input:not([type="checkbox"]):not([type="password"])').first();
        const loginVisible = await login.waitFor({state: "visible", timeout: 10000})
            .then(() => true)
            .catch(() => false);
        if (loginVisible) {
            await login.fill(state.admin_username);
            await page.getByPlaceholder("Password", {exact: true}).fill(state.admin_password);
            await page.getByRole("button", {name: "Log in", exact: true}).click();
            await page.waitForURL((url) => !url.pathname.includes("/login"), {timeout: 30000});
            await chooseBrowser();
        }
        await page.goto(channelUrl, {waitUntil: "domcontentloaded"});
        await chooseBrowser();
        const dismissWelcome = page.getByText(/No thanks.*figure it out myself/i).first();
        if (await dismissWelcome.isVisible({timeout: 3000}).catch(() => false)) {
            await dismissWelcome.click();
        }
        browserErrors.length = 0;
        const composer = page.locator(
            '#post_textbox, [data-testid="post_textbox"], [contenteditable="true"][role="textbox"]',
        ).last();
        await composer.waitFor({state: "visible", timeout: 30000});
        const keyboard = page.locator(".MattergramMenu").filter({
            hasText: "\u0410\u0434\u043c\u0438\u043d\u0438\u0441\u0442\u0440\u0438\u0440\u043e\u0432\u0430\u043d\u0438\u0435 \u0438\u0433\u0440\u043e\u0432\u044b\u0445 \u0430\u043a\u043a\u0430\u0443\u043d\u0442\u043e\u0432",
        });
        await keyboard.waitFor({state: "visible", timeout: 30000});
        const keyboardHeadings = new Map([
            ["\u041e\u0431\u0437\u043e\u0440", "\u0410\u0434\u043c\u0438\u043d\u0438\u0441\u0442\u0440\u0438\u0440\u043e\u0432\u0430\u043d\u0438\u0435 \u0438\u0433\u0440\u043e\u0432\u044b\u0445 \u0430\u043a\u043a\u0430\u0443\u043d\u0442\u043e\u0432"],
            ["\u0410\u043a\u043a\u0430\u0443\u043d\u0442\u044b", "\u0418\u0433\u0440\u043e\u0432\u044b\u0435 \u0430\u043a\u043a\u0430\u0443\u043d\u0442\u044b"],
            ["\u041f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u0442\u0435\u043b\u0438", "\u041f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u0442\u0435\u043b\u0438 \u043f\u0440\u0438\u043b\u043e\u0436\u0435\u043d\u0438\u044f"],
        ]);
        const clickKeyboard = async (label) => {
            const before = await recentPostIds();
            await keyboard.getByText(label, {exact: true}).click();
            for (let attempt = 0; attempt < 60; attempt += 1) {
                const posts = await api(`/channels/${channelId}/posts?page=0&per_page=30`);
                const triggerId = posts.order.find((postId) => (
                    !before.has(postId) &&
                    posts.posts[postId].user_id === state.admin_id &&
                    posts.posts[postId].message === label
                ));
                const botPostId = posts.order.find((postId) => (
                    !before.has(postId) &&
                    posts.posts[postId].user_id === state.bot_id &&
                    posts.posts[postId].props.mm_blocks &&
                    (!keyboardHeadings.has(label) || postHasText(posts.posts[postId], keyboardHeadings.get(label)))
                ));
                if (triggerId && botPostId) {
                    keyboardPostIds.push(triggerId);
                    botPostIds.push(botPostId);
                    return botPostId;
                }
                await page.waitForTimeout(250);
            }
            throw new Error(`Keyboard message ${label} did not produce a new bot post`);
        };
        const locatePost = async (postId) => {
            let target = page.locator(`#post_${postId}`);
            if (await target.count() === 0) {
                await page.goto(`${baseUrl}/${team}/pl/${postId}`, {waitUntil: "domcontentloaded"});
                target = page.locator(`#post_${postId}`);
            }
            await target.waitFor({state: "visible", timeout: 30000});
            return target;
        };
        initialPostIds = await recentPostIds();
        testPostId = await clickKeyboard("\u041e\u0431\u0437\u043e\u0440");
        let post = await locatePost(testPostId);
        let homeHeading = post.getByText("\u0410\u0434\u043c\u0438\u043d\u0438\u0441\u0442\u0440\u0438\u0440\u043e\u0432\u0430\u043d\u0438\u0435 \u0438\u0433\u0440\u043e\u0432\u044b\u0445 \u0430\u043a\u043a\u0430\u0443\u043d\u0442\u043e\u0432", {exact: true});
        await homeHeading.waitFor({state: "visible", timeout: 30000});
        await page.screenshot({path: path.join(output, "home.png"), fullPage: false});

        const homePostId = testPostId;
        testPostId = await clickKeyboard("\u0410\u043a\u043a\u0430\u0443\u043d\u0442\u044b");
        if (testPostId === homePostId) throw new Error("Keyboard navigation edited the previous bot post");
        post = await locatePost(testPostId);
        const invalidAction = page.getByText("Invalid action id.", {exact: true});
        const invalidVisible = await invalidAction.waitFor({state: "visible", timeout: 3000})
            .then(() => true)
            .catch(() => false);
        if (invalidVisible) {
            throw new Error("Mattermost displayed Invalid action id for the new post");
        }
        const accountsHeading = post.getByText("\u0418\u0433\u0440\u043e\u0432\u044b\u0435 \u0430\u043a\u043a\u0430\u0443\u043d\u0442\u044b", {exact: true});
        await accountsHeading.waitFor({state: "visible", timeout: 30000});
        const accountsPost = await api(`/posts/${testPostId}`);
        if (!postHasText(accountsPost, "\u0418\u0433\u0440\u043e\u0432\u044b\u0435 \u0430\u043a\u043a\u0430\u0443\u043d\u0442\u044b")) {
            throw new Error("The Accounts command did not create the expected bot post");
        }
        const accountBlocks = accountsPost.props.mm_blocks || [];
        const accountContainer = accountBlocks[0];
        if (
            accountBlocks.length !== 1 ||
            accountContainer?.type !== "container" ||
            accountContainer.border !== true ||
            accountContainer.background !== "gray" ||
            accountContainer.accent_color !== "primary"
        ) {
            throw new Error("Accounts post is not rendered in one shared accented container");
        }
        const unchangedHomePost = await api(`/posts/${homePostId}`);
        if (!unchangedHomePost.message.includes("\u0410\u0434\u043c\u0438\u043d\u0438\u0441\u0442\u0440\u0438\u0440\u043e\u0432\u0430\u043d\u0438\u0435 \u0438\u0433\u0440\u043e\u0432\u044b\u0445 \u0430\u043a\u043a\u0430\u0443\u043d\u0442\u043e\u0432")) {
            throw new Error("Reply-keyboard navigation modified the previous bot post");
        }
        await page.screenshot({path: path.join(output, "accounts.png"), fullPage: false});

        const accountSelect = post.locator([
            "select",
            '[role="combobox"]',
            'button[aria-haspopup="listbox"]',
            'button[aria-haspopup="menu"]',
        ].join(", ")).last();
        await accountSelect.click();
        await page.keyboard.press("ArrowDown");
        await page.keyboard.press("Enter");
        // Match the base emoji code point instead of an exact grapheme: browsers may
        // expose the optional variation selector differently in accessible names.
        const editAccount = post.locator("button").filter({hasText: /✏/}).first();
        const deleteAccount = post.locator("button").filter({hasText: /🗑/}).first();
        await editAccount.waitFor({state: "visible", timeout: 30000});
        await deleteAccount.waitFor({state: "visible", timeout: 30000});

        await post.getByRole("button", {name: "Проверить", exact: true}).click();
        await post.getByText("Успешно завершена", {exact: true})
            .waitFor({state: "visible", timeout: 30000});
        const embeddedCheckHeading = post.getByText("Проверка", {exact: true});
        await embeddedCheckHeading.scrollIntoViewIfNeeded();
        await post.screenshot({path: path.join(output, "account-check-nested.png")});
        const checkedAccountPost = await api(`/posts/${testPostId}`);
        const selectedAccountCard = findBlock(
            checkedAccountPost.props.mm_blocks || [],
            (block) => block.type === "collapsible" && valueHasText(block.header, "Выбранный аккаунт"),
        );
        const embeddedCheck = selectedAccountCard && findBlock(
            selectedAccountCard.content || [],
            (block) => block.type === "collapsible" && valueHasText(block.header, "### Проверка"),
        );
        if (!selectedAccountCard || !embeddedCheck) {
            throw new Error("Account check is not nested inside the selected account card");
        }
        if (valueHasText(embeddedCheck.header, "Проверка аккаунта")) {
            throw new Error("Embedded account check repeats the parent account ID");
        }

        await editAccount.click();
        const editAccountForm = page.getByRole("dialog").filter({hasText: "Учётные данные"});
        await editAccountForm.waitFor({state: "visible", timeout: 30000});
        const closeAccountEdit = editAccountForm.locator([
            'button[aria-label*="close" i]',
            'button[title*="close" i]',
            'button.close',
            '[data-testid*="close" i]',
        ].join(", ")).first();
        await closeAccountEdit.click();
        await editAccountForm.waitFor({state: "hidden", timeout: 30000});
        await page.waitForTimeout(1000);

        await deleteAccount.click();
        const deleteAccountForm = page.getByRole("dialog").filter({hasText: "Удалить аккаунт"});
        await deleteAccountForm.waitFor({state: "visible", timeout: 30000});
        await deleteAccountForm.locator('input[type="checkbox"]').first()
            .waitFor({state: "visible", timeout: 30000});
        await page.screenshot({path: path.join(output, "account-delete-confirm.png"), fullPage: false});
        const closeAccountDelete = deleteAccountForm.locator([
            'button[aria-label*="close" i]',
            'button[title*="close" i]',
            'button.close',
            '[data-testid*="close" i]',
        ].join(", ")).first();
        await closeAccountDelete.click();
        await deleteAccountForm.waitFor({state: "hidden", timeout: 30000});
        await page.waitForTimeout(1000);

        testPostId = await clickKeyboard("\u041e\u0431\u0437\u043e\u0440");
        post = await locatePost(testPostId);
        homeHeading = post.getByText("\u0410\u0434\u043c\u0438\u043d\u0438\u0441\u0442\u0440\u0438\u0440\u043e\u0432\u0430\u043d\u0438\u0435 \u0438\u0433\u0440\u043e\u0432\u044b\u0445 \u0430\u043a\u043a\u0430\u0443\u043d\u0442\u043e\u0432", {exact: true});
        await homeHeading.waitFor({state: "visible", timeout: 30000});
        testPostId = await clickKeyboard("\u041f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u0442\u0435\u043b\u0438");
        post = await locatePost(testPostId);
        const usersHeading = post.getByText("\u041f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u0442\u0435\u043b\u0438", {exact: true}).first();
        await usersHeading.waitFor({state: "visible", timeout: 30000});
        const usersPostBeforeForm = await api(`/posts/${testPostId}`);
        const userBlocks = usersPostBeforeForm.props.mm_blocks || [];
        if (userBlocks.length < 2 || userBlocks[0].border !== false || userBlocks[1].border !== false) {
            throw new Error("Application users must render as plain borderless post content");
        }
        if (userBlocks[0].accent_color || userBlocks[1].accent_color) {
            throw new Error("Application users unexpectedly rendered a decorative accent container");
        }
        const addButtonBlock = userBlocks[0].content?.[0]?.columns?.[0]?.items?.[0];
        if (addButtonBlock?.type !== "button") {
            throw new Error("The add-user action is not rendered before the users table");
        }
        await post.getByRole("button", {name: "\u0414\u043e\u0431\u0430\u0432\u0438\u0442\u044c \u043f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u0442\u0435\u043b\u044f", exact: true}).click();
        const userForm = page.getByRole("dialog").filter({
            hasText: "\u0414\u043e\u0431\u0430\u0432\u0438\u0442\u044c \u043f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u0442\u0435\u043b\u044f",
        });
        await userForm.waitFor({state: "visible", timeout: 30000});
        await page.screenshot({path: path.join(output, "user-form.png"), fullPage: false});
        const closeForm = userForm.locator([
            'button[aria-label*="close" i]',
            'button[title*="close" i]',
            'button.close',
            '[data-testid*="close" i]',
        ].join(", ")).first();
        if (await closeForm.count() === 0) {
            throw new Error("The native user form has no discoverable close button");
        }
        await closeForm.click();
        await userForm.waitFor({state: "hidden", timeout: 30000});
        await usersHeading.waitFor({state: "visible", timeout: 30000});
        const outdatedAction = page.getByText("This dialog action is outdated", {exact: true});
        if (await outdatedAction.isVisible().catch(() => false)) {
            throw new Error("Closing the native form left the source post actions outdated");
        }
        // Mattermost hides the form before the asynchronous cancel callback has restored
        // dialog state. Give that callback one bounded client tick before reusing the post.
        await page.waitForTimeout(1000);

        await post.getByRole("button", {
            name: "\u0414\u043e\u0431\u0430\u0432\u0438\u0442\u044c \u043f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u0442\u0435\u043b\u044f",
            exact: true,
        }).click();
        await userForm.waitFor({state: "visible", timeout: 30000});
        await userForm.locator(".react-select__control").first().click();
        await page.getByText(`@${state.actor_username}`, {exact: true}).last().click();
        await userForm.getByRole("button", {name: "\u0414\u043e\u0431\u0430\u0432\u0438\u0442\u044c", exact: true}).click();
        await userForm.waitFor({state: "hidden", timeout: 30000});
        await post.getByText(state.actor_username, {exact: true}).waitFor({state: "visible", timeout: 30000});

        const usersPostId = testPostId;
        testPostId = await clickKeyboard("\u041e\u0431\u0437\u043e\u0440");
        if (testPostId === usersPostId) throw new Error("Keyboard navigation edited the users post");
        post = await locatePost(testPostId);
        homeHeading = post.getByText("\u0410\u0434\u043c\u0438\u043d\u0438\u0441\u0442\u0440\u0438\u0440\u043e\u0432\u0430\u043d\u0438\u0435 \u0438\u0433\u0440\u043e\u0432\u044b\u0445 \u0430\u043a\u043a\u0430\u0443\u043d\u0442\u043e\u0432", {exact: true});
        await homeHeading.waitFor({state: "visible", timeout: 30000});
        const unchangedUsersPost = await api(`/posts/${usersPostId}`);
        if (!unchangedUsersPost.message.includes("\u041f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u0442\u0435\u043b\u0438 \u043f\u0440\u0438\u043b\u043e\u0436\u0435\u043d\u0438\u044f")) {
            throw new Error("Reply-keyboard navigation modified the users post");
        }
        await page.screenshot({path: path.join(output, "user-after-cancel.png"), fullPage: false});

        const botUser = await api(`/users/${state.bot_id}`);
        const actorChannel = await actorApi("/channels/direct", {
            method: "POST",
            headers: {"Content-Type": "application/json"},
            body: JSON.stringify([state.actor_id, state.bot_id]),
        });
        const actorUrl = `${baseUrl}/${team}/messages/@${botUser.username}`;
        const actorErrorStart = browserErrors.length;
        const actorContext = await browser.newContext({viewport: {width: 1440, height: 1000}});
        const actorPage = await actorContext.newPage();
        actorPage.on("response", (response) => {
            if (response.status() >= 400) {
                browserErrors.push(`operator response ${response.status()}: ${response.url()}`);
            }
        });
        actorPage.on("pageerror", (error) => browserErrors.push(`operator page: ${error.message}`));
        try {
            await actorPage.goto(actorUrl, {waitUntil: "domcontentloaded"});
            await chooseBrowser(actorPage);
            const actorLogin = actorPage.locator(
                'input:not([type="checkbox"]):not([type="password"])',
            ).first();
            if (await actorLogin.waitFor({state: "visible", timeout: 10000}).then(() => true).catch(() => false)) {
                await actorLogin.fill(state.actor_username);
                await actorPage.getByPlaceholder("Password", {exact: true}).fill(state.actor_password);
                await actorPage.getByRole("button", {name: "Log in", exact: true}).click();
                await actorPage.waitForURL((url) => !url.pathname.includes("/login"), {timeout: 30000});
                await chooseBrowser(actorPage);
            }
            await actorPage.goto(actorUrl, {waitUntil: "domcontentloaded"});
            await chooseBrowser(actorPage);
            const actorDismissWelcome = actorPage.getByText(/No thanks.*figure it out myself/i).first();
            if (await actorDismissWelcome.isVisible({timeout: 3000}).catch(() => false)) {
                await actorDismissWelcome.click();
            }
            browserErrors.splice(actorErrorStart);
            const actorKeyboard = actorPage.locator(".MattergramMenu").filter({
                hasText: "\u0418\u0433\u0440\u043e\u0432\u044b\u0435 \u0430\u043a\u043a\u0430\u0443\u043d\u0442\u044b",
            });
            await actorKeyboard.waitFor({state: "visible", timeout: 30000});
            const operatorActions = [
                "\u041e\u0431\u0437\u043e\u0440",
                "\u0410\u043a\u043a\u0430\u0443\u043d\u0442\u044b",
            ];
            for (const label of operatorActions) {
                if (await actorKeyboard.getByText(label, {exact: true}).count() !== 1) {
                    throw new Error(`Operator keyboard is missing game-account action: ${label}`);
                }
            }
            if (await actorKeyboard.getByText("\u041f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u0442\u0435\u043b\u0438", {exact: true}).count() !== 0) {
                throw new Error("Operator keyboard exposes application-user administration");
            }

            const actorPostsBefore = await actorApi(
                `/channels/${actorChannel.id}/posts?page=0&per_page=30`,
            );
            const knownActorPosts = new Set(actorPostsBefore.order);
            await actorKeyboard.getByText("\u0410\u043a\u043a\u0430\u0443\u043d\u0442\u044b", {exact: true}).click();
            let actorTriggerId = null;
            let actorBotPostId = null;
            for (let attempt = 0; attempt < 60; attempt += 1) {
                const posts = await actorApi(`/channels/${actorChannel.id}/posts?page=0&per_page=30`);
                actorTriggerId = posts.order.find((postId) => (
                    !knownActorPosts.has(postId) &&
                    posts.posts[postId].user_id === state.actor_id &&
                    posts.posts[postId].message === "\u0410\u043a\u043a\u0430\u0443\u043d\u0442\u044b"
                ));
                actorBotPostId = posts.order.find((postId) => (
                    !knownActorPosts.has(postId) &&
                    posts.posts[postId].user_id === state.bot_id &&
                    posts.posts[postId].props.mm_blocks &&
                    postHasText(posts.posts[postId], "\u0418\u0433\u0440\u043e\u0432\u044b\u0435 \u0430\u043a\u043a\u0430\u0443\u043d\u0442\u044b")
                ));
                if (actorTriggerId && actorBotPostId) break;
                await actorPage.waitForTimeout(250);
            }
            if (!actorTriggerId || !actorBotPostId) {
                throw new Error("Operator keyboard did not produce an accounts post");
            }
            actorPostIds.push(actorTriggerId, actorBotPostId);
            await actorPage.goto(`${baseUrl}/${team}/pl/${actorBotPostId}`, {waitUntil: "domcontentloaded"});
            await chooseBrowser(actorPage);
            const actorPost = actorPage.locator(`#post_${actorBotPostId}`);
            await actorPost.waitFor({state: "visible", timeout: 30000});
            await actorPost.getByText("\u0418\u0433\u0440\u043e\u0432\u044b\u0435 \u0430\u043a\u043a\u0430\u0443\u043d\u0442\u044b", {exact: true})
                .waitFor({state: "visible", timeout: 30000});
            if (await actorPage.getByText("Action integration error.", {exact: true}).isVisible().catch(() => false)) {
                throw new Error("Opening operator accounts produced Action integration error");
            }
            await actorPage.screenshot({path: path.join(output, "operator-accounts.png"), fullPage: false});
        } finally {
            await actorContext.close();
        }

        const result = {
            url: page.url(),
            channel_url: channelUrl,
            title: await page.title(),
            login: state.admin_username,
            command: "/game-admin",
            test_post_id: testPostId,
            test_post_ids: botPostIds,
            home_visible: true,
            accounts_visible: true,
            accounts_single_container: true,
            account_check_nested: true,
            account_edit_form: true,
            account_delete_confirmation: true,
            keyboard_visible: true,
            keyboard_navigation: true,
            keyboard_new_posts: new Set(botPostIds).size === botPostIds.length,
            bot_dm: channelId !== state.channel_id && channelUrl.includes("/messages/@"),
            form_cancel_restored_source: true,
            operator_onboarding: true,
            operator_game_accounts: true,
            operator_user_admin_hidden: true,
            browser_errors: browserErrors,
            screenshots: [
                path.join(output, "home.png"),
                path.join(output, "accounts.png"),
                path.join(output, "account-check-nested.png"),
                path.join(output, "account-delete-confirm.png"),
                path.join(output, "user-form.png"),
                path.join(output, "user-after-cancel.png"),
                path.join(output, "operator-accounts.png"),
            ],
        };
        fs.writeFileSync(path.join(output, "result.json"), JSON.stringify(result, null, 2) + "\n");
        process.stdout.write(JSON.stringify(result, null, 2) + "\n");
    } catch (error) {
        await page.screenshot({path: path.join(output, "failure.png"), fullPage: false});
        const diagnostic = {
            url: page.url(),
            title: await page.title(),
            body: (await page.locator("body").innerText()).slice(-6000),
            browser_errors: browserErrors,
        };
        process.stderr.write(JSON.stringify(diagnostic, null, 2) + "\n");
        throw error;
    } finally {
        for (const postId of botPostIds) {
            await api(`/posts/${postId}`, {method: "DELETE"}).catch(() => null);
        }
        for (const postId of keyboardPostIds) {
            if (!initialPostIds.has(postId)) {
                await api(`/posts/${postId}`, {method: "DELETE"}).catch(() => null);
            }
        }
        for (const postId of actorPostIds) {
            await actorApi(`/posts/${postId}`, {method: "DELETE"}).catch(() => null);
        }
        await browser.close();
    }
}


main().catch((error) => {
    console.error(error);
    process.exitCode = 1;
});
