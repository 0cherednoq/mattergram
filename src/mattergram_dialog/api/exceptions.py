class DialogsError(RuntimeError):
    pass


class InvalidStackIdError(DialogsError):
    pass


class UnknownIntent(DialogsError):
    pass


class OutdatedIntent(DialogsError):
    pass


class UnknownState(DialogsError):
    pass


class DialogStackOverflow(DialogsError):
    pass


class NoContextError(DialogsError):
    pass


class UnregisteredDialogError(DialogsError):
    pass


class UnregisteredWindowError(DialogsError):
    pass


class InvalidWidgetIdError(DialogsError):
    pass


class InvalidWidget(DialogsError):
    pass


class InvalidWidgetType(InvalidWidget):
    pass


class RendererUnavailable(DialogsError):
    pass
