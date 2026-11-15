from __future__ import annotations

import json
import re
from dataclasses import dataclass

from mattergram.types.http import CallbackQuery
from mattergram.ui.actions import (
    ActionContext,
    Actions,
    Attachment,
    Button,
    External,
)
from mattergram.ui.actions import (
    Select as ActionSelect,
)
from mattergram.ui.blocks import (
    Block,
    Column,
    ColumnSet,
    Container,
    StaticSelect,
)
from mattergram.ui.blocks import (
    Button as BlockButton,
)
from mattergram.ui.blocks import (
    Text as BlockText,
)
from mattergram.ui.capabilities import UIProfile
from mattergram_dialog.rendering import BoundActions, PostControl, PostControlGroup

_KIND = "mattergram_dialog.action.v2"
_LEGACY_KIND = "mattergram_dialog.transition.v1"
_ACTION = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
_NAVIGATION_ACTIONS = frozenset({"switch", "back", "next"})


@dataclass(frozen=True, slots=True)
class DialogAction:
    action: str
    intent_id: str | None
    source_state: str | None
    target_state: str | None = None
    widget_id: str | None = None
    value: str | None = None


class OpenFormActions:
    """Compile dialog transition controls to signed native Mattermost post actions."""

    def __init__(
        self,
        callback_url: str,
        context: ActionContext,
        *,
        ui_profile: UIProfile | None = None,
    ) -> None:
        self.callback_url = callback_url
        self.context = context
        self.ui_profile = ui_profile

    def __call__(self, controls: tuple[PostControl, ...] | tuple[PostControlGroup, ...]) -> BoundActions:
        def payload(control: PostControl) -> str:
            value: dict[str, object] = {
                "kind": _KIND,
                "action": control.action,
                "intent": control.intent_id,
                "source": control.source_state,
            }
            if control.target_state is not None:
                value["state"] = control.target_state
            if control.widget_id is not None:
                value["widget"] = control.widget_id
            if control.value is not None:
                value["value"] = control.value
            return json.dumps(value, ensure_ascii=True, separators=(",", ":"))

        explicit_groups = bool(controls) and all(
            isinstance(control, PostControlGroup) for control in controls
        )
        if explicit_groups:
            declarations = tuple(control for control in controls if isinstance(control, PostControlGroup))
        elif all(isinstance(control, PostControl) for control in controls):
            flat = tuple(control for control in controls if isinstance(control, PostControl))
            declarations = (PostControlGroup(flat),) if flat else ()
        else:
            raise TypeError("Control declarations cannot mix controls and control groups")
        attachments: list[Attachment] = []
        blocks: list[Block] = []
        groups: list[Actions] = []
        use_blocks = (
            self.ui_profile is not None and self.ui_profile.capability("blocks").status == "supported"
        )
        for declaration in declarations:
            assert isinstance(declaration, PostControlGroup)

            def external(control: PostControl) -> External:
                return External(url=self.callback_url, data=payload(control))

            group = (
                Actions(
                    items=tuple(
                        ActionSelect(
                            id=control.id,
                            name=control.label,
                            disabled=control.disabled,
                            style=control.style,
                            options=control.options,
                            default_option=control.initial_option,
                            action=external(control),
                        )
                        if control.options
                        else Button(
                            id=control.id,
                            name=control.label,
                            disabled=control.disabled,
                            style=control.style,
                            action=external(control),
                        )
                        for control in declaration.controls
                    )
                )
                if declaration.controls
                else None
            )
            if group is not None:
                groups.append(group)
            if declaration.table_rows and not use_blocks:
                raise ValueError("Native action tables require a supported Mattermost Blocks profile")
            if use_blocks:
                content: list[Block] = []
                if declaration.title:
                    content.append(BlockText(text="**" + declaration.title + "**"))
                if declaration.text and declaration.table_rows:
                    content.append(BlockText(text=declaration.text))
                if declaration.table_rows:
                    content.append(
                        ColumnSet(
                            columns=tuple(
                                Column(
                                    width=("stretch" if index < declaration.table_data_columns else "auto"),
                                    items=(BlockText(text="**" + header + "**"),),
                                )
                                for index, header in enumerate(declaration.table_headers)
                            ),
                            gap="small",
                        )
                    )
                    for table_row in declaration.table_rows:
                        columns = [
                            Column(width="stretch", items=(BlockText(text=cell),)) for cell in table_row.cells
                        ]
                        columns.extend(
                            Column(
                                width="auto",
                                items=(
                                    BlockButton(
                                        action_id=control.id,
                                        text=control.label,
                                        disabled=control.disabled,
                                        style=control.style,
                                        action=External(
                                            url=self.callback_url,
                                            data=payload(control),
                                        ),
                                    ),
                                ),
                            )
                            for control in table_row.controls
                        )
                        content.append(ColumnSet(columns=tuple(columns), gap="small"))
                else:
                    for index, row in enumerate(declaration.control_rows):
                        if index == declaration.content_row_index and declaration.text:
                            content.append(BlockText(text=declaration.text))
                        content.append(
                            ColumnSet(
                                columns=tuple(
                                    Column(
                                        width=declaration.row_width,
                                        items=(
                                            StaticSelect(
                                                action_id=control.id,
                                                placeholder=control.label,
                                                options=control.options,
                                                initial_option=control.initial_option,
                                                disabled=control.disabled,
                                                action=external(control),
                                            )
                                            if control.options
                                            else BlockButton(
                                                action_id=control.id,
                                                text=control.label,
                                                disabled=control.disabled,
                                                style=control.style,
                                                action=external(control),
                                            ),
                                        ),
                                    )
                                    for control in row
                                ),
                                gap="small",
                            )
                        )
                    if declaration.content_row_index == len(declaration.control_rows) and declaration.text:
                        content.append(BlockText(text=declaration.text))
                if declaration.detail_title is not None and declaration.detail_text is not None:
                    content.append(
                        Container(
                            content=(
                                BlockText(text="**" + declaration.detail_title + "**"),
                                BlockText(text=declaration.detail_text),
                            ),
                            border=True,
                            accent_color=declaration.detail_color or declaration.color or "primary",
                            background="gray",
                            flow="vertical",
                            gap="small",
                        )
                    )
                blocks.append(
                    Container(
                        content=tuple(content),
                        border=True,
                        accent_color=declaration.color or "primary",
                        flow="vertical",
                        gap="small",
                    )
                )
            else:
                legacy_text = declaration.text
                if declaration.detail_title is not None and declaration.detail_text is not None:
                    legacy_detail = "**" + declaration.detail_title + "**\n\n" + declaration.detail_text
                    legacy_text = (
                        legacy_detail if not legacy_text else legacy_text + "\n\n---\n\n" + legacy_detail
                    )
                attachments.append(
                    Attachment(
                        title=declaration.title,
                        color=declaration.color,
                        text=legacy_text,
                        actions=group,
                    )
                )
        return BoundActions(
            actions=groups[0] if groups else Actions(),
            context=self.context,
            attachments=tuple(attachments if explicit_groups else attachments[1:]),
            blocks=tuple(blocks),
            profile=self.ui_profile if use_blocks else None,
            grouped=explicit_groups,
        )

    @staticmethod
    def target_state(data: str | None) -> str:
        action = OpenFormActions.decode(data)
        if action.action != "switch" or action.target_state is None:
            raise ValueError("Dialog action is not a state transition")
        return action.target_state

    @staticmethod
    def transition(data: str | None) -> tuple[str, str | None, str | None]:
        action = OpenFormActions.decode(data)
        if action.action != "switch" or action.target_state is None:
            raise ValueError("Dialog action is not a state transition")
        return action.target_state, action.intent_id, action.source_state

    @staticmethod
    def decode(data: str | None) -> DialogAction:
        if data is None:
            raise ValueError("OpenForm callback has no signed data")
        try:
            value = json.loads(data)
        except (TypeError, ValueError):
            raise ValueError("OpenForm callback data is invalid") from None
        if not isinstance(value, dict) or value.get("kind") not in {_KIND, _LEGACY_KIND}:
            raise ValueError("OpenForm callback data is not a supported transition")
        action = "switch" if value["kind"] == _LEGACY_KIND else value.get("action")
        if not isinstance(action, str) or not _ACTION.fullmatch(action):
            raise ValueError("Dialog action type is invalid")
        state = value.get("state")
        widget = value.get("widget")
        intent = value.get("intent")
        source = value.get("source")
        if intent is not None and (not isinstance(intent, str) or not intent):
            raise ValueError("Dialog transition intent is invalid")
        if source is not None and (not isinstance(source, str) or not source):
            raise ValueError("Dialog transition source is invalid")
        if state is not None and (not isinstance(state, str) or not state):
            raise ValueError("Dialog transition state is invalid")
        if widget is not None and (not isinstance(widget, str) or not widget):
            raise ValueError("Dialog action widget is invalid")
        if action == "switch" and state is None:
            raise ValueError("Dialog state transition has no target")
        if action not in _NAVIGATION_ACTIONS and widget is None:
            raise ValueError("Dialog widget action has no target")
        if action in _NAVIGATION_ACTIONS and widget is not None:
            raise ValueError("Dialog navigation action cannot target a widget")
        raw_value = value.get("value")
        if raw_value is not None and not isinstance(raw_value, str):
            raise ValueError("Dialog action value is invalid")
        return DialogAction(action, intent, source, state, widget, raw_value)

    @classmethod
    def matches(cls, data: str | None) -> bool:
        try:
            cls.decode(data)
        except ValueError:
            return False
        return True

    @classmethod
    def target_from_event(cls, event: CallbackQuery) -> str:
        return cls.target_state(event.data)


DialogActions = OpenFormActions

__all__ = ["DialogAction", "DialogActions", "OpenFormActions"]
