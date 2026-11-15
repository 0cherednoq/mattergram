"""Executable offline Slack/Adaptive -> signed native Blocks example.

Run: uv run --locked python examples/ui_input_formats.py
The example makes no HTTP requests. Sending these declarations additionally
requires a real, verified UIProfile and a real channel/user scope.
"""

from __future__ import annotations

from mattergram.http import CallbackSigner
from mattergram.ui import from_adaptive_cards, from_slack_blocks
from mattergram.ui.actions import ActionContext, External
from mattergram.ui.blocks import compile_post_ui
from mattergram.ui.capabilities import UIProfile


def main() -> None:
    signer = CallbackSigner(
        instance_id="offline-example-instance",
        bot_id="offline-example-bot",
        keys={"offline": b"offline-example-key-not-for-production"},
        active_key_id="offline",
    )
    action = External(url="https://integration.example/mattergram/action", data="request:approve:42")
    slack = from_slack_blocks(
        [
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "action_id": "approve",
                        "text": {"type": "plain_text", "text": "Approve"},
                        "value": "request:approve:42",
                    }
                ],
            }
        ],
        actions={"approve": action},
    )
    adaptive = from_adaptive_cards(
        [
            {
                "type": "AdaptiveCard",
                "version": "1.5",
                "body": [{"type": "TextBlock", "text": "**Request 42**", "wrap": True}],
                "actions": [
                    {
                        "type": "Action.Submit",
                        "id": "approve",
                        "title": "Approve",
                        "data": "request:approve:42",
                    }
                ],
            }
        ],
        actions={"approve": action},
    )
    # This declaration selects a serializer for the offline example. It is not
    # evidence of any installed web/desktop/mobile client's rendering support.
    profile = UIProfile(
        "12.0.2",
        client="offline-example",
        client_features=frozenset({"blocks"}),
        client_evidence="offline declaration, not UI proof",
        mm_blocks_enabled=True,
    )
    binding = ActionContext(signer, "offline-example-channel")
    for name, blocks in (("Slack", slack), ("Adaptive", adaptive)):
        props = compile_post_ui(blocks=blocks, profile=profile, context=binding)
        verified = signer.verify(
            {"channel_id": binding.channel_id, "context": props["mm_blocks_actions"]["approve"]["context"]}
        )
        assert verified["data"] == action.data
        print(f"{name}: native blocks compiled; signed callback verified")


if __name__ == "__main__":
    main()
