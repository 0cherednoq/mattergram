from __future__ import annotations

import json
import unittest

from pydantic import ValidationError

from mattergram.exceptions import UnsupportedCapabilityError
from mattergram.http import CallbackSigner
from mattergram.ui import UIProfile
from mattergram.ui.actions import ActionContext, Actions, Attachment, AttachmentField, External, OpenURL
from mattergram.ui.actions import Button as LegacyButton
from mattergram.ui.actions import Select as LegacySelect
from mattergram.ui.blocks import (
    Button,
    Collapsible,
    Column,
    ColumnSet,
    Container,
    Divider,
    Image,
    StaticSelect,
    Text,
    compile_post_ui,
)
from mattergram.ui.capabilities import SERVER_FEATURES
from mattergram.ui.forms import (
    Checkbox,
    CheckboxGroup,
    CheckboxMatrix,
    DatePicker,
    DateTimePicker,
    DialogAction,
    DialogButton,
    FileInput,
    Form,
    MatrixConfig,
    MultiSelect,
    Option,
    Radio,
    Select,
    TextArea,
    TextInput,
)
from mattergram.ui.forms import (
    Collapsible as FieldGroup,
)


def profile(version="12.0.2"):
    return UIProfile(
        version,
        client="declared-test-client",
        client_features=SERVER_FEATURES[version],
        client_evidence="test fixture, not UI proof",
        mm_blocks_enabled=True,
    )


class UISchemaTests(unittest.TestCase):
    def test_catalog_serializes_all_native_field_types_without_invented_wire_types(self):
        options = [Option(text="Yes", value="yes"), Option(text="No", value="no")]
        form = Form(
            callback_id="form",
            title="Form",
            elements=[
                TextInput(name="text", label="Text", subtype="number", min_length=1),
                TextArea(name="area", label="Area", default="long text"),
                Select(name="select", label="Select", options=options),
                MultiSelect(name="multi", label="Multi", options=options, default_values=["yes", "no"]),
                Checkbox(name="bool", label="Bool", default=False, label_position="after"),
                Radio(name="radio", label="Radio", options=options),
                CheckboxGroup(name="group", label="Group", options=options, default_values=["yes"]),
                CheckboxMatrix(
                    name="matrix",
                    label="Matrix",
                    matrix_config=MatrixConfig(rows=options, columns=options),
                    default_values={"yes": ["no"]},
                ),
                DatePicker(name="date", label="Date", default="2026-11-12", min_date="today"),
                DateTimePicker(
                    name="dt",
                    label="Datetime",
                    default="2026-11-12T10:30:00+03:00",
                    time_interval=30,
                    location_timezone="Europe/Simferopol",
                    manual_time_entry=True,
                ),
                FileInput(name="file", label="File", allow_multiple=True),
                DialogButton(
                    name="child",
                    label="Child",
                    action_button=DialogAction(
                        url="https://integration.test/child", context={"key": "value"}
                    ),
                ),
                FieldGroup(
                    name="section", label="Section", elements=[TextInput(name="nested", label="Nested")]
                ),
            ],
        )
        wire = form.to_wire(profile())
        self.assertEqual(
            [e["type"] for e in wire["elements"]],
            [
                "text",
                "textarea",
                "select",
                "select",
                "bool",
                "radio",
                "checkbox_group",
                "checkbox_matrix",
                "date",
                "datetime",
                "file",
                "action_button",
                "collapsible",
            ],
        )
        self.assertEqual(wire["elements"][3]["default"], "yes,no")
        self.assertTrue(wire["elements"][3]["multiselect"])
        self.assertEqual(wire["elements"][4]["default"], "false")
        self.assertEqual(wire["elements"][7]["default"], "yes:no")
        self.assertEqual(wire["elements"][8]["datetime_config"], {"min_date": "today"})
        self.assertEqual(wire["elements"][-1]["collapsible_config"]["elements"][0]["name"], "nested")
        self.assertNotIn("default_values", json.dumps(wire))

    def test_versioned_datetime_shapes_and_title_limits(self):
        value = DateTimePicker(name="dt", label="Datetime", min_date="today", time_interval=30)
        self.assertEqual(value.to_wire(profile("11.1.0"))["time_interval"], 30)
        old = value.model_copy(update={"manual_time_entry": False})
        wire = old.to_wire(profile("11.7.0"))
        self.assertEqual(wire["datetime_config"], {"time_interval": 30, "allow_manual_time_entry": False})
        self.assertEqual(wire["min_date"], "today")
        current = old.to_wire(profile())
        self.assertEqual(
            current["datetime_config"], {"min_date": "today", "time_interval": 30, "manual_time_entry": False}
        )
        self.assertNotIn("min_date", current)
        long = Form(callback_id="form", title="x" * 40)
        long.to_wire(profile())
        with self.assertRaises(ValueError):
            long.to_wire(profile("11.11.0"))
        with self.assertRaises(UnsupportedCapabilityError):
            FileInput(name="f", label="File").to_wire(profile("11.7.0"))

    def test_unknown_client_disabled_flags_and_known_unsupported_cannot_silently_pass(self):
        for target in (
            UIProfile("12.0.2"),
            UIProfile("99.0", client_features=frozenset({"blocks"}), mm_blocks_enabled=True),
            UIProfile("12.0.2", client_features=frozenset({"blocks"}), mm_blocks_enabled=False),
        ):
            with self.subTest(target=target):
                with self.assertRaises(UnsupportedCapabilityError):
                    compile_post_ui(blocks=[Text(text="visible")], profile=target)
        UIProfile("unknown", overrides=frozenset({"blocks"})).require("blocks")
        with self.assertRaises(UnsupportedCapabilityError):
            UIProfile("11.1.0", overrides=frozenset({"blocks"})).require("blocks")

    def test_wrong_kwargs_duplicate_names_and_refresh_without_source_fail_early(self):
        constructors = [
            lambda: Checkbox(name="a", label="A", options=[]),
            lambda: TextInput(name="a", label="A", min=0),
            lambda: Select(name="a", label="A", options=[Option(text="X", value="x")], data_source="users"),
            lambda: Select(name="a", label="A", data_source="dynamic"),
            lambda: Form(
                callback_id="f",
                title="F",
                elements=[
                    TextInput(name="a", label="A"),
                    FieldGroup(name="b", label="B", elements=[TextInput(name="a", label="Duplicate")]),
                ],
            ),
            lambda: Form(
                callback_id="f",
                title="F",
                elements=[Select(name="a", label="A", data_source="users", refresh=True)],
            ),
            lambda: TextInput(name="a", label="я" * 13),
        ]
        for constructor in constructors:
            with self.subTest(constructor=constructor):
                with self.assertRaises(ValidationError):
                    constructor()

    def test_datetime_and_delimited_defaults_reject_ambiguous_values(self):
        for kwargs in (
            {"time_interval": 7},
            {"default": "2026-11-12T10:17:00Z", "time_interval": 30},
            {"default": "2026-11-12T10:00:00"},
            {"default": "2026-11-12"},
        ):
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValidationError):
                    DateTimePicker(name="dt", label="Date", **kwargs)
        with self.assertRaises(ValidationError):
            MultiSelect(name="s", label="Select", options=[Option(text="A", value="a,b")])
        with self.assertRaises(ValidationError):
            CheckboxMatrix(
                name="m",
                label="M",
                matrix_config=MatrixConfig(
                    rows=[Option(text="R", value="r")],
                    columns=[Option(text="C", value="c")],
                    row_selection="single",
                ),
                default_values={"r": ["c", "c"]},
            )

    def test_full_block_tree_preserves_layout_fields_and_reconciles_registry(self):
        action = OpenURL(url="/channels/town-square")
        result = compile_post_ui(
            profile=profile(),
            blocks=[
                Container(
                    flow="horizontal",
                    border=True,
                    accent_color="primary",
                    gap="large",
                    background="gray",
                    max_height="small",
                    content=[
                        Text(text="**Hello**", is_subtle=True, size="small"),
                        Image(
                            url="https://images.test/logo.png",
                            max_width=200,
                            alt_text="Logo",
                            image_style="person",
                        ),
                        Button(text="Open", action=action, tooltip="Open channel"),
                    ],
                ),
                Collapsible(
                    header=[Text(text="More")],
                    content=[
                        Divider(),
                        ColumnSet(
                            columns=[
                                Column(
                                    items=[
                                        StaticSelect(
                                            placeholder="Pick",
                                            options=[Option(text="A", value="a")],
                                            action=action,
                                        )
                                    ]
                                )
                            ]
                        ),
                    ],
                ),
            ],
        )
        self.assertEqual(result["mm_blocks"][0]["flow"], "horizontal")
        self.assertEqual(result["mm_blocks"][0]["content"][1]["max_width"], 200)
        self.assertEqual(len(result["mm_blocks_actions"]), 2)
        for identifier in result["mm_blocks_actions"]:
            self.assertRegex(identifier, r"^[A-Za-z0-9_-]{1,64}$")

    def test_callback_data_is_signed_context_separate_from_technical_id_and_selected_value(self):
        signer = CallbackSigner(
            instance_id="https://mattermost.test",
            bot_id="bot",
            keys={"one": b"x" * 32},
            active_key_id="one",
            clock=lambda: 1000,
        )
        action = External(url="https://integration.test/callback", data="request:approve:123")
        props = compile_post_ui(
            profile=profile(),
            context=ActionContext(signer, "channel", "post", "user"),
            blocks=[
                StaticSelect(placeholder="Pick", options=[Option(text="Yes", value="yes")], action=action)
            ],
        )
        identifier, definition = next(iter(props["mm_blocks_actions"].items()))
        self.assertNotIn(":", identifier)
        definition["context"]["selected_option"] = "yes"
        wire = {
            "channel_id": "channel",
            "post_id": "post",
            "user_id": "user",
            "context": definition["context"],
        }
        verified = signer.verify(wire)
        self.assertEqual(verified["data"], "request:approve:123")
        self.assertEqual(verified["selected_option"], "yes")
        with self.assertRaises(ValueError):
            compile_post_ui(profile=profile(), blocks=[Button(text="A", action=action)])

    def test_registry_missing_unused_duplicate_and_invalid_placement_are_errors(self):
        action = OpenURL(url="https://example.test")
        cases = [
            {"blocks": [Text(text="[Run](mmaction://missing)")]},
            {"blocks": [Text(text="None")], "markdown_actions": {"unused": action}},
            {
                "blocks": [
                    Button(text="A", action=action, action_id="same"),
                    Button(text="B", action=action, action_id="same"),
                ]
            },
            {"blocks": [Button(text="A", action=action, action_id="request:approve")]},
            {"blocks": [Column(items=[Text(text="No parent")])]},
            {"blocks": [Text(text="x")] * 101},
            {
                "blocks": [
                    Button(text=str(index), action=action, action_id=f"action{index}") for index in range(51)
                ]
            },
        ]
        for kwargs in cases:
            with self.subTest(kwargs=kwargs):
                with self.assertRaises(ValueError):
                    compile_post_ui(profile=profile(), **kwargs)
        props = compile_post_ui(
            profile=profile(), message="[Run](mmaction://valid)", markdown_actions={"valid": action}
        )
        self.assertEqual(props["mm_blocks_actions"]["valid"]["type"], "openURL")
        disabled = compile_post_ui(
            profile=profile(),
            blocks=[Button(text="Unavailable", action=action, action_id="disabled", disabled=True)],
        )
        self.assertEqual(disabled["mm_blocks"][0]["action_id"], "disabled")
        self.assertEqual(disabled["mm_blocks_actions"]["disabled"]["type"], "openURL")

    def test_rich_attachment_and_legacy_actions_use_same_signing_compiler(self):
        numeric = Attachment(fields=[AttachmentField(title="Count", value=3)])
        self.assertEqual(numeric.to_wire()["fields"][0]["value"], 3)
        with self.assertRaises(ValidationError):
            Attachment(author_name="Author", author_link="/plugins/relative")
        action = External(url="https://integration.test/action", context={"native": "context"})
        props = compile_post_ui(
            attachments=[
                Attachment(
                    fallback="Summary",
                    author_name="Author",
                    author_link="https://example.test",
                    title="Title",
                    fields=[AttachmentField(title="Status", value="Ready", short=True)],
                    actions=Actions(
                        items=[LegacyButton(id="legacy", name="Run", style="primary", action=action)]
                    ),
                )
            ]
        )
        attachment = props["attachments"][0]
        self.assertEqual(attachment["fields"][0]["value"], "Ready")
        self.assertEqual(attachment["actions"][0]["integration"]["context"], {"native": "context"})
        duplicate = Actions(items=[LegacyButton(id="duplicate", name="Run", action=action)])
        with self.assertRaises(ValueError):
            compile_post_ui(actions=duplicate, attachments=[Attachment(actions=duplicate)])
        self.assertNotIn("action", attachment["actions"][0])

    def test_legacy_action_ids_match_versioned_native_route_without_rewriting(self):
        action = External(url="https://integration.test/action", context={"native": "context"})
        for kind in (LegacyButton, LegacySelect):
            extra = {"options": (Option(text="Alpha", value="a"),)} if kind is LegacySelect else {}
            for invalid in ("", "has space", "slash/id", "dot.id", "nonascii-é"):
                with self.subTest(kind=kind.__name__, invalid=invalid):
                    with self.assertRaises(ValidationError):
                        kind(id=invalid, name="Run", action=action, **extra)
            item = kind(id="route-ID_1", name="Run", action=action, **extra)
            for target in (None, UIProfile("11.1.0"), UIProfile("11.7.0"), UIProfile("unreferenced")):
                with self.subTest(kind=kind.__name__, target=target):
                    with self.assertRaises(UnsupportedCapabilityError):
                        compile_post_ui(actions=Actions(items=(item,)), profile=target)
            for version in ("11.11.0", "12.0.2"):
                with self.subTest(kind=kind.__name__, version=version):
                    props = compile_post_ui(
                        attachments=(Attachment(actions=Actions(items=(item,))),), profile=UIProfile(version)
                    )
                    self.assertEqual(props["attachments"][0]["actions"][0]["id"], "route-ID_1")
            native = kind(name="Run", action=action, **extra)
            self.assertNotIn("id", native.to_wire())
            portable = kind(id="Alphanumeric123", name="Run", action=action, **extra)
            self.assertEqual(portable.to_wire()["id"], "Alphanumeric123")
