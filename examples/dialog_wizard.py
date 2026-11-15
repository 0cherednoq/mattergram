"""Native mattergram_dialog declaration; wire it to slash/callback handlers as documented."""

from __future__ import annotations

from typing import Any

from mattergram.fsm.state import State, StatesGroup
from mattergram_dialog import Dialog, FormWindow, Window
from mattergram_dialog.api.protocols.manager import DialogManager
from mattergram_dialog.widgets.form import Checkbox, DateTimePicker, TextInput
from mattergram_dialog.widgets.message import OpenForm
from mattergram_dialog.widgets.text import Const, Format


class Settings(StatesGroup):
    menu = State()
    form = State()
    saved = State()


async def save(event: Any, manager: DialogManager) -> None:
    values = event.values
    manager.dialog_data["title"] = values["title"]
    await manager.switch_to(Settings.saved)


settings = Dialog(
    Window(
        Const("Settings"),
        OpenForm(Const("Edit"), id="edit", state=Settings.form),
        state=Settings.menu,
    ),
    FormWindow(
        TextInput(id="title", label="Title"),
        Checkbox(id="enabled", label="Enabled"),
        DateTimePicker(id="start_at", label="Start"),
        title=Const("Settings"),
        submit_text=Const("Save"),
        on_submit=save,
        state=Settings.form,
    ),
    Window(Format("Saved: {title}"), state=Settings.saved),
)
