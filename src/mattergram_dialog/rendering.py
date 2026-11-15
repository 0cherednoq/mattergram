from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from mattergram.bot import Bot
from mattergram.ui.actions import ActionContext, Actions, Attachment
from mattergram.ui.blocks import Block, compile_post_ui
from mattergram.ui.capabilities import UIProfile
from mattergram.ui.forms import Form, Option
from mattergram_dialog.api.entities import ShowMode, Stack
from mattergram_dialog.api.exceptions import RendererUnavailable


@dataclass(frozen=True, slots=True)
class PostControl:
    id: str
    label: str
    target_state: str | None = None
    intent_id: str | None = None
    source_state: str | None = None
    action: str = "switch"
    widget_id: str | None = None
    value: str | None = None
    style: str | None = None
    disabled: bool = False
    options: tuple[Option, ...] = ()
    initial_option: str | None = None

    def __post_init__(self) -> None:
        if self.initial_option is not None and self.initial_option not in {
            option.value for option in self.options
        }:
            raise ValueError("Post control initial option must belong to its options")


@dataclass(frozen=True, slots=True)
class PostTableRow:
    """One native Blocks table row with local actions for that record."""

    cells: tuple[str, ...]
    controls: tuple[PostControl, ...]


@dataclass(frozen=True, slots=True)
class PostControlGroup:
    """One Mattermost attachment containing local content and its actions."""

    controls: tuple[PostControl, ...]
    title: str | None = None
    color: str | None = None
    text: str | None = None
    rows: tuple[tuple[PostControl, ...], ...] = ()
    row_width: Literal["auto", "stretch"] = "auto"
    table_headers: tuple[str, ...] = ()
    table_data_columns: int = 0
    table_rows: tuple[PostTableRow, ...] = ()
    content_row_index: int = 0
    detail_title: str | None = None
    detail_text: str | None = None
    detail_color: str | None = None

    def __post_init__(self) -> None:
        if self.row_width not in {"auto", "stretch"}:
            raise ValueError("Post control row width must be auto or stretch")
        if (self.detail_title is None) != (self.detail_text is None):
            raise ValueError("Post detail title and text must be declared together")
        if self.detail_color is not None and self.detail_text is None:
            raise ValueError("Post detail color requires detail content")
        if (
            type(self.content_row_index) is not int
            or self.content_row_index < 0
            or self.content_row_index > len(self.control_rows)
        ):
            raise ValueError("Post content row index must be within the declared control rows")
        if self.table_rows:
            if self.rows:
                raise ValueError("A control group cannot mix button rows and table rows")
            if self.table_data_columns < 1:
                raise ValueError("A native table must declare at least one data column")
            if len(self.table_headers) <= self.table_data_columns:
                raise ValueError("A native table must declare data and action headers")
            action_columns = len(self.table_headers) - self.table_data_columns
            for row in self.table_rows:
                if len(row.cells) != self.table_data_columns:
                    raise ValueError("Native table row cell count does not match its headers")
                if len(row.controls) != action_columns:
                    raise ValueError("Native table row action count does not match its headers")
            flattened = tuple(control for row in self.table_rows for control in row.controls)
            if flattened != self.controls:
                raise ValueError("Native table rows must contain every group control in order")
        if self.rows and tuple(control for row in self.rows for control in row) != self.controls:
            raise ValueError("Post control rows must contain every group control in order")

    @property
    def control_rows(self) -> tuple[tuple[PostControl, ...], ...]:
        if self.rows:
            return self.rows
        return (self.controls,) if self.controls else ()


@dataclass(frozen=True, slots=True)
class BoundActions:
    actions: Actions
    context: ActionContext
    attachments: tuple[Attachment, ...] = ()
    blocks: tuple[Block, ...] = ()
    profile: UIProfile | None = None
    grouped: bool = False


@dataclass(frozen=True, slots=True)
class PostRender:
    text: str
    controls: tuple[PostControl, ...] = ()
    control_groups: tuple[PostControlGroup, ...] = ()


@dataclass(frozen=True, slots=True)
class FormRender:
    form: Form


RenderPlan = PostRender | FormRender


class Renderer(Protocol):
    async def render(self, plan: RenderPlan, stack: Stack, mode: ShowMode) -> None: ...


class MattermostRenderer:
    def __init__(
        self,
        bot: Bot,
        channel_id: str,
        *,
        root_id: str | None = None,
        open_form: Callable[[Form], Awaitable[object]] | None = None,
        actions_factory: Callable[
            [tuple[PostControl, ...] | tuple[PostControlGroup, ...]], Actions | BoundActions
        ]
        | None = None,
        update_post: Callable[[str, Mapping[str, Any] | None], Awaitable[object]] | None = None,
        ui_profile: UIProfile | None = None,
    ) -> None:
        if not channel_id:
            raise ValueError("channel_id must be nonempty")
        self.bot = bot
        self.channel_id = channel_id
        self.root_id = root_id
        self.open_form = open_form
        self.actions_factory = actions_factory
        self.update_post = update_post
        self.ui_profile = ui_profile

    async def render(self, plan: RenderPlan, stack: Stack, mode: ShowMode) -> None:
        if mode is ShowMode.NO_UPDATE:
            return
        if isinstance(plan, FormRender):
            if mode not in (ShowMode.AUTO, ShowMode.FORM):
                raise RendererUnavailable("A native form cannot be sent or edited as a post")
            if self.open_form is None:
                raise RendererUnavailable("This event cannot open a native Mattermost form")
            await self.open_form(plan.form)
            return
        if mode is ShowMode.FORM:
            raise RendererUnavailable("A post window cannot be rendered as a native form")
        actions = None
        attachments: tuple[Attachment, ...] = ()
        blocks: tuple[Block, ...] | None = None
        ui_profile = self.ui_profile
        action_context = None
        if plan.controls or plan.control_groups:
            if self.actions_factory is None:
                raise RendererUnavailable("Post controls require a signed Mattermost actions factory")
            built = self.actions_factory(plan.control_groups or plan.controls)
            if isinstance(built, BoundActions):
                action_context = built.context
                blocks = built.blocks or None
                ui_profile = built.profile or ui_profile
                attachments = (
                    built.attachments
                    if built.grouped
                    else (Attachment(actions=built.actions), *built.attachments)
                )
            else:
                actions = built
        if mode in (ShowMode.AUTO, ShowMode.EDIT) and stack.last_post_id is not None:
            props = compile_post_ui(
                actions=actions,
                blocks=blocks,
                attachments=attachments,
                profile=ui_profile,
                context=action_context,
                message=plan.text,
            )
            if self.update_post is not None:
                await self.update_post(plan.text, props)
            else:
                await self.bot.edit_message_text(
                    self.channel_id,
                    stack.last_post_id,
                    plan.text,
                    props=props,
                )
            return
        message = await self.bot.send_message(
            self.channel_id,
            plan.text,
            root_id=self.root_id,
            actions=actions,
            blocks=blocks,
            attachments=attachments,
            ui_profile=ui_profile,
            action_context=action_context,
        )
        stack.last_post_id = message.message_id


__all__ = [
    "BoundActions",
    "FormRender",
    "MattermostRenderer",
    "PostControl",
    "PostControlGroup",
    "PostTableRow",
    "PostRender",
    "RenderPlan",
    "Renderer",
]
