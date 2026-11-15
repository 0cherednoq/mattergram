from __future__ import annotations

import copy
import unittest

from mattergram.exceptions import UnsupportedCapabilityError
from mattergram.http import CallbackSigner
from mattergram.ui import UIConversionError, UIProfile, from_adaptive_cards, from_slack_blocks
from mattergram.ui.actions import ActionContext, External, OpenURL
from mattergram.ui.blocks import compile_post_ui


def plain(text):
    return {"type": "plain_text", "text": text}


def profile():
    return UIProfile(
        "12.0.2",
        client="declared-test-client",
        client_features=frozenset({"blocks"}),
        client_evidence="unit fixture, not real UI proof",
        mm_blocks_enabled=True,
    )


def card(body=None, actions=None, version="1.5"):
    result = {"type": "AdaptiveCard", "version": version, "body": body or []}
    if actions is not None:
        result["actions"] = actions
    return result


class UIImporterTests(unittest.TestCase):
    def setUp(self):
        self.action = External(url="https://integration.test/callback", data="request:approve:42")
        self.signer = CallbackSigner(
            instance_id="fixture-server", bot_id="fixture-bot", keys={"one": b"k" * 32}, active_key_id="one"
        )
        self.context = ActionContext(self.signer, "fixture-channel")

    def compile(self, blocks):
        return compile_post_ui(blocks=blocks, profile=profile(), context=self.context)

    def test_slack_sections_fields_accessories_and_action_rows_share_signed_registry(self):
        option = {"text": plain("Yes"), "value": "yes"}
        source = [
            {"type": "header", "text": plain("Review *literal*")},
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": "*Bold* and _italic_ and ~removed~"},
                "fields": [plain("Left"), plain("Right"), plain("Remainder")],
                "accessory": {
                    "type": "button",
                    "text": plain("Approve"),
                    "action_id": "approve",
                    "value": self.action.data,
                },
            },
            {"type": "divider"},
            {
                "type": "image",
                "image_url": "https://images.test/logo.png",
                "alt_text": "Logo",
                "title": plain("Title"),
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "static_select",
                        "action_id": "pick",
                        "placeholder": plain("Pick"),
                        "options": [option],
                        "initial_option": option,
                    },
                    {"type": "button", "text": plain("Docs"), "url": "https://docs.test/"},
                ],
            },
        ]
        frozen = copy.deepcopy(source)
        props = self.compile(from_slack_blocks(source, actions={"approve": self.action, "pick": self.action}))
        self.assertEqual(source, frozen)
        self.assertEqual(props["mm_blocks"][0]["text"], r"# Review \*literal\*")
        columns = props["mm_blocks"][1]["content"][0]["columns"]
        self.assertEqual(columns[0]["items"][0]["text"], "**Bold** and _italic_ and ~~removed~~")
        self.assertEqual(columns[0]["items"][1]["type"], "column_set")
        self.assertEqual(columns[0]["items"][2]["text"], "Remainder")
        self.assertEqual(columns[1]["items"][0]["action_id"], "approve")
        self.assertEqual(props["mm_blocks"][3]["size"], "large")
        self.assertEqual(props["mm_blocks"][4]["content"][0]["initial_option"], "yes")
        for identifier in ("approve", "pick"):
            context = copy.deepcopy(props["mm_blocks_actions"][identifier]["context"])
            if identifier == "pick":
                context["selected_option"] = "yes"
            verified = self.signer.verify({"channel_id": "fixture-channel", "context": context})
            self.assertEqual(verified["data"], self.action.data)
        self.assertEqual(len(props["mm_blocks_actions"]), 3)
        self.assertNotIn("request:approve:42", str(props["mm_blocks"]))

    def test_slack_text_formats_escaping_code_links_entities_and_explicit_workspace_renderer(self):
        source = [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": "`*code*` <https://docs.test/?a=1&amp;b=2|Docs> &lt;literal&gt;\n> quote",
                },
            }
        ]
        text = self.compile(from_slack_blocks(source))["mm_blocks"][0]["content"][0]["text"]
        self.assertIn("`*code*` [Docs](<https://docs.test/?a=1&b=2>)", text)
        self.assertIn(r"\<literal\>", text)
        self.assertTrue(text.endswith("\n> quote"))
        for token in ("<@U123>", "<!channel>", "<!date^1^{date}|fallback>", "@alice", "#general"):
            with self.subTest(token=token):
                data = [{"type": "section", "text": {"type": "mrkdwn", "text": token}}]
                with self.assertRaises(UIConversionError):
                    from_slack_blocks(data)
                result = from_slack_blocks(data, mrkdwn_renderer=lambda _: "resolved by application")
                self.assertEqual(result[0].content[0].text, "resolved by application")
        with self.assertRaises(UIConversionError) as error:
            from_slack_blocks(
                source, mrkdwn_renderer=lambda _: (_ for _ in ()).throw(ValueError("private-secret"))
            )
        self.assertNotIn("private-secret", str(error.exception))

    def test_slack_accessory_image_and_select_are_native_and_keep_option_text_plain(self):
        source = [
            {
                "type": "section",
                "text": plain("Image"),
                "accessory": {"type": "image", "image_url": "https://images.test/a.png", "alt_text": "A"},
            },
            {
                "type": "section",
                "text": plain("Pick"),
                "accessory": {
                    "type": "static_select",
                    "action_id": "pick",
                    "placeholder": plain("Pick"),
                    "options": [{"text": plain("*literal*"), "value": "yes"}],
                },
            },
            {"type": "markdown", "text": "*Markdown*"},
        ]
        props = self.compile(from_slack_blocks(source, actions={"pick": self.action}))
        self.assertEqual(props["mm_blocks"][0]["content"][0]["columns"][1]["items"][0]["size"], "small")
        self.assertEqual(
            props["mm_blocks"][1]["content"][0]["columns"][1]["items"][0]["options"][0]["text"], "*literal*"
        )
        self.assertEqual(props["mm_blocks"][2]["text"], "**Markdown**")

    def test_adaptive_layout_images_actions_and_plain_titles_keep_native_semantics(self):
        source = [
            card(
                [
                    {
                        "type": "Container",
                        "items": [
                            {
                                "type": "TextBlock",
                                "text": "**Review**",
                                "wrap": True,
                                "size": "Small",
                                "isSubtle": False,
                            }
                        ],
                    },
                    {
                        "type": "ColumnSet",
                        "columns": [
                            {
                                "type": "Column",
                                "items": [
                                    {
                                        "type": "Image",
                                        "url": "https://images.test/a.png",
                                        "altText": "A",
                                        "size": "Small",
                                        "style": "person",
                                        "width": "120px",
                                        "height": "80px",
                                        "horizontalAlignment": "Right",
                                    }
                                ],
                            },
                            {
                                "type": "Column",
                                "width": "auto",
                                "items": [
                                    {
                                        "type": "ActionSet",
                                        "actions": [
                                            {
                                                "type": "Action.Submit",
                                                "title": "*literal*",
                                                "id": "approve",
                                                "data": self.action.data,
                                                "style": "positive",
                                                "tooltip": "Approve request",
                                                "isEnabled": False,
                                                "associatedInputs": "none",
                                            }
                                        ],
                                    }
                                ],
                            },
                        ],
                    },
                ],
                [{"type": "Action.OpenUrl", "title": "Docs", "id": "docs", "url": "https://docs.test/"}],
            )
        ]
        frozen = copy.deepcopy(source)
        props = self.compile(from_adaptive_cards(source, actions={"approve": self.action}))
        self.assertEqual(source, frozen)
        self.assertEqual(props["mm_blocks"][0]["content"][0]["size"], "small")
        columns = props["mm_blocks"][1]["columns"]
        self.assertEqual(columns[0]["width"], "stretch")
        self.assertEqual(columns[0]["items"][0]["max_width"], 120)
        self.assertEqual(columns[0]["items"][0]["max_height"], 80)
        self.assertEqual(columns[0]["items"][0]["horizontal_alignment"], "right")
        button = columns[1]["items"][0]["content"][0]
        self.assertTrue(button["disabled"])
        self.assertEqual(button["text"], r"\*literal\*")
        self.assertEqual(button["style"], "primary")
        self.assertEqual(button["action_id"], "approve")
        self.assertEqual(props["mm_blocks_actions"]["docs"]["url"], "https://docs.test/")
        verified = self.signer.verify(
            {
                "channel_id": "fixture-channel",
                "context": props["mm_blocks_actions"]["approve"]["context"],
            }
        )
        self.assertEqual(verified["data"], self.action.data)

    def test_embedded_data_requires_exact_explicit_binding_and_never_becomes_an_action_id(self):
        submit = {"type": "Action.Submit", "id": "approve", "title": "Approve", "data": {"approved": True}}
        action = External(url="https://integration.test/native", context={"approved": True})
        declarations = from_adaptive_cards([card(actions=[submit])], actions={"approve": action})
        action.context["approved"] = False
        self.assertTrue(self.compile(declarations)["mm_blocks_actions"]["approve"]["context"]["approved"])
        for binding in (
            self.action,
            OpenURL(url="https://docs.test"),
            External(url="https://integration.test/native", context={"approved": 1}),
        ):
            with self.subTest(binding=type(binding).__name__):
                with self.assertRaises(UIConversionError):
                    from_adaptive_cards([card(actions=[submit])], actions={"approve": binding})
        bad = {
            "type": "button",
            "action_id": "approve",
            "text": plain("Approve"),
            "value": "private-business-value",
        }
        with self.assertRaises(UIConversionError) as error:
            from_slack_blocks([{"type": "actions", "elements": [bad]}], actions={"approve": self.action})
        self.assertNotIn("private-business-value", str(error.exception))

    def test_missing_unused_duplicate_oversize_and_conflicting_action_ids_fail(self):
        button = {"type": "button", "action_id": "approve", "text": plain("Approve")}
        for blocks, actions in (
            ([{"type": "actions", "elements": [button]}], {}),
            ([], {"unused": self.action}),
            ([{"type": "actions", "elements": [button, button]}], {"approve": self.action}),
            (
                [{"type": "actions", "elements": [{**button, "action_id": "request:approve:42"}]}],
                {"request:approve:42": self.action},
            ),
            (
                [{"type": "actions", "elements": [{**button, "url": "https://docs.test/"}]}],
                {"approve": self.action},
            ),
        ):
            with self.subTest(blocks=blocks):
                with self.assertRaises(UIConversionError):
                    from_slack_blocks(blocks, actions=actions)
        with self.assertRaises(UIConversionError):
            from_adaptive_cards(
                [
                    card(
                        actions=[
                            {
                                "type": "Action.OpenUrl",
                                "id": "same",
                                "title": "Docs",
                                "url": "https://docs.test",
                            }
                        ]
                    )
                ]
                * 2
            )

    def test_unsupported_slack_elements_properties_and_wrong_field_types_are_never_dropped(self):
        cases = [
            {"type": "context", "elements": [plain("Hidden")]},
            {"type": "input", "element": {"type": "plain_text_input"}},
            {"type": "section", "text": plain("Text"), "expand": True},
            {"type": "section", "text": plain("Text"), "block_id": "requires-source-identity"},
            {"type": "section", "text": {"type": "plain_text", "text": "Text", "emoji": "false"}},
            {
                "type": "section",
                "text": plain("Text"),
                "accessory": {"type": "datepicker", "action_id": "date"},
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "action_id": "approve",
                        "text": plain("Approve"),
                        "confirm": {"title": plain("Confirm")},
                    }
                ],
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "static_select",
                        "action_id": "approve",
                        "placeholder": plain("Pick"),
                        "options": [{"text": plain("Yes"), "value": "yes", "description": plain("Lost")}],
                    }
                ],
            },
            {"type": "image", "image_url": "javascript:private-secret", "alt_text": "A"},
            {
                "type": "actions",
                "elements": [
                    {"type": "button", "text": plain("Docs"), "url": "https://docs.test", "action_id": None}
                ],
            },
        ]
        for source in cases:
            with self.subTest(source=source.get("type")):
                with self.assertRaises(UIConversionError) as error:
                    from_slack_blocks([source], actions={"approve": self.action})
                self.assertNotIn("private-secret", str(error.exception))
                self.assertTrue(error.exception.path.startswith("$.blocks[0]"))

    def test_unsupported_adaptive_inputs_actions_layout_and_versions_fail_at_source_path(self):
        cases = [
            card([{"type": "Input.Text", "id": "name"}]),
            card([{"type": "TextBlock", "text": "Text"}]),
            card([{"type": "TextBlock", "text": "Text", "wrap": True, "size": "Large"}]),
            card([{"type": "Container", "items": [], "selectAction": {"type": "Action.Submit"}}]),
            card([{"type": "ColumnSet", "columns": [{"type": "Column", "width": 2, "items": []}]}]),
            card([{"type": "Image", "url": "https://images.test/a.png", "width": "50%"}]),
            card(actions=[{"type": "Action.ShowCard", "title": "Child", "card": card()}]),
            card(
                actions=[{"type": "Action.Submit", "id": "approve", "title": "Approve", "isEnabled": "false"}]
            ),
            card(
                actions=[
                    {
                        "type": "Action.OpenUrl",
                        "title": "Docs",
                        "url": "https://docs.test",
                        "mode": "secondary",
                    }
                ]
            ),
            card(
                actions=[
                    {
                        "type": "Action.OpenUrl",
                        "title": "Docs",
                        "url": "https://docs.test",
                        "tooltip": "Newer",
                    }
                ],
                version="1.0",
            ),
            card(version="1.6"),
            {**card(), "refresh": {}},
            card(
                actions=[{"type": "Action.OpenUrl", "title": "Docs", "url": "https://docs.test", "id": None}]
            ),
        ]
        for source in cases:
            with self.subTest(source=source):
                with self.assertRaises(UIConversionError) as error:
                    from_adaptive_cards([source], actions={"approve": self.action})
                self.assertTrue(error.exception.path.startswith("$.cards[0]"))

    def test_json_cycles_nonfinite_values_and_resource_limits_fail_with_bounded_errors(self):
        cyclic = []
        cyclic.append(cyclic)
        nested = {"type": "Container", "items": []}
        for _ in range(70):
            nested = {"type": "Container", "items": [nested]}
        for source in (
            cyclic,
            [{"x": float("nan")}],
            [object()],
            [{1: "not-string"}],
            [card([nested])],
            [{"text": "a" * 1048577}],
        ):
            with self.subTest(kind=type(source[0]).__name__):
                with self.assertRaises(UIConversionError):
                    from_adaptive_cards(source)

    def test_native_capability_registry_and_output_size_limits_still_apply_after_import(self):
        blocks = from_slack_blocks([{"type": "section", "text": plain("Visible")}])
        for target in (
            UIProfile("11.1.0"),
            UIProfile("12.0.2"),
            UIProfile("12.0.2", mm_blocks_enabled=False),
        ):
            with self.subTest(target=target):
                with self.assertRaises(UnsupportedCapabilityError):
                    compile_post_ui(blocks=blocks, profile=target)
        with self.assertRaises(ValueError):
            self.compile(from_slack_blocks([{"type": "section", "text": plain("Visible")}] * 51))
        with self.assertRaises(ValueError):
            self.compile(
                from_adaptive_cards([card([{"type": "TextBlock", "text": "x" * 16001, "wrap": True}])])
            )
        with self.assertRaises(ValueError):
            self.compile(
                from_adaptive_cards(
                    [card([{"type": "TextBlock", "text": "[Run](mmaction://unbound)", "wrap": True}])]
                )
            )
