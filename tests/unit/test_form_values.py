from __future__ import annotations

import json
import unittest
from datetime import date, timedelta
from pathlib import Path

from mattergram.exceptions import ResponseStateError
from mattergram.types import DialogLookup, DialogRefresh, DialogSubmission
from mattergram.ui.form_snapshot import restore_form, snapshot_form
from mattergram.ui.form_values import MISSING, parse_form_values
from mattergram.ui.forms import (
    Checkbox,
    CheckboxGroup,
    CheckboxMatrix,
    Collapsible,
    DatePicker,
    DateTimePicker,
    DialogAction,
    DialogButton,
    FileInput,
    Form,
    MatrixConfig,
    MultiSelect,
    Option,
    Select,
    TextInput,
)


class FormValueTests(unittest.TestCase):
    def test_captured_web_lookup_options_are_parsed_only_in_lookup_context_and_keep_raw(self):
        fixture = Path(__file__).parent.parent / "fixtures/mattermost-12.0.2-web-lookup-values.json"
        payload = json.loads(fixture.read_text(encoding="utf-8"))
        form = Form(
            callback_id="f",
            title="F",
            elements=(
                Select(name="category", label="Category", options=(Option(text="Alpha", value="a"),)),
                MultiSelect(
                    name="many", label="Many", options=(Option(text="Alpha", value="a"),), optional=True
                ),
            ),
        )
        wire = {**payload, "many": [{"label": "Untrusted display metadata", "value": "a"}]}
        parsed = parse_form_values(form, wire, partial=True, source="lookup")
        self.assertFalse(parsed.errors)
        self.assertEqual(parsed["category"], "a")
        self.assertEqual(parsed["many"], ["a"])
        self.assertEqual(parsed.raw["category"], payload["category"])
        for source in ("submission", "refresh"):
            with self.subTest(source=source):
                self.assertEqual(
                    set(parse_form_values(form, wire, partial=True, source=source).errors),
                    {"category", "many"},
                )
        for value in (
            {"value": "a"},
            {"label": "Alpha", "value": 1},
            {"label": "Alpha", "value": "unknown"},
            {"label": "Alpha", "value": "a", "extra": "ignored?"},
        ):
            with self.subTest(value=value):
                self.assertIn(
                    "category",
                    parse_form_values(form, {"category": value}, partial=True, source="lookup").errors,
                )

    def test_captured_native_form_shapes_are_structural_and_not_implicitly_authenticated(self):
        fixtures = Path(__file__).parent.parent / "fixtures"
        for kind, model in (
            ("dialog_submission", DialogSubmission),
            ("dialog_lookup", DialogLookup),
            ("refresh", DialogRefresh),
        ):
            with self.subTest(kind=kind):
                wire = json.loads((fixtures / f"mattermost-11.1-{kind}.json").read_text(encoding="utf-8"))
                event = model.from_wire(wire)
                self.assertEqual(event.type, kind)
                self.assertEqual(event.raw["state"], "[REDACTED]")
                self.assertNotIn("removed-state", repr(event))
                with self.assertRaises(ResponseStateError):
                    _ = event.session_id

    def test_missing_empty_null_false_and_defaults_remain_distinct(self):
        form = Form(
            callback_id="f",
            title="Form",
            elements=(
                TextInput(name="missing", label="Missing", optional=True, default="must not substitute"),
                TextInput(name="empty", label="Empty", optional=True),
                TextInput(name="null", label="Null", optional=True),
                Checkbox(name="false", label="False"),
                TextInput(name="number", label="Number", subtype="number"),
            ),
        )
        values = parse_form_values(form, {"empty": "", "null": None, "false": False, "number": 12.5})
        self.assertFalse(values.errors)
        self.assertIs(values["missing"], MISSING)
        self.assertEqual(values["empty"], "")
        self.assertIsNone(values["null"])
        self.assertIs(values["false"], False)
        self.assertEqual(values["number"], "12.5")
        self.assertNotIn("12.5", repr(values))

    def test_select_arrays_are_not_confused_with_comma_defaults_or_other_text(self):
        form = Form(
            callback_id="f",
            title="F",
            elements=(
                MultiSelect(
                    name="choice",
                    label="Choice",
                    options=(Option(text="A", value="a"), Option(text="B", value="b")),
                ),
                TextInput(name="text", label="Text"),
            ),
        )
        valid = parse_form_values(form, {"choice": ["a", "b"], "text": "a,b"})
        self.assertEqual(valid["choice"], ["a", "b"])
        self.assertEqual(valid["text"], "a,b")
        for value in ("a,b", ["a", "a"], ["unknown"], [True]):
            with self.subTest(value=value):
                self.assertIn("choice", parse_form_values(form, {"choice": value, "text": "a,b"}).errors)

    def test_matrix_wire_is_an_array_of_row_entries_with_schema_validation(self):
        matrix = CheckboxMatrix(
            name="matrix",
            label="Matrix",
            matrix_config=MatrixConfig(
                rows=(Option(text="Row", value="row"),),
                columns=(Option(text="A", value="a"), Option(text="B", value="b")),
            ),
        )
        form = Form(callback_id="f", title="F", elements=(matrix,))
        self.assertEqual(parse_form_values(form, {"matrix": ["row:a,b"]})["matrix"], {"row": ["a", "b"]})
        for value in ("row:a,b", ["other:a"], ["row:a", "row:b"], ["row:unknown"]):
            with self.subTest(value=value):
                self.assertIn("matrix", parse_form_values(form, {"matrix": value}).errors)

    def test_dates_preserve_offset_and_reject_naive_or_invalid_values(self):
        form = Form(
            callback_id="f",
            title="F",
            elements=(DatePicker(name="date", label="Date"), DateTimePicker(name="time", label="Time")),
        )
        values = parse_form_values(form, {"date": "2026-11-12", "time": "2026-11-12T10:30:00+03:00"})
        self.assertEqual(values["date"], date(2026, 11, 12))
        self.assertEqual(values["time"].utcoffset(), timedelta(hours=3))
        invalid = parse_form_values(form, {"date": "2026-02-30", "time": "2026-11-12T10:30:00"})
        self.assertEqual(set(invalid.errors), {"date", "time"})

    def test_bool_parsing_does_not_use_truthiness_and_partial_lookup_skips_required(self):
        form = Form(
            callback_id="f",
            title="F",
            elements=(Checkbox(name="flag", label="Flag"), TextInput(name="text", label="Text")),
        )
        self.assertIs(parse_form_values(form, {"flag": "false"}, partial=True)["flag"], False)
        self.assertFalse(parse_form_values(form, {}, partial=True).errors)
        for value in ("False", "0", 0, [], {}):
            with self.subTest(value=value):
                self.assertIn("flag", parse_form_values(form, {"flag": value}, partial=False).errors)

    def test_global_files_are_not_assigned_to_an_individual_input(self):
        form = Form(
            callback_id="f",
            title="F",
            elements=(
                FileInput(name="one", label="One", optional=True),
                FileInput(name="two", label="Two", optional=True),
            ),
        )
        values = parse_form_values(form, {"one": "", "two": ""}, file_ids=["confirmed-file"])
        self.assertEqual(values.file_ids, ("confirmed-file",))
        self.assertEqual(values["one"], "")
        self.assertEqual(values["two"], "")

    def test_lossless_snapshot_keeps_excluded_defaults_and_nested_declarations(self):
        form = Form(
            callback_id="f",
            title="F",
            state="private application state",
            elements=(
                MultiSelect(
                    name="multi", label="Multi", options=(Option(text="A", value="a"),), default_values=("a",)
                ),
                CheckboxGroup(
                    name="group", label="Group", options=(Option(text="A", value="a"),), default_values=("a",)
                ),
                Collapsible(
                    name="section",
                    label="Section",
                    collapsed=True,
                    elements=(
                        DialogButton(
                            name="child",
                            label="Child",
                            action_button=DialogAction(
                                url="https://integration.test/dialog",
                                context={"app": "kept", "$ui": "literal"},
                            ),
                        ),
                    ),
                ),
                TextInput(name="implicit", label="Implicit"),
                TextInput(name="reset", label="Reset", default=None),
            ),
        )
        restored = restore_form(snapshot_form(form))
        self.assertEqual(restored, form)
        self.assertEqual(restored.elements[0].default_values, ("a",))
        self.assertNotIn("default", restored.elements[-2].model_fields_set)
        self.assertIn("default", restored.elements[-1].model_fields_set)
