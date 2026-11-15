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
    const apiHeaders = {Authorization: `Bearer ${state.admin_token}`};
    const api = async (route, options = {}) => {
        const response = await fetch(`${baseUrl}/api/v4${route}`, {
            ...options,
            headers: {...apiHeaders, ...(options.headers || {})},
        });
        if (!response.ok) throw new Error(`Mattermost API ${route} returned ${response.status}`);
        return response.status === 204 ? null : response.json();
    };
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
    const browserErrors = [];
    page.on("console", (message) => {
        if (message.type() === "error") browserErrors.push(`console: ${message.text()}`);
    });
    page.on("pageerror", (error) => browserErrors.push(`page: ${error.message}`));

    try {
        const chooseBrowser = async () => {
            const viewInBrowser = page.getByText("View in Browser", {exact: true}).first();
            const visible = await viewInBrowser.waitFor({state: "visible", timeout: 5000})
                .then(() => true)
                .catch(() => false);
            if (visible) {
                await viewInBrowser.click();
                await page.waitForURL((url) => !url.pathname.includes("/landing"), {timeout: 15000});
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
        const composer = page.locator(
            '#post_textbox, [data-testid="post_textbox"], [contenteditable="true"][role="textbox"]',
        ).last();
        await composer.waitFor({state: "visible", timeout: 30000});
        const keyboard = page.locator(".MattergramMenu").filter({
            hasText: "\u0410\u0434\u043c\u0438\u043d\u0438\u0441\u0442\u0440\u0438\u0440\u043e\u0432\u0430\u043d\u0438\u0435 \u0438\u0433\u0440\u043e\u0432\u044b\u0445 \u0430\u043a\u043a\u0430\u0443\u043d\u0442\u043e\u0432",
        });
        await keyboard.waitFor({state: "visible", timeout: 30000});
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
                    posts.posts[postId].props.mm_blocks
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
        if (!accountsPost.message.includes("\u0418\u0433\u0440\u043e\u0432\u044b\u0435 \u0430\u043a\u043a\u0430\u0443\u043d\u0442\u044b")) {
            throw new Error("The Accounts command did not create the expected bot post");
        }
        const unchangedHomePost = await api(`/posts/${homePostId}`);
        if (!unchangedHomePost.message.includes("\u0410\u0434\u043c\u0438\u043d\u0438\u0441\u0442\u0440\u0438\u0440\u043e\u0432\u0430\u043d\u0438\u0435 \u0438\u0433\u0440\u043e\u0432\u044b\u0445 \u0430\u043a\u043a\u0430\u0443\u043d\u0442\u043e\u0432")) {
            throw new Error("Reply-keyboard navigation modified the previous bot post");
        }
        await page.screenshot({path: path.join(output, "accounts.png"), fullPage: false});

        testPostId = await clickKeyboard("\u041e\u0431\u0437\u043e\u0440");
        post = await locatePost(testPostId);
        homeHeading = post.getByText("\u0410\u0434\u043c\u0438\u043d\u0438\u0441\u0442\u0440\u0438\u0440\u043e\u0432\u0430\u043d\u0438\u0435 \u0438\u0433\u0440\u043e\u0432\u044b\u0445 \u0430\u043a\u043a\u0430\u0443\u043d\u0442\u043e\u0432", {exact: true});
        await homeHeading.waitFor({state: "visible", timeout: 30000});
        testPostId = await clickKeyboard("\u041f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u0442\u0435\u043b\u0438");
        post = await locatePost(testPostId);
        const usersHeading = post.getByText("\u041f\u043e\u043b\u044c\u0437\u043e\u0432\u0430\u0442\u0435\u043b\u0438", {exact: true}).first();
        await usersHeading.waitFor({state: "visible", timeout: 30000});
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
        await post.getByRole("button", {name: "\u041d\u0430\u0437\u0430\u0434", exact: true}).click();
        homeHeading = post.getByText("\u0410\u0434\u043c\u0438\u043d\u0438\u0441\u0442\u0440\u0438\u0440\u043e\u0432\u0430\u043d\u0438\u0435 \u0438\u0433\u0440\u043e\u0432\u044b\u0445 \u0430\u043a\u043a\u0430\u0443\u043d\u0442\u043e\u0432", {exact: true});
        await homeHeading.waitFor({state: "visible", timeout: 30000});
        const outdatedAction = page.getByText("This dialog action is outdated", {exact: true});
        if (await outdatedAction.isVisible().catch(() => false)) {
            throw new Error("Closing the native form left the source post actions outdated");
        }
        await page.screenshot({path: path.join(output, "user-after-cancel.png"), fullPage: false});

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
            keyboard_visible: true,
            keyboard_navigation: true,
            keyboard_new_posts: new Set(botPostIds).size === 4,
            bot_dm: channelId !== state.channel_id && channelUrl.includes("/messages/@"),
            form_cancel_restored_source: true,
            browser_errors: browserErrors,
            screenshots: [
                path.join(output, "home.png"),
                path.join(output, "accounts.png"),
                path.join(output, "user-form.png"),
                path.join(output, "user-after-cancel.png"),
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
        await browser.close();
    }
}


main().catch((error) => {
    console.error(error);
    process.exitCode = 1;
});
