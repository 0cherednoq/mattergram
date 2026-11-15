from __future__ import annotations

import re
import unittest

from mattergram import Dispatcher, F, Router
from mattergram.dispatcher.event.slash import REGISTRATION_FLAG
from mattergram.dispatcher.flags import Flag, FlagDecorator
from mattergram.exceptions import UnsupportedKeywordArgument
from mattergram.filters import Command
from mattergram.filters.base import Filter
from mattergram.provisioning import CommandManifest, CommandSpec, collect_commands
from mattergram.types.command_metadata import CommandRegistration


async def callback(event):
    return None


class CommandManifestTests(unittest.TestCase):
    def test_attached_slash_literals_aliases_and_metadata_are_collected(self):
        parent, child, detached = Router(), Router(), Router()
        parent.include_router(child)
        child.slash_command.register(
            callback, Command("request", "ticket"), description="Create request", hint="[topic]"
        )
        parent.slash_command.register(callback, Command("help"), description="Help")
        parent.message.register(callback, Command("posted"))
        detached.slash_command.register(callback, Command("detached"))
        result = collect_commands(parent)
        self.assertEqual([c.trigger for c in result.commands], ["help", "request", "ticket"])
        self.assertEqual(result.commands[-1].description, "Create request")
        self.assertEqual(result.commands[-1].hint, "[topic]")
        self.assertEqual(result.skipped_handlers, 0)

    def test_ambiguous_filters_require_explicit_registration(self):
        for filters in ((), (Command(re.compile("help.*")),), (Command("help"), F.text), (F.text,)):
            with self.subTest(filters=filters):
                router = Router()
                router.slash_command.register(callback, *filters)
                with self.assertRaises(ValueError):
                    collect_commands(router)

    def test_explicit_triggers_do_not_execute_or_guess_arbitrary_filters(self):
        def forbidden(event):
            raise AssertionError("Collection must not call handlers or filters")

        router = Router()
        router.slash_command.register(forbidden, forbidden, triggers=["known"], description="Explicit")
        self.assertEqual(collect_commands(router).commands, (CommandSpec("known", description="Explicit"),))

    def test_parent_root_filters_are_considered_and_explicit_trigger_is_available(self):
        root, child = Router(), Router()
        root.include_router(child)
        root.slash_command.filter(F.user_id == "owned-fixture")
        child.slash_command.register(callback, Command("help"))
        with self.assertRaises(ValueError):
            collect_commands(child)
        child.slash_command.handlers.clear()
        child.slash_command.register(callback, Command("help"), triggers=("help",))
        self.assertEqual(collect_commands(root).commands[0].trigger, "help")

    def test_magic_and_non_slash_command_filters_are_not_inferred(self):
        for command in (Command("help", prefix="!"), Command("help", magic=F.args)):
            with self.subTest(command=command):
                router = Router()
                router.slash_command.register(callback, command)
                with self.assertRaises(ValueError):
                    collect_commands(router)

    def test_identical_registrations_merge_but_conflicting_metadata_does_not(self):
        router = Router()
        router.slash_command.register(callback, Command("help"), hint="[topic]")
        router.slash_command.register(callback, Command("help"), hint="[topic]")
        self.assertEqual(len(collect_commands(router).commands), 1)
        router.slash_command.register(callback, Command("help"), hint="[other]")
        with self.assertRaises(ValueError):
            collect_commands(router)

    def test_provision_false_is_an_explicit_skip_not_an_empty_trigger(self):
        router = Router()
        router.slash_command.register(callback, F.text, provision=False)
        manifest = collect_commands(router)
        self.assertEqual(manifest.commands, ())
        self.assertEqual(manifest.skipped_handlers, 1)
        with self.assertRaises(ValueError):
            router.slash_command.register(callback, triggers=("help",), provision=False)
        with self.assertRaises(ValueError):
            router.slash_command.register(callback, triggers=())

    def test_registration_is_detached_from_mutable_inputs_and_handler_flags(self):
        router = Router()
        triggers = ["help"]
        input_flags = {"commands": [], "custom": True}
        router.slash_command.register(callback, Command("help"), flags=input_flags, triggers=triggers)
        before = collect_commands(router)
        triggers.append("later")
        self.assertEqual(input_flags["commands"], [])
        self.assertNotIn(REGISTRATION_FLAG, input_flags)
        self.assertEqual(before.commands, collect_commands(router).commands)
        router.slash_command.register(callback, Command("later"))
        self.assertEqual([c.trigger for c in before.commands], ["help"])

    def test_reserved_metadata_cannot_be_replaced_by_flags_decorator_or_filter(self):
        @FlagDecorator(Flag(REGISTRATION_FLAG, CommandRegistration(provision=False)))
        async def decorated(event):
            pass

        class Replacement(Filter):
            async def __call__(self, event):
                return True

            def update_handler_flags(self, flags):
                flags[REGISTRATION_FLAG] = CommandRegistration(provision=False)

        for handler, options, filters in (
            (callback, {"flags": {REGISTRATION_FLAG: CommandRegistration()}}, ()),
            (decorated, {}, ()),
            (callback, {}, (Replacement(),)),
        ):
            with self.subTest(handler=handler, options=options):
                router = Router()
                with self.assertRaises(ValueError):
                    router.slash_command.register(handler, *filters, **options)
                self.assertEqual(router.slash_command.handlers, [])

    def test_unknown_keywords_and_non_slash_metadata_still_fail(self):
        router = Router()
        with self.assertRaises(UnsupportedKeywordArgument):
            router.slash_command.register(callback, Command("help"), descripton="Typo")
        with self.assertRaises(UnsupportedKeywordArgument):
            router.message.register(callback, description="Not a slash command")
        self.assertEqual(router.slash_command.handlers, [])

    def test_utf8_limits_opaque_trigger_and_payload_fields(self):
        spec = CommandSpec("я" * 64, description="я" * 64, hint="[тема]", method="GET")
        payload = spec.to_payload(team_id="issued-team-fixture", url="https://callback.test/prefix/slash")
        self.assertEqual(payload["trigger"], "я" * 64)
        self.assertEqual(payload["method"], "G")
        self.assertEqual(payload["auto_complete_hint"], "[тема]")
        self.assertEqual(payload["auto_complete_desc"], payload["description"])
        self.assertNotIn("id", payload)
        self.assertNotIn("token", payload)
        payload["trigger"] = "changed"
        self.assertEqual(spec.trigger, "я" * 64)
        for options in ({"trigger": "я" * 65}, {"trigger": "ok", "description": "я" * 65}):
            with self.subTest(options=options), self.assertRaises(ValueError):
                CommandSpec(**options)

    def test_invalid_registration_and_callback_urls_are_rejected_locally(self):
        for trigger in ("", "/help", "has space", "tab\t", "nul\x00", "x" * 129, 7):
            with self.subTest(trigger=trigger), self.assertRaises(ValueError):
                CommandSpec(trigger)
        for url in (
            "https://@callback.test",
            "https://user:secret@callback.test",
            "file:///tmp/x",
            "https://a/#x",
            "https://a:bad/x",
            "https://a/\x00invalid",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                CommandSpec("help").to_payload(team_id="issued-fixture", url=url)
        for triggers in ("help", {"help"}, [], ["same", "same"]):
            with self.subTest(triggers=triggers), self.assertRaises((ValueError, TypeError)):
                Router().slash_command.register(callback, triggers=triggers)

    def test_fingerprint_changes_only_with_material_command_declarations(self):
        a, b = CommandSpec("a"), CommandSpec("b", hint="[topic]")
        first = CommandManifest((a, b))
        self.assertEqual(first.fingerprint, CommandManifest((b, a), skipped_handlers=5).fingerprint)
        self.assertNotEqual(
            first.fingerprint, CommandManifest((a, CommandSpec("b", hint="[other]"))).fingerprint
        )
        with self.assertRaises(ValueError):
            CommandManifest((a, a))
        with self.assertRaises(ValueError):
            CommandManifest((), skipped_handlers=True)

    def test_dispatcher_decorator_collects_and_no_handler_means_empty_manifest(self):
        dispatcher = Dispatcher()

        @dispatcher.slash_command(Command("help"), description="Help", hint="[topic]")
        async def help_command(event):
            raise AssertionError("Manifest collection cannot call the handler")

        self.assertEqual(collect_commands(dispatcher).commands, (CommandSpec("help", "Help", "[topic]"),))
        self.assertEqual(collect_commands(Router()).commands, ())
