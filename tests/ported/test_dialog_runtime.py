from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import pytest
from magic_filter import F

from mattergram.fsm.state import State, StatesGroup
from mattergram.http.auth import CallbackSigner
from mattergram.ui.actions import ActionContext
from mattergram.ui.blocks import compile_post_ui
from mattergram.ui.capabilities import UIProfile
from mattergram.ui.forms import Option, TextInput
from mattergram_dialog import (
    ActionRow,
    ActionTable,
    Back,
    Button,
    ButtonGrid,
    ButtonRow,
    Checkbox,
    CheckboxGroup,
    CompactPager,
    Const,
    CursorList,
    CursorPage,
    CursorPager,
    DataBlock,
    DatePicker,
    Dialog,
    DialogManager,
    DialogRegistry,
    DialogStorage,
    FileInput,
    Filter,
    Format,
    FormWindow,
    MarkdownColumn,
    MultiSelect,
    OpenForm,
    OpenFormActions,
    PagedList,
    PagedTable,
    PageItemSelect,
    PagePager,
    Pager,
    Progress,
    Radio,
    Section,
    Select,
    TextArea,
    Window,
)
from mattergram_dialog.api.entities import ShowMode
from mattergram_dialog.rendering import (
    FormRender,
    PostControl,
    PostControlGroup,
    PostRender,
    PostTableRow,
    RenderPlan,
)
from mattergram_dialog.widgets import Case, Multi, NativeField, Or
from mattergram_dialog.widgets.form import DateTimePicker
from mattergram_dialog.widgets.form import TextInput as DialogTextInput
from tests.fixtures.dialog_navigation_app import Demo as DialogDemo
from tests.fixtures.dialog_navigation_app import DemoData, build_dialog


class Main(StatesGroup):
    first = State()
    second = State()


class Child(StatesGroup):
    only = State()


class Branch(StatesGroup):
    home = State()
    orders = State()
    incidents = State()
    shared = State()


@dataclass
class SpyRenderer:
    calls: list[tuple[RenderPlan, ShowMode]]

    async def render(self, plan: RenderPlan, stack: Any, mode: ShowMode) -> None:
        self.calls.append((plan, mode))
        if isinstance(plan, PostRender):
            stack.last_post_id = stack.last_post_id or "post-1"


async def parent_result(result: Any, manager: DialogManager) -> None:
    manager.dialog_data["child"] = result


def runtime(storage: Any, isolation: Any, storage_key: Any) -> tuple[DialogStorage, DialogRegistry]:
    main = Dialog(
        Window(Or(Format("hello {name}", when="name"), Const("hello")), state=Main.first),
        Window(Or(Format("result {child}", when="child"), Const("waiting")), state=Main.second),
        on_process_result=parent_result,
    )
    child = Dialog(Window(Const("child"), state=Child.only))
    return DialogStorage(storage, isolation), DialogRegistry(main, child)


def manager(
    dialog_storage: DialogStorage,
    registry: DialogRegistry,
    storage_key: Any,
    renderer: SpyRenderer,
) -> DialogManager:
    return DialogManager(
        dialog_storage,
        storage_key,
        registry,
        renderer,
        actor_id="user",
    )


@pytest.mark.parametrize(
    ("storage", "isolation"),
    [("memory_storage", "lock_isolation"), ("redis_storage", "redis_isolation")],
    indirect=True,
)
async def test_nested_transitions_persist_on_memory_and_redis(
    storage: Any, isolation: Any, storage_key: Any
) -> None:
    dialog_storage, registry = runtime(storage, isolation, storage_key)
    renderer = SpyRenderer([])
    async with manager(dialog_storage, registry, storage_key, renderer) as active:
        await active.start(Main.first)
        active.dialog_data["name"] = "Ada"
        await active.show()
        await active.next()
        await active.start(Child.only)
        await active.done(42)
        assert active.current_context().state == Main.second
        assert active.dialog_data["child"] == 42

    async with manager(dialog_storage, registry, storage_key, renderer) as restored:
        assert restored.current_context().state == Main.second
        assert restored.dialog_data == {"name": "Ada", "child": 42}
    assert [call[0].text for call in renderer.calls if isinstance(call[0], PostRender)] == [
        "hello",
        "hello Ada",
        "waiting",
        "child",
        "result 42",
    ]


@pytest.mark.parametrize(
    ("storage", "isolation"),
    [("memory_storage", "lock_isolation"), ("redis_storage", "redis_isolation")],
    indirect=True,
)
async def test_failed_manager_transaction_does_not_commit(
    storage: Any, isolation: Any, storage_key: Any
) -> None:
    dialog_storage, registry = runtime(storage, isolation, storage_key)
    renderer = SpyRenderer([])
    with pytest.raises(RuntimeError, match="rollback"):
        async with manager(dialog_storage, registry, storage_key, renderer) as active:
            await active.start(Main.first)
            active.dialog_data["uncommitted"] = True
            raise RuntimeError("rollback")
    session = await dialog_storage.load(storage_key)
    assert session.stack.empty()
    assert session.contexts == {}


@pytest.mark.parametrize("storage", ["memory_storage"], indirect=True)
@pytest.mark.parametrize("isolation", ["lock_isolation"], indirect=True)
async def test_locked_managers_do_not_lose_updates(storage: Any, isolation: Any, storage_key: Any) -> None:
    dialog_storage, registry = runtime(storage, isolation, storage_key)
    renderer = SpyRenderer([])
    async with manager(dialog_storage, registry, storage_key, renderer) as active:
        await active.start(Main.first)
        active.dialog_data["count"] = 0

    async def increment() -> None:
        async with manager(dialog_storage, registry, storage_key, renderer) as active:
            value = active.dialog_data["count"]
            await asyncio.sleep(0)
            active.dialog_data["count"] = value + 1

    await asyncio.gather(*(increment() for _ in range(20)))
    async with manager(dialog_storage, registry, storage_key, renderer) as active:
        assert active.dialog_data["count"] == 20


@pytest.mark.parametrize("storage", ["memory_storage"], indirect=True)
@pytest.mark.parametrize("isolation", ["lock_isolation"], indirect=True)
async def test_text_widgets_and_native_form(storage: Any, isolation: Any, storage_key: Any) -> None:
    text = Multi(
        Const("visible", when="enabled"),
        Or(Const(""), Const("fallback")),
        Case({"admin": Const("root")}, F["role"], default=Const("user")),
    )
    form = FormWindow(
        Const("Profile"),
        NativeField(TextInput(name="email", label="Email", subtype="email")),
        state=Main.first,
        introduction=text,
    )
    registry = DialogRegistry(Dialog(form))
    renderer = SpyRenderer([])
    async with manager(DialogStorage(storage, isolation), registry, storage_key, renderer) as active:
        await active.start(Main.first)
        active.dialog_data.update(enabled=True, role="admin")
        await active.show(ShowMode.FORM)
    plans = [item[0] for item in renderer.calls]
    assert all(isinstance(item, FormRender) for item in plans)
    final = plans[-1]
    assert isinstance(final, FormRender)
    assert final.form.introduction_text == "visible\nfallback\nroot"
    assert final.form.elements[0].name == "email"
    assert final.form.notify_on_cancel is True
    assert final.form.state


@pytest.mark.parametrize("storage", ["memory_storage"], indirect=True)
@pytest.mark.parametrize("isolation", ["lock_isolation"], indirect=True)
async def test_progress_renders_determinate_and_indeterminate_values(
    storage: Any, isolation: Any, storage_key: Any
) -> None:
    dialog = Dialog(
        Window(
            Multi(
                Progress(current="done", total="total", label="Parsing", width=10),
                Progress(current="discovered", label="Discovering"),
            ),
            state=Branch.orders,
            getter=lambda: {"done": 6, "total": 10, "discovered": 42},
        )
    )
    renderer = SpyRenderer([])
    async with manager(
        DialogStorage(storage, isolation), DialogRegistry(dialog), storage_key, renderer
    ) as active:
        await active.start(Branch.orders)

    plan = renderer.calls[-1][0]
    assert isinstance(plan, PostRender)
    assert plan.text == "Parsing\n██████░░░░ 60% · 6/10\n⏳ Discovering 42"


@pytest.mark.parametrize("storage", ["memory_storage"], indirect=True)
@pytest.mark.parametrize("isolation", ["lock_isolation"], indirect=True)
async def test_form_defaults_resolve_from_render_data(storage: Any, isolation: Any, storage_key: Any) -> None:
    async def observed_on(data: dict[str, Any], manager: Any) -> str:
        del manager
        return data["observed_on"]

    dialog = Dialog(
        FormWindow(
            DialogTextInput(id="owner", label="Owner", default=Format("{owner}")),
            Checkbox(id="enabled", label="Enabled", default=F["enabled"]),
            MultiSelect(
                id="sources",
                label="Sources",
                options=(Option(text="API", value="api"), Option(text="CSV", value="csv")),
                default_values=lambda data, manager: data["sources"],
            ),
            DatePicker(id="observed", label="Observed", default=observed_on),
            title=Const("Edit import"),
            state=Branch.orders,
            getter=lambda: {
                "owner": "Ada",
                "enabled": True,
                "sources": ("api", "csv"),
                "observed_on": "2026-11-15",
            },
        )
    )
    renderer = SpyRenderer([])
    async with manager(
        DialogStorage(storage, isolation), DialogRegistry(dialog), storage_key, renderer
    ) as active:
        await active.start(Branch.orders)

    plan = renderer.calls[-1][0]
    assert isinstance(plan, FormRender)
    owner, enabled, sources, observed = plan.form.elements
    assert owner.default == "Ada"
    assert enabled.default is True
    assert sources.default_values == ("api", "csv")
    assert observed.default == "2026-11-15"


def test_action_table_is_a_public_blocks_only_widget() -> None:
    assert ActionTable.__module__ == "mattergram_dialog.widgets.table"


@pytest.mark.parametrize("storage", ["memory_storage"], indirect=True)
@pytest.mark.parametrize("isolation", ["lock_isolation"], indirect=True)
async def test_static_data_block_survives_without_visible_actions(
    storage: Any, isolation: Any, storage_key: Any
) -> None:
    dialog = Dialog(
        Window(
            Const("Job"),
            DataBlock(
                "Progress",
                Progress(current="done", total="total", width=8),
                Button(Const("Cancel"), id="cancelJob", when="can_cancel"),
                color="#3d85c6",
            ),
            state=Branch.orders,
            getter=lambda: {"done": 4, "total": 8, "can_cancel": False},
        )
    )
    renderer = SpyRenderer([])
    async with manager(
        DialogStorage(storage, isolation), DialogRegistry(dialog), storage_key, renderer
    ) as active:
        await active.start(Branch.orders)

    plan = renderer.calls[-1][0]
    assert isinstance(plan, PostRender)
    assert plan.controls == ()
    assert len(plan.control_groups) == 1
    group = plan.control_groups[0]
    assert group.title == "Progress"
    assert group.text == "████░░░░ 50% · 4/8"
    assert group.controls == ()
    assert group.control_rows == ()

    context = ActionContext(
        signer=CallbackSigner(
            instance_id="https://instance.test",
            bot_id="bot",
            keys={"key": b"x" * 32},
            active_key_id="key",
        ),
        channel_id="channel",
        user_id="user",
    )
    legacy = OpenFormActions("https://app.test/callback", context)(plan.control_groups)
    legacy_props = compile_post_ui(
        attachments=legacy.attachments,
        context=legacy.context,
        message=plan.text,
    )
    assert legacy_props["attachments"][0] == {
        "color": "#3d85c6",
        "title": "Progress",
        "text": "████░░░░ 50% · 4/8",
        "fields": [],
    }

    profile = UIProfile(
        "12.0.2",
        client="test",
        client_features=frozenset({"blocks"}),
        client_evidence="unit test",
        mm_blocks_enabled=True,
    )
    blocks = OpenFormActions(
        "https://app.test/callback",
        context,
        ui_profile=profile,
    )(plan.control_groups)
    block_props = compile_post_ui(
        blocks=blocks.blocks,
        profile=blocks.profile,
        context=blocks.context,
        message=plan.text,
    )
    assert block_props["mm_blocks_actions"] == {}
    assert [item["text"] for item in block_props["mm_blocks"][0]["content"]] == [
        "**Progress**",
        "████░░░░ 50% · 4/8",
    ]


@pytest.mark.parametrize("storage", ["memory_storage"], indirect=True)
@pytest.mark.parametrize("isolation", ["lock_isolation"], indirect=True)
async def test_documented_declaration_and_submit_flow(storage: Any, isolation: Any, storage_key: Any) -> None:
    submitted: list[dict[str, Any]] = []

    async def save(event: Any, active: DialogManager) -> None:
        submitted.append(event)
        await active.done()

    dialog = Dialog(
        Window(
            Const("Settings"),
            OpenForm(Const("Edit"), id="edit", state=Main.second),
            state=Main.first,
        ),
        FormWindow(
            DialogTextInput(id="title", label="Title"),
            Checkbox(id="enabled", label="Enabled"),
            DateTimePicker(id="start", label="Start"),
            title=Const("Settings"),
            submit_text=Const("Save"),
            on_submit=save,
            state=Main.second,
        ),
    )
    registry = DialogRegistry(dialog)
    renderer = SpyRenderer([])
    async with manager(DialogStorage(storage, isolation), registry, storage_key, renderer) as active:
        await active.start(Main.first)
        first = renderer.calls[-1][0]
        assert isinstance(first, PostRender)
        assert first.controls[0].target_state == Main.second.state
        await active.switch_to(Main.second, show_mode=ShowMode.FORM)
        form = renderer.calls[-1][0]
        assert isinstance(form, FormRender)
        assert [field.name for field in form.form.elements] == ["title", "enabled", "start"]
        assert form.form.submit_label == "Save"
        await active.submit({"title": "Mattergram"})
        assert active.stack.empty()
    assert submitted == [{"title": "Mattergram"}]


@pytest.mark.parametrize("storage", ["memory_storage"], indirect=True)
@pytest.mark.parametrize("isolation", ["lock_isolation"], indirect=True)
async def test_typed_form_presets_cover_common_input_shapes(
    storage: Any, isolation: Any, storage_key: Any
) -> None:
    options = (Option(text="One", value="one"), Option(text="Two", value="two"))
    form = FormWindow(
        DialogTextInput(id="title", label="Title", max_length=80),
        TextArea(id="description", label="Description", max_length=500),
        Select(id="choice", label="Choice", options=options),
        MultiSelect(id="tags", label="Tags", options=options),
        Radio(id="radio", label="Radio", options=options),
        CheckboxGroup(id="checks", label="Checks", options=options),
        DatePicker(id="due", label="Due"),
        FileInput(id="files", label="Files", allow_multiple=True),
        title=Const("Typed fields"),
        state=Main.first,
    )
    renderer = SpyRenderer([])
    async with manager(
        DialogStorage(storage, isolation), DialogRegistry(Dialog(form)), storage_key, renderer
    ) as active:
        await active.start(Main.first)

    plan = renderer.calls[-1][0]
    assert isinstance(plan, FormRender)
    assert [field.name for field in plan.form.elements] == [
        "title",
        "description",
        "choice",
        "tags",
        "radio",
        "checks",
        "due",
        "files",
    ]


@pytest.mark.parametrize("storage", ["memory_storage"], indirect=True)
@pytest.mark.parametrize("isolation", ["lock_isolation"], indirect=True)
async def test_select_options_can_be_loaded_when_the_form_opens(
    storage: Any, isolation: Any, storage_key: Any
) -> None:
    calls = 0

    async def options(data: Any, active: DialogManager) -> tuple[Option, ...]:
        nonlocal calls
        assert data["manager"] is active
        calls += 1
        return (Option(text="Alice", value="alice-id"),)

    form = FormWindow(
        Select(id="user", label="User", options=options),
        title=Const("Assign role"),
        state=Main.first,
    )
    renderer = SpyRenderer([])
    async with manager(
        DialogStorage(storage, isolation), DialogRegistry(Dialog(form)), storage_key, renderer
    ) as active:
        await active.start(Main.first)

    plan = renderer.calls[-1][0]
    assert isinstance(plan, FormRender)
    assert [(option.text, option.value) for option in plan.form.elements[0].options] == [
        ("Alice", "alice-id")
    ]
    assert calls == 1


def test_open_form_actions_carry_the_signing_context() -> None:
    context = ActionContext(
        signer=CallbackSigner(
            instance_id="https://instance.test",
            bot_id="bot",
            keys={"key": b"x" * 32},
            active_key_id="key",
        ),
        channel_id="channel",
        user_id="user",
    )
    factory = OpenFormActions("https://app.test/callback", context)
    bound = factory((PostControl("edit", "Edit", Main.second.state or ""),))
    action = bound.actions.items[0]
    wire = action.to_wire(bound.context)
    assert wire["integration"]["context"]
    assert "data" not in wire["integration"]
    assert OpenFormActions.target_state(action.action.data) == Main.second.state


@pytest.mark.parametrize("storage", ["memory_storage", "redis_storage"], indirect=True)
@pytest.mark.parametrize("isolation", ["lock_isolation"], indirect=True)
async def test_back_uses_actual_branch_history_and_persists(
    storage: Any, isolation: Any, storage_key: Any
) -> None:
    dialog = Dialog(
        Window(Const("Home"), state=Branch.home),
        Window(Const("Orders"), state=Branch.orders),
        Window(Const("Incidents"), state=Branch.incidents),
        Window(Const("Shared"), Back(Const("Back")), state=Branch.shared),
    )
    registry = DialogRegistry(dialog)
    dialog_storage = DialogStorage(storage, isolation)
    renderer = SpyRenderer([])
    async with manager(dialog_storage, registry, storage_key, renderer) as active:
        await active.start(Branch.home)
        await active.switch_to(Branch.orders)
        await active.switch_to(Branch.shared)
        await active.back()
        assert active.current_context().state == Branch.orders
        await active.back()
        assert active.current_context().state == Branch.home
        await active.switch_to(Branch.incidents)
        await active.switch_to(Branch.shared)

    async with manager(dialog_storage, registry, storage_key, renderer) as restored:
        assert restored.current_context().state == Branch.shared
        await restored.back()
        assert restored.current_context().state == Branch.incidents
        await restored.back()
        assert restored.current_context().state == Branch.home


@pytest.mark.parametrize("storage", ["memory_storage"], indirect=True)
@pytest.mark.parametrize("isolation", ["lock_isolation"], indirect=True)
async def test_paged_list_filter_and_pager_keep_independent_widget_state(
    storage: Any, isolation: Any, storage_key: Any
) -> None:
    orders = [
        {"name": "A", "status": "open"},
        {"name": "B", "status": "closed"},
        {"name": "C", "status": "open"},
        {"name": "D", "status": "open"},
    ]
    listing = PagedList(
        Format("- {item[name]}"),
        id="orders",
        items="orders",
        page_size=2,
        filter_by="status",
    )
    dialog = Dialog(
        Window(
            Multi(Const("Orders"), listing),
            Filter(id="status", target="orders", options={"All": None, "Open": "open"}),
            Pager(id="ordersPager", target="orders"),
            state=Branch.orders,
            getter=lambda: {"orders": orders},
        )
    )
    registry = DialogRegistry(dialog)
    renderer = SpyRenderer([])
    async with manager(DialogStorage(storage, isolation), registry, storage_key, renderer) as active:
        await active.start(Branch.orders)
        first = renderer.calls[-1][0]
        assert isinstance(first, PostRender)
        assert first.text == "Orders\n- A\n- B\nPage 1/2"
        assert [control.action for control in first.controls] == ["filter", "filter", "page"]

        filter_widget = active.find("status")
        assert isinstance(filter_widget, Filter)
        filter_widget.apply("open", active)
        await active.show()
        filtered = renderer.calls[-1][0]
        assert isinstance(filtered, PostRender)
        assert filtered.text == "Orders\n- A\n- C\nPage 1/2"

        pager = active.find("ordersPager")
        assert isinstance(pager, Pager)
        pager.apply("next", active)
        await active.show()
        second = renderer.calls[-1][0]
        assert isinstance(second, PostRender)
        assert second.text == "Orders\n- D\nPage 2/2"
        assert active.current_context().widget_data["orders"] == {
            "page": 1,
            "filter": "open",
            "pages": 2,
            "total": 3,
        }


@pytest.mark.parametrize("storage", ["memory_storage"], indirect=True)
@pytest.mark.parametrize("isolation", ["lock_isolation"], indirect=True)
async def test_one_window_can_render_and_control_multiple_independent_tables(
    storage: Any, isolation: Any, storage_key: Any
) -> None:
    first = PagedList(Format("A:{item}"), id="firstRows", items="first", page_size=1)
    second = PagedList(
        Format("B:{item[name]}"),
        id="secondRows",
        items="second",
        page_size=1,
        filter_by="kind",
    )
    dialog = Dialog(
        Window(
            Multi(Const("Report"), first, second),
            Pager(id="firstPager", target="firstRows"),
            Filter(id="secondFilter", target="secondRows", options={"All": None, "X": "x"}),
            Pager(id="secondPager", target="secondRows"),
            state=Branch.orders,
            getter=lambda: {
                "first": ("one", "two"),
                "second": ({"name": "red", "kind": "x"}, {"name": "blue", "kind": "y"}),
            },
        )
    )
    renderer = SpyRenderer([])
    async with manager(
        DialogStorage(storage, isolation), DialogRegistry(dialog), storage_key, renderer
    ) as active:
        await active.start(Branch.orders)
        assert renderer.calls[-1][0].text == "Report\nA:one\nPage 1/2\nB:red\nPage 1/2"

        first_pager = active.find("firstPager")
        second_filter = active.find("secondFilter")
        assert isinstance(first_pager, Pager)
        assert isinstance(second_filter, Filter)
        first_pager.apply("next", active)
        second_filter.apply("x", active)
        await active.show()

        assert renderer.calls[-1][0].text == "Report\nA:two\nPage 2/2\nB:red\nPage 1/1"
        assert active.current_context().widget_data["firstRows"]["page"] == 1
        assert active.current_context().widget_data["secondRows"] == {
            "page": 0,
            "filter": "x",
            "pages": 1,
            "total": 1,
        }


@pytest.mark.parametrize("storage", ["memory_storage"], indirect=True)
@pytest.mark.parametrize("isolation", ["lock_isolation"], indirect=True)
async def test_button_grid_preserves_declared_two_four_two_layout(
    storage: Any, isolation: Any, storage_key: Any
) -> None:
    def link(index: int) -> OpenForm:
        return OpenForm(Const(f"Action {index}"), id=f"action{index}", state=Branch.orders)

    dialog = Dialog(
        Window(
            Const("Structured card"),
            ButtonGrid(
                ActionRow(link(1), link(2)),
                ActionRow(link(3), link(4), link(5), link(6)),
                ActionRow(link(7), link(8)),
                title="Actions",
                color="#3d85c6",
            ),
            state=Branch.orders,
        )
    )
    renderer = SpyRenderer([])
    async with manager(
        DialogStorage(storage, isolation), DialogRegistry(dialog), storage_key, renderer
    ) as active:
        await active.start(Branch.orders)

    plan = renderer.calls[-1][0]
    assert isinstance(plan, PostRender)
    assert len(plan.control_groups) == 1
    group = plan.control_groups[0]
    assert [len(row) for row in group.control_rows] == [2, 4, 2]
    assert group.row_width == "stretch"
    assert group.title == "Actions"
    assert [control.id for control in plan.controls] == [f"action{index}" for index in range(1, 9)]

    context = ActionContext(
        signer=CallbackSigner(
            instance_id="https://instance.test",
            bot_id="bot",
            keys={"key": b"x" * 32},
            active_key_id="key",
        ),
        channel_id="channel",
        user_id="user",
    )
    profile = UIProfile(
        "12.0.2",
        client="test",
        client_features=frozenset({"blocks"}),
        client_evidence="unit test",
        mm_blocks_enabled=True,
    )
    bound = OpenFormActions(
        "https://app.test/callback",
        context,
        ui_profile=profile,
    )(plan.control_groups)
    props = compile_post_ui(
        blocks=bound.blocks,
        profile=bound.profile,
        context=bound.context,
        message=plan.text,
    )
    outer = props["mm_blocks"][0]
    assert [len(row["columns"]) for row in outer["content"][1:]] == [2, 4, 2]
    assert all(column["width"] == "stretch" for row in outer["content"][1:] for column in row["columns"])
    assert len(props["mm_blocks_actions"]) == 8


def test_native_action_table_renders_text_cells_and_row_buttons() -> None:
    edit = PostControl(
        "ordersEdit0",
        "Изменить",
        target_state=Branch.shared.state,
        intent_id="intent",
        source_state=Branch.orders.state,
        action="item",
        widget_id="orders",
        value="ORD-1042",
        style="primary",
    )
    delete = PostControl(
        "ordersDelete0",
        "Удалить",
        target_state=Branch.incidents.state,
        intent_id="intent",
        source_state=Branch.orders.state,
        action="item",
        widget_id="orders",
        value="ORD-1042",
        style="danger",
    )
    group = PostControlGroup(
        (edit, delete),
        title="Заказы",
        table_headers=("Заказ", "Статус", "Владелец", "Изменить", "Удалить"),
        table_data_columns=3,
        table_rows=(PostTableRow(("ORD-1042", "open", "Ada"), (edit, delete)),),
    )
    context = ActionContext(
        signer=CallbackSigner(
            instance_id="https://instance.test",
            bot_id="bot",
            keys={"key": b"x" * 32},
            active_key_id="key",
        ),
        channel_id="channel",
        user_id="user",
    )
    profile = UIProfile(
        "12.0.2",
        client="test",
        client_features=frozenset({"blocks"}),
        client_evidence="unit test",
        mm_blocks_enabled=True,
    )
    bound = OpenFormActions(
        "https://app.test/callback",
        context,
        ui_profile=profile,
    )((group,))
    props = compile_post_ui(
        blocks=bound.blocks,
        profile=bound.profile,
        context=bound.context,
    )

    container = props["mm_blocks"][0]
    header, row = container["content"][1:]
    assert [column["items"][0]["text"] for column in header["columns"]] == [
        "**Заказ**",
        "**Статус**",
        "**Владелец**",
        "**Изменить**",
        "**Удалить**",
    ]
    assert [column["items"][0]["type"] for column in row["columns"]] == [
        "text",
        "text",
        "text",
        "button",
        "button",
    ]
    assert set(props["mm_blocks_actions"]) == {"ordersEdit0", "ordersDelete0"}
    verified = context.signer.verify(
        {
            "channel_id": "channel",
            "user_id": "user",
            "context": props["mm_blocks_actions"]["ordersEdit0"]["context"],
        }
    )
    assert OpenFormActions.decode(verified["data"]).value == "ORD-1042"

    with pytest.raises(ValueError, match="require a supported Mattermost Blocks profile"):
        OpenFormActions("https://app.test/callback", context)((group,))


def test_borderless_control_group_renders_without_accent() -> None:
    control = PostControl(
        "addUser",
        "Добавить пользователя",
        intent_id="intent",
        source_state=Branch.orders.state,
    )
    context = ActionContext(
        signer=CallbackSigner(
            instance_id="https://instance.test",
            bot_id="bot",
            keys={"key": b"x" * 32},
            active_key_id="key",
        ),
        channel_id="channel",
        user_id="user",
    )
    profile = UIProfile(
        "12.0.2",
        client="test",
        client_features=frozenset({"blocks"}),
        client_evidence="unit test",
        mm_blocks_enabled=True,
    )
    bound = OpenFormActions(
        "https://app.test/callback",
        context,
        ui_profile=profile,
    )((PostControlGroup((control,), border=False),))
    props = compile_post_ui(
        blocks=bound.blocks,
        profile=bound.profile,
        context=bound.context,
    )

    container = props["mm_blocks"][0]
    assert container["border"] is False
    assert "accent_color" not in container


@pytest.mark.parametrize("storage", ["memory_storage"], indirect=True)
@pytest.mark.parametrize("isolation", ["lock_isolation"], indirect=True)
async def test_dialog_demo_orders_compile_six_native_action_rows(
    storage: Any, isolation: Any, storage_key: Any
) -> None:
    dialog = build_dialog(DemoData())
    renderer = SpyRenderer([])
    async with manager(
        DialogStorage(storage, isolation), DialogRegistry(dialog), storage_key, renderer
    ) as active:
        await active.start(DialogDemo.orders)
        table = active.find("orderActions")
        table.apply_item("ORD-1042", active)
        assert active.dialog_data["selected_order_id"] == "ORD-1042"

    plan = renderer.calls[-1][0]
    assert isinstance(plan, PostRender)
    table_group = plan.control_groups[0]
    assert len(table_group.table_rows) == 6
    assert [len(row.controls) for row in table_group.table_rows] == [2] * 6
    assert len(plan.control_groups[1].controls) == 2

    context = ActionContext(
        signer=CallbackSigner(
            instance_id="https://instance.test",
            bot_id="bot",
            keys={"key": b"x" * 32},
            active_key_id="key",
        ),
        channel_id="channel",
        user_id="user",
    )
    profile = UIProfile(
        "12.0.2",
        client="test",
        client_features=frozenset({"blocks"}),
        client_evidence="unit test",
        mm_blocks_enabled=True,
    )
    bound = OpenFormActions(
        "https://app.test/callback",
        context,
        ui_profile=profile,
    )(plan.control_groups)
    props = compile_post_ui(
        blocks=bound.blocks,
        profile=bound.profile,
        context=bound.context,
        message=plan.text,
    )
    assert len(props["mm_blocks"]) == 2
    assert len(props["mm_blocks_actions"]) == 14


@pytest.mark.parametrize("storage", ["memory_storage"], indirect=True)
@pytest.mark.parametrize("isolation", ["lock_isolation"], indirect=True)
async def test_dialog_demo_report_places_filters_above_tables_and_selects_beside_pagers(
    storage: Any, isolation: Any, storage_key: Any
) -> None:
    dialog = build_dialog(DemoData())
    renderer = SpyRenderer([])
    async with manager(
        DialogStorage(storage, isolation), DialogRegistry(dialog), storage_key, renderer
    ) as active:
        await active.start(DialogDemo.reports)

    plan = renderer.calls[-1][0]
    assert isinstance(plan, PostRender)
    assert len(plan.control_groups) == 3
    for group, select_id in zip(
        plan.control_groups[:2],
        ("reportOrderSelect", "reportIncidentSelect"),
        strict=True,
    ):
        assert group.content_row_index == 1
        assert [len(row) for row in group.control_rows] == [3, 6]
        assert group.control_rows[0][0].style == "primary"
        assert not group.control_rows[0][0].label.startswith("✓")
        selector = group.control_rows[1][-1]
        assert selector.id == select_id
        assert len(selector.options) == 2

    context = ActionContext(
        signer=CallbackSigner(
            instance_id="https://instance.test",
            bot_id="bot",
            keys={"key": b"x" * 32},
            active_key_id="key",
        ),
        channel_id="channel",
        user_id="user",
    )
    profile = UIProfile(
        "12.0.2",
        client="test",
        client_features=frozenset({"blocks"}),
        client_evidence="unit test",
        mm_blocks_enabled=True,
    )
    bound = OpenFormActions(
        "https://app.test/callback",
        context,
        ui_profile=profile,
    )(plan.control_groups)
    props = compile_post_ui(
        blocks=bound.blocks,
        profile=bound.profile,
        context=bound.context,
        message=plan.text,
    )
    assert len(props["mm_blocks"]) == 3
    assert len(props["mm_blocks_actions"]) == 21


@pytest.mark.parametrize("storage", ["memory_storage"], indirect=True)
@pytest.mark.parametrize("isolation", ["lock_isolation"], indirect=True)
async def test_page_pager_has_stable_five_button_layout(
    storage: Any, isolation: Any, storage_key: Any
) -> None:
    listing = PagedList(Format("{item}"), id="rows", items="rows", page_size=2)
    dialog = Dialog(
        Window(
            Multi(Const("Rows"), listing),
            ButtonRow(PagePager(id="pages", target="rows"), title="Page"),
            state=Branch.orders,
            getter=lambda: {"rows": ("a", "b", "c", "d", "e")},
        )
    )
    renderer = SpyRenderer([])
    async with manager(
        DialogStorage(storage, isolation), DialogRegistry(dialog), storage_key, renderer
    ) as active:
        await active.start(Branch.orders)
        first = renderer.calls[-1][0]
        assert isinstance(first, PostRender)
        assert [control.label for control in first.control_groups[0].controls] == [
            "<<",
            "<",
            "1/3",
            ">",
            ">>",
        ]
        assert [control.disabled for control in first.control_groups[0].controls] == [
            True,
            True,
            True,
            False,
            False,
        ]

        pager = active.find("pages")
        assert isinstance(pager, PagePager)
        pager.apply("last", active)
        await active.show()
        last = renderer.calls[-1][0]
        assert isinstance(last, PostRender)
        assert "e\nPage 3/3" in last.text
        assert [control.disabled for control in last.control_groups[0].controls] == [
            False,
            False,
            True,
            True,
            True,
        ]


@pytest.mark.parametrize("storage", ["memory_storage"], indirect=True)
@pytest.mark.parametrize("isolation", ["lock_isolation"], indirect=True)
async def test_markdown_table_is_bounded_escaped_and_addressable_inside_section(
    storage: Any, isolation: Any, storage_key: Any
) -> None:
    table = PagedTable(
        MarkdownColumn("ID", "id", max_chars=8),
        MarkdownColumn("Value", "value", max_chars=8),
        id="tableRows",
        items="rows",
        page_size=1,
    )
    dialog = Dialog(
        Window(
            Section("Data", table),
            ButtonRow(CompactPager(id="tablePager", target="tableRows")),
            state=Branch.orders,
            getter=lambda: {
                "rows": (
                    {"id": "A|1", "value": "123456789"},
                    {"id": "B", "value": "ok"},
                )
            },
        )
    )
    renderer = SpyRenderer([])
    async with manager(
        DialogStorage(storage, isolation), DialogRegistry(dialog), storage_key, renderer
    ) as active:
        await active.start(Branch.orders)
        plan = renderer.calls[-1][0]
        assert isinstance(plan, PostRender)
        assert "| A\\|1 | 1234567… |" in plan.text
        assert [item.label for item in plan.control_groups[0].controls] == ["<", "1/2", ">"]
        assert active.find("tableRows") is table


@pytest.mark.parametrize("storage", ["memory_storage"], indirect=True)
@pytest.mark.parametrize("isolation", ["lock_isolation"], indirect=True)
async def test_data_block_keeps_table_filter_and_pager_in_one_attachment(
    storage: Any, isolation: Any, storage_key: Any
) -> None:
    table = PagedTable(
        MarkdownColumn("Order", "id", max_chars=12),
        MarkdownColumn("Status", "status", max_chars=12),
        id="localRows",
        items="rows",
        page_size=1,
        filter_by="status",
        page_label="Page {page}/{pages}",
    )
    dialog = Dialog(
        Window(
            Const("Summary"),
            DataBlock(
                "Orders",
                table,
                Filter(
                    id="localFilter",
                    target="localRows",
                    options={"All": None, "Open": "open", "Paid": "paid"},
                ),
                ActionRow(
                    PagePager(id="localPager", target="localRows"),
                    PageItemSelect(
                        id="localSelect",
                        target="localRows",
                        item_id="id",
                        item_label="id",
                        selection_key="selected_order",
                        placeholder="Choose order",
                        detail_title=Format("Order {item[id]}"),
                        detail=Format("**Status:** {item[status]}"),
                        detail_color="#3d85c6",
                    ),
                ),
            ),
            state=Branch.orders,
            getter=lambda: {
                "rows": (
                    {"id": "A", "status": "open"},
                    {"id": "B", "status": "paid"},
                )
            },
        )
    )
    renderer = SpyRenderer([])
    async with manager(
        DialogStorage(storage, isolation), DialogRegistry(dialog), storage_key, renderer
    ) as active:
        await active.start(Branch.orders)
        assert active.find("localRows") is table
        initial = renderer.calls[-1][0]
        assert isinstance(initial, PostRender)
        assert initial.control_groups[0].detail_text is None
        selector = active.find("localSelect")
        selector.apply("A", active)
        assert active.dialog_data["selected_order"] == "A"
        await active.show()

    plan = renderer.calls[-1][0]
    assert isinstance(plan, PostRender)
    assert len(plan.control_groups) == 1
    group = plan.control_groups[0]
    assert group.title == "Orders"
    assert group.text is not None and "| A | open |" in group.text
    assert group.text.endswith("Page 1/2")
    assert [len(row) for row in group.control_rows] == [3, 6]
    assert group.content_row_index == 1
    assert group.row_width == "auto"
    assert group.detail_title == "Order A"
    assert group.detail_text == "**Status:** open"
    assert group.detail_color == "#3d85c6"
    assert [control.label for control in group.controls] == [
        "All",
        "Open",
        "Paid",
        "<<",
        "<",
        "1/2",
        ">",
        ">>",
        "Choose order",
    ]
    assert group.controls[0].style == "primary"
    assert all(control.style is None for control in group.controls[1:])

    context = ActionContext(
        signer=CallbackSigner(
            instance_id="https://instance.test",
            bot_id="bot",
            keys={"key": b"x" * 32},
            active_key_id="key",
        ),
        channel_id="channel",
        user_id="user",
    )
    bound = OpenFormActions("https://app.test/callback", context)(plan.control_groups)
    assert bound.grouped is True
    assert len(bound.attachments) == 1
    assert bound.attachments[0].title == "Orders"
    assert bound.attachments[0].text is not None
    assert bound.attachments[0].text.startswith(group.text)
    assert bound.attachments[0].text.endswith("**Order A**\n\n**Status:** open")
    assert len(bound.attachments[0].actions.items) == 9

    profile = UIProfile(
        "12.0.2",
        client="test",
        client_features=frozenset({"blocks"}),
        client_evidence="unit test",
        mm_blocks_enabled=True,
    )
    block_bound = OpenFormActions(
        "https://app.test/callback",
        context,
        ui_profile=profile,
    )(plan.control_groups)
    props = compile_post_ui(
        blocks=block_bound.blocks,
        profile=block_bound.profile,
        context=block_bound.context,
        message=plan.text,
    )
    content = props["mm_blocks"][0]["content"]
    assert [item["type"] for item in content] == [
        "text",
        "column_set",
        "text",
        "column_set",
        "container",
    ]
    rows = (content[1], content[3])
    assert [len(row["columns"]) for row in rows] == [3, 6]
    assert all(column["width"] == "auto" for row in rows for column in row["columns"])
    assert content[1]["columns"][0]["items"][0]["style"] == "primary"
    assert len(props["mm_blocks_actions"]) == 9
    assert [column["items"][0]["action_id"] for column in rows[1]["columns"]] == [
        "localPagerFirst",
        "localPagerPrev",
        "localPagerPosition",
        "localPagerNext",
        "localPagerLast",
        "localSelect",
    ]
    select = rows[1]["columns"][-1]["items"][0]
    assert select["type"] == "static_select"
    assert select["options"] == [{"text": "A", "value": "A"}]
    select_context = props["mm_blocks_actions"]["localSelect"]["context"]
    select_context["selected_option"] = "A"
    verified = context.signer.verify(
        {
            "channel_id": "channel",
            "user_id": "user",
            "context": select_context,
        }
    )
    select_action = OpenFormActions.decode(verified["data"])
    assert select_action.action == "select"
    assert select_action.widget_id == "localSelect"
    assert verified["selected_option"] == "A"
    detail = content[-1]
    assert detail["accent_color"] == "#3d85c6"
    assert detail["background"] == "gray"
    assert [item["text"] for item in detail["content"]] == [
        "**Order A**",
        "**Status:** open",
    ]


@pytest.mark.parametrize("storage", ["memory_storage"], indirect=True)
@pytest.mark.parametrize("isolation", ["lock_isolation"], indirect=True)
async def test_cursor_list_moves_forward_and_back_with_opaque_cursors(
    storage: Any, isolation: Any, storage_key: Any
) -> None:
    rows = ("a", "b", "c", "d", "e")

    async def load(cursor: str | None, limit: int, manager: Any) -> CursorPage:
        del manager
        offset = int(cursor or "0")
        page = rows[offset : offset + limit]
        next_offset = offset + len(page)
        return CursorPage(page, str(next_offset) if next_offset < len(rows) else None)

    listing = CursorList(Format("{item}"), id="feed", loader=load, page_size=2)
    dialog = Dialog(
        Window(
            Multi(Const("Feed"), listing),
            ButtonRow(CursorPager(id="feedPager", target="feed")),
            state=Branch.orders,
        )
    )
    renderer = SpyRenderer([])
    async with manager(
        DialogStorage(storage, isolation), DialogRegistry(dialog), storage_key, renderer
    ) as active:
        await active.start(Branch.orders)
        first = renderer.calls[-1][0]
        assert isinstance(first, PostRender)
        assert first.text == "Feed\na\nb\nPage 1"
        assert [control.disabled for control in first.control_groups[0].controls] == [True, False]

        pager = active.find("feedPager")
        assert isinstance(pager, CursorPager)
        pager.apply("next", active)
        await active.show()
        second = renderer.calls[-1][0]
        assert isinstance(second, PostRender)
        assert second.text == "Feed\nc\nd\nPage 2"
        assert [control.disabled for control in second.control_groups[0].controls] == [False, False]

        pager.apply("prev", active)
        await active.show()
        assert renderer.calls[-1][0].text == "Feed\na\nb\nPage 1"


def test_dialog_actions_round_trip_page_and_all_filter_values() -> None:
    context = ActionContext(
        signer=CallbackSigner(
            instance_id="https://instance.test",
            bot_id="bot",
            keys={"key": b"x" * 32},
            active_key_id="key",
        ),
        channel_id="channel",
        user_id="user",
    )
    factory = OpenFormActions("https://app.test/callback", context)
    bound = factory(
        (
            PostControl(
                "pagerNext",
                "Next",
                intent_id="intent",
                source_state=Branch.orders.state,
                action="page",
                widget_id="pager",
                value="next",
            ),
            PostControl(
                "filterAll",
                "All",
                intent_id="intent",
                source_state=Branch.orders.state,
                action="filter",
                widget_id="filter",
                value=None,
            ),
        )
    )
    page = OpenFormActions.decode(bound.actions.items[0].action.data)
    all_items = OpenFormActions.decode(bound.actions.items[1].action.data)
    assert (page.action, page.widget_id, page.value) == ("page", "pager", "next")
    assert (all_items.action, all_items.widget_id, all_items.value) == ("filter", "filter", None)


def test_dialog_actions_keep_large_cards_in_one_native_attachment_group() -> None:
    context = ActionContext(
        signer=CallbackSigner(
            instance_id="https://instance.test",
            bot_id="bot",
            keys={"key": b"x" * 32},
            active_key_id="key",
        ),
        channel_id="channel",
        user_id="user",
    )
    bound = OpenFormActions("https://app.test/callback", context)(
        tuple(
            PostControl(
                f"action{index}",
                f"Action {index}",
                intent_id="intent",
                source_state=Branch.orders.state,
                action="back",
            )
            for index in range(12)
        )
    )

    assert len(bound.actions.items) == 12
    assert bound.attachments == ()
    assert bound.actions.items[0].id == "action0"
    assert bound.actions.items[-1].id == "action11"
