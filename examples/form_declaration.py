"""Typed reusable declaration; open it through the managed native form runtime."""

from mattergram.ui.forms import Checkbox, DateTimePicker, Form, MultiSelect, Option, TextInput


def request_form() -> Form:
    return Form(
        callback_id="request",
        title="Request",
        elements=(
            TextInput(name="title", label="Title", min_length=1),
            Checkbox(name="urgent", label="Urgent", default=False),
            MultiSelect(
                name="departments",
                label="Departments",
                options=(
                    Option(text="Engineering", value="engineering"),
                    Option(text="Support", value="support"),
                ),
            ),
            DateTimePicker(name="due", label="Due", min_date="today", time_interval=30),
        ),
        notify_on_cancel=True,
    )
