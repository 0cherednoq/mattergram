(function mattergramMenuModule(root, factory) {
    const api = factory();
    if (typeof module === "object" && module.exports) {
        module.exports = api;
    }
    if (root && typeof root.registerPlugin === "function") {
        api.register(root);
    }
})(typeof window === "undefined" ? undefined : window, function mattergramMenuFactory() {
    "use strict";

    const PLUGIN_ID = "com.mattergram.menu";
    const STYLE_ID = "mattergram-menu-styles";
    const UPDATE_EVENT = `custom_${PLUGIN_ID}_keyboard_updated`;
    const subscribers = new Set();

    const STYLES = `
.MattergramMenu {
    box-sizing: border-box;
    display: flex;
    flex-direction: column;
    gap: 8px;
    margin: 0 0 8px;
    padding: 10px;
    border: 1px solid rgba(var(--center-channel-color-rgb, 61, 60, 64), 0.16);
    border-radius: 8px;
    background: var(--center-channel-bg, #fff);
    color: var(--center-channel-color, #3d3c40);
}
.MattergramMenu-title {
    font-size: 12px;
    font-weight: 600;
    line-height: 16px;
    opacity: 0.72;
}
.MattergramMenu-row {
    display: grid;
    grid-template-columns: repeat(var(--mattergram-menu-columns), minmax(0, 1fr));
    gap: 8px;
}
.MattergramMenu-button {
    min-width: 0;
    min-height: 36px;
    padding: 7px 12px;
    overflow: hidden;
    border: 0;
    border-radius: 6px;
    background: rgba(var(--center-channel-color-rgb, 61, 60, 64), 0.08);
    color: inherit;
    font: inherit;
    font-weight: 600;
    text-overflow: ellipsis;
    white-space: nowrap;
    cursor: pointer;
}
.MattergramMenu-button--primary {
    background: var(--button-bg, #1c58d9);
    color: var(--button-color, #fff);
}
.MattergramMenu-button--danger {
    background: var(--dnd-indicator, #d24b4e);
    color: #fff;
}
.MattergramMenu-button:hover:not(:disabled),
.MattergramMenu-button:focus-visible:not(:disabled) {
    filter: brightness(0.94);
    outline: 2px solid var(--button-bg, #1c58d9);
    outline-offset: 2px;
}
.MattergramMenu-button:disabled {
    cursor: not-allowed;
    opacity: 0.56;
}
.MattergramMenu-error {
    margin: 0 0 8px;
    color: var(--error-text, #d24b4e);
    font-size: 12px;
    line-height: 16px;
}
@media (max-width: 560px) {
    .MattergramMenu {
        gap: 6px;
        padding: 8px;
    }
    .MattergramMenu-row {
        gap: 6px;
    }
    .MattergramMenu-button {
        padding-inline: 8px;
        font-size: 13px;
    }
}`;

    function validKeyboard(value) {
        if (!value || value.schema_version !== 1 || typeof value.title !== "string" ||
            !Array.isArray(value.rows) || value.rows.length < 1 || value.rows.length > 6) {
            return false;
        }
        const identifiers = new Set();
        return value.rows.every((row) => Array.isArray(row) && row.length >= 1 && row.length <= 4 &&
            row.every((item) => {
                if (!item || !/^[A-Za-z0-9_-]{1,64}$/.test(item.id) || identifiers.has(item.id) ||
                    typeof item.text !== "string" || !item.text || typeof item.message !== "string" ||
                    !item.message || !["default", "primary", "danger"].includes(item.style) ||
                    typeof item.disabled !== "boolean") {
                    return false;
                }
                identifiers.add(item.id);
                return true;
            }));
    }

    function shouldRenderMenu(channel, keyboard) {
        return Boolean(channel && channel.id && Number(channel.delete_at || 0) === 0 && validKeyboard(keyboard));
    }

    async function responseError(response) {
        try {
            const body = await response.json();
            if (body && typeof body.message === "string" && body.message) {
                return body.message;
            }
        } catch (_error) {
            // The HTTP status below is the safe fallback for a non-JSON response.
        }
        return `Mattermost rejected the request (${response.status})`;
    }

    async function fetchKeyboard(channelId, request) {
        if (!/^[A-Za-z0-9]+$/.test(channelId || "")) {
            throw new Error("Invalid Mattermost channel ID");
        }
        const response = await request(`/plugins/${PLUGIN_ID}/api/v1/channels/${channelId}/keyboard`, {
            method: "GET",
            credentials: "same-origin",
            cache: "no-store",
            headers: {"X-Requested-With": "XMLHttpRequest"},
        });
        if (!response.ok) {
            throw new Error(await responseError(response));
        }
        const state = await response.json();
        if (state && state.keyboard === null) {
            return null;
        }
        if (!state || state.channel_id !== channelId || !Number.isInteger(state.revision) ||
            state.revision < 1 || !validKeyboard(state.keyboard)) {
            throw new Error("Mattergram Menu returned an invalid keyboard");
        }
        return state.keyboard;
    }

    async function postMessage(channelId, item, request) {
        if (!/^[A-Za-z0-9]+$/.test(channelId || "") || !item || typeof item.message !== "string" ||
            !item.message || item.disabled) {
            throw new Error("Invalid Mattergram menu button");
        }
        const response = await request("/api/v4/posts", {
            method: "POST",
            credentials: "same-origin",
            cache: "no-store",
            headers: {
                "Content-Type": "application/json",
                "X-Requested-With": "XMLHttpRequest",
            },
            body: JSON.stringify({channel_id: channelId, message: item.message}),
        });
        if (!response.ok) {
            throw new Error(await responseError(response));
        }
        return response.json();
    }

    function installStyles(documentObject) {
        if (!documentObject || documentObject.getElementById(STYLE_ID)) {
            return;
        }
        const style = documentObject.createElement("style");
        style.id = STYLE_ID;
        style.textContent = STYLES;
        documentObject.head.appendChild(style);
    }

    function subscribe(listener) {
        subscribers.add(listener);
        return () => subscribers.delete(listener);
    }

    function notifySubscribers(channelId) {
        subscribers.forEach((listener) => listener(channelId));
    }

    function createMenuComponent(React, request) {
        const element = React.createElement;
        return function MattergramMenu(props) {
            const channel = props.channel;
            const channelId = channel && channel.id;
            const [keyboard, setKeyboard] = React.useState(null);
            const [pending, setPending] = React.useState("");
            const [error, setError] = React.useState("");

            React.useEffect(() => {
                let active = true;
                const load = async () => {
                    if (!channelId || Number(channel.delete_at || 0) > 0) {
                        if (active) {
                            setKeyboard(null);
                            setError("");
                        }
                        return;
                    }
                    try {
                        const next = await fetchKeyboard(channelId, request);
                        if (active) {
                            setKeyboard(next);
                            setError("");
                        }
                    } catch (loadError) {
                        if (active) {
                            setKeyboard(null);
                            setError(loadError instanceof Error ? loadError.message : "Keyboard could not be loaded");
                        }
                    }
                };
                const unsubscribe = subscribe((updatedChannelId) => {
                    if (!updatedChannelId || updatedChannelId === channelId) {
                        void load();
                    }
                });
                void load();
                return () => {
                    active = false;
                    unsubscribe();
                };
            }, [channelId]);

            if (!shouldRenderMenu(channel, keyboard)) {
                return error ? element("div", {
                    className: "MattergramMenu-error",
                    role: "alert",
                    "aria-live": "polite",
                }, error) : null;
            }

            const send = async (item) => {
                if (pending || item.disabled) {
                    return;
                }
                setPending(item.id);
                setError("");
                try {
                    await postMessage(channelId, item, request);
                } catch (sendError) {
                    setError(sendError instanceof Error ? sendError.message : "Message was not sent");
                } finally {
                    setPending("");
                }
            };

            return element(
                "section",
                {className: "MattergramMenu", "aria-label": keyboard.title},
                element("div", {className: "MattergramMenu-title"}, keyboard.title),
                ...keyboard.rows.map((row, rowIndex) => element(
                    "div",
                    {
                        className: "MattergramMenu-row",
                        key: `row-${rowIndex}`,
                        style: {"--mattergram-menu-columns": String(row.length)},
                    },
                    ...row.map((item) => element(
                        "button",
                        {
                            className: `MattergramMenu-button MattergramMenu-button--${item.style}`,
                            disabled: Boolean(pending) || item.disabled,
                            key: item.id,
                            onClick: () => send(item),
                            title: item.message,
                            type: "button",
                            "aria-label": `${item.text}: send ${item.message}`,
                        },
                        pending === item.id ? "Sending…" : item.text,
                    )),
                )),
                error ? element("div", {
                    className: "MattergramMenu-error",
                    role: "alert",
                    "aria-live": "polite",
                }, error) : null,
            );
        };
    }

    function register(host) {
        if (!host.React || typeof host.fetch !== "function") {
            throw new Error("Mattergram Menu requires Mattermost webapp React and fetch");
        }
        const component = createMenuComponent(host.React, host.fetch.bind(host));
        class MattergramMenuPlugin {
            initialize(registry) {
                if (typeof registry.registerChannelComposerBannerComponent !== "function" ||
                    typeof registry.registerWebSocketEventHandler !== "function") {
                    throw new Error("Mattergram Menu requires Mattermost 11.9 or newer");
                }
                installStyles(host.document);
                registry.registerChannelComposerBannerComponent(component);
                registry.registerWebSocketEventHandler(UPDATE_EVENT, (event) => {
                    notifySubscribers(event && event.data && event.data.channel_id);
                });
                if (typeof registry.registerReconnectHandler === "function") {
                    registry.registerReconnectHandler(() => notifySubscribers(""));
                }
            }
        }
        host.registerPlugin(PLUGIN_ID, new MattergramMenuPlugin());
    }

    return Object.freeze({
        PLUGIN_ID,
        UPDATE_EVENT,
        createMenuComponent,
        fetchKeyboard,
        installStyles,
        notifySubscribers,
        postMessage,
        register,
        shouldRenderMenu,
        validKeyboard,
    });
});
