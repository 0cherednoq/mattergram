from __future__ import annotations

import asyncio
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from cryptography.fernet import Fernet
from pydantic import SecretBytes, SecretStr

from mattergram.provisioning import FernetCodec, ProvisioningScope, ResourceKey, SQLiteProvisioningStore
from mattergram.provisioning.store import (
    PersistenceError,
    ProvisioningStoreError,
    StoreIntegrityError,
    UnresolvedIntentError,
)

WORKER = r"""
import asyncio,json,sys
from pydantic import SecretBytes
from mattergram.provisioning import FernetCodec,ProvisioningScope,ResourceKey,SQLiteProvisioningStore
data=json.loads(sys.stdin.readline())
async def run():
    store=SQLiteProvisioningStore(data['directory'],codec=FernetCodec(SecretBytes(data['key'].encode())))
    scope=ProvisioningScope(server='https://server.test/prefix',application='app')
    async with store.session(scope):
        pass
    async with store.session(scope) as session:
        session.begin(ResourceKey(kind='command',name='crash',team_id='native-team'),'create',require_secret=True)
        print('locked-and-intent-committed',flush=True)
        await asyncio.sleep(60)
asyncio.run(run())
"""


class StoreTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="mattergram-store-")
        self.path = Path(self.temporary.name) / "private"
        self.master = Fernet.generate_key()
        self.scope = ProvisioningScope(server="https://server.test/prefix", application="app")
        self.key = ResourceKey(kind="command", name="help", team_id="native-team")
        self.store = self.make_store()

    async def asyncTearDown(self):
        self.temporary.cleanup()

    def make_store(self, *, master=None, codec=None):
        return SQLiteProvisioningStore(
            self.path, codec=codec or FernetCodec(SecretBytes(master or self.master))
        )

    async def save(self, *, key=None, secret="issued-secret", action="create"):
        key = key or self.key
        async with self.store.session(self.scope) as session:
            intent = session.begin(key, action, require_secret=True)
            return session.complete(intent, resource_id="native-resource", secret=SecretStr(secret))

    async def test_restart_preserves_binding_secret_and_completed_journal(self):
        binding = await self.save()
        self.assertNotIn("issued-secret", repr(binding))
        async with self.make_store().session(self.scope) as session:
            self.assertEqual(session.binding(self.key), binding)
            self.assertEqual(session.secret(self.key).get_secret_value(), "issued-secret")
            self.assertEqual(session.intents()[0].phase, "completed")
        for path in self.path.iterdir():
            self.assertNotIn(b"issued-secret", path.read_bytes())
            self.assertNotIn(self.master, path.read_bytes())

    async def test_owner_evidence_survives_restart_and_owned_update(self):
        async with self.store.session(self.scope) as session:
            created = session.begin(self.key, "create")
            binding = session.complete(created, resource_id="native-resource", owner_id="native-owner")
            self.assertEqual(binding.owner_id, "native-owner")
        async with self.make_store().session(self.scope) as session:
            updated = session.begin(self.key, "update")
            binding = session.complete(updated, resource_id="native-resource")
            self.assertEqual(binding.owner_id, "native-owner")

    async def test_non_id_step_completes_without_fabricated_binding(self):
        settings = ResourceKey(kind="settings", name="EnableCommands")
        membership = ResourceKey(kind="membership", name="team:user", team_id="native-team")
        async with self.store.session(self.scope) as session:
            for key in (settings, membership):
                intent = session.begin(key, "create")
                session.complete_step(intent)
                self.assertIsNone(session.binding(key))
            invalid = session.begin(ResourceKey(kind="channel", name="alerts"), "create")
            with self.assertRaises(ProvisioningStoreError):
                session.complete_step(invalid)
            secret_step = session.begin(
                ResourceKey(kind="settings", name="secret"), "create", require_secret=True
            )
            with self.assertRaises(ProvisioningStoreError):
                session.complete_step(secret_step)
        async with self.make_store().session(self.scope) as session:
            completed = {intent.key: intent.phase for intent in session.intents()}
            self.assertEqual(completed[settings], "completed")
            self.assertEqual(completed[membership], "completed")
            self.assertEqual(completed[invalid.key], "pending")
            self.assertEqual(completed[secret_step.key], "pending")

    async def test_namespaces_include_server_application_team_and_kind(self):
        await self.save()
        for scope in (
            ProvisioningScope(server="https://other.test", application="app"),
            ProvisioningScope(server=self.scope.server, application="other"),
        ):
            async with self.store.session(scope) as session:
                self.assertIsNone(session.binding(self.key))
                self.assertEqual(session.intents(), ())
        async with self.store.session(self.scope) as session:
            self.assertIsNone(session.binding(self.key.model_copy(update={"team_id": "another-team"})))
            self.assertIsNone(session.binding(self.key.model_copy(update={"kind": "channel"})))
        self.assertEqual(self.scope.server, "https://server.test/prefix/")

    async def test_wrong_key_fails_without_reinitializing_existing_state(self):
        await self.save()
        before = (self.path / "state.sqlite3").read_bytes()
        with self.assertRaises(StoreIntegrityError):
            async with self.make_store(master=Fernet.generate_key()).session(self.scope):
                self.fail("Wrong key entered store")
        self.assertEqual((self.path / "state.sqlite3").read_bytes(), before)
        async with self.store.session(self.scope) as session:
            self.assertIsNotNone(session.binding(self.key))

    async def test_tampered_ciphertext_fails_without_exposing_secret(self):
        await self.save()
        with closing(sqlite3.connect(self.path / "state.sqlite3", isolation_level=None)) as connection:
            row = connection.execute("SELECT payload FROM bindings").fetchone()[0]
            altered = row[:-12] + b"X" * 12
            connection.execute("UPDATE bindings SET payload=?", (altered,))
        async with self.store.session(self.scope) as session:
            with self.assertRaises(StoreIntegrityError) as caught:
                session.secret(self.key)
            self.assertNotIn("issued-secret", str(caught.exception))

    async def test_ciphertext_transplant_to_another_resource_is_rejected(self):
        await self.save()
        second = ResourceKey(kind="command", name="second", team_id="native-team")
        await self.save(key=second)
        with closing(sqlite3.connect(self.path / "state.sqlite3", isolation_level=None)) as connection:
            blob = connection.execute(
                "SELECT payload FROM bindings WHERE key=?", (self.key.digest,)
            ).fetchone()[0]
            connection.execute("UPDATE bindings SET payload=? WHERE key=?", (blob, second.digest))
        async with self.store.session(self.scope) as session:
            with self.assertRaises(StoreIntegrityError):
                session.secret(second)

    async def test_update_preserves_secret_and_cannot_replace_native_identity(self):
        await self.save()
        async with self.store.session(self.scope) as session:
            intent = session.begin(self.key, "update", fingerprint="new-manifest", require_secret=True)
            with self.assertRaises(ProvisioningStoreError):
                session.complete(intent, resource_id="another-native-resource")
            binding = session.complete(intent, resource_id="native-resource")
            self.assertEqual(binding.fingerprint, "new-manifest")
            self.assertEqual(session.secret(self.key).get_secret_value(), "issued-secret")

    async def test_reuse_does_not_grant_update_ownership(self):
        binding = await self.save(action="reuse")
        self.assertEqual(binding.ownership, "reused")
        async with self.store.session(self.scope) as session:
            for action in ("create", "reuse", "update"):
                with self.subTest(action=action), self.assertRaises(ProvisioningStoreError):
                    session.begin(self.key, action)

    async def test_pending_and_uncertain_intents_block_duplicate_until_reconciled(self):
        async with self.store.session(self.scope) as session:
            intent = session.begin(self.key, "create", require_secret=True)
        async with self.make_store().session(self.scope) as session:
            with self.assertRaises(UnresolvedIntentError):
                session.begin(self.key, "create")
            session.uncertain(intent, resource_id="native-issued")
        async with self.make_store().session(self.scope) as session:
            self.assertEqual(session.intents()[0].phase, "indeterminate")
            with self.assertRaises(ProvisioningStoreError):
                session.complete(intent, resource_id="different", secret=SecretStr("issued-secret"))
            session.complete(intent, resource_id="native-issued", secret=SecretStr("issued-secret"))
            self.assertEqual(session.intents()[0].phase, "completed")

    async def test_explicit_confirmed_absence_allows_a_new_attempt(self):
        async with self.store.session(self.scope) as session:
            intent = session.begin(self.key, "create")
            session.confirm_absent(intent)
            replacement = session.begin(self.key, "create")
            self.assertNotEqual(intent.id, replacement.id)
            with self.assertRaises(ProvisioningStoreError):
                session.complete(intent, resource_id="native-resource")

    async def test_conclusive_abort_can_finish_update_without_losing_binding(self):
        await self.save()
        async with self.store.session(self.scope) as session:
            intent = session.begin(self.key, "update")
            session.abort(intent)
            self.assertEqual(session.intents()[-1].phase, "aborted")
            self.assertEqual(session.binding(self.key).resource_id, "native-resource")
            replacement = session.begin(self.key, "update")
            session.complete(replacement, resource_id="native-resource")

    async def test_missing_required_secret_reports_native_id_and_keeps_intent(self):
        async with self.store.session(self.scope) as session:
            intent = session.begin(self.key, "create", require_secret=True)
            with self.assertRaises(PersistenceError) as caught:
                session.complete(intent, resource_id="native-created")
            self.assertEqual(caught.exception.resource_id, "native-created")
            self.assertIsNone(session.binding(self.key))
            self.assertEqual(session.intents()[0].phase, "pending")

    async def test_failed_encryption_rolls_back_binding_and_completion_atomically(self):
        backing = FernetCodec(SecretBytes(self.master))

        class FailingCodec:
            failed = False

            def encrypt(self, value):
                if self.failed:
                    raise OSError("issued-secret must not leak")
                return backing.encrypt(value)

            def decrypt(self, value):
                return backing.decrypt(value)

        codec = FailingCodec()
        async with self.make_store(codec=codec).session(self.scope) as session:
            intent = session.begin(self.key, "create", require_secret=True)
            codec.failed = True
            with self.assertRaises(PersistenceError) as caught:
                session.complete(intent, resource_id="native-created", secret=SecretStr("issued-secret"))
            self.assertNotIn("issued-secret", str(caught.exception))
        async with self.store.session(self.scope) as session:
            self.assertIsNone(session.binding(self.key))
            self.assertEqual(session.intents()[0].phase, "pending")

    async def test_failure_after_binding_write_rolls_back_both_records(self):
        async with self.store.session(self.scope) as session:
            intent = session.begin(self.key, "create", require_secret=True)
            session._connection.execute(
                "CREATE TRIGGER fail_intent BEFORE INSERT ON intents BEGIN SELECT RAISE(ABORT,'simulated storage failure'); END"
            )
            with self.assertRaises(PersistenceError):
                session.complete(intent, resource_id="native-created", secret=SecretStr("issued-secret"))
            self.assertIsNone(session.binding(self.key))
            self.assertEqual(session.intents()[0].phase, "pending")
            session._connection.execute("DROP TRIGGER fail_intent")
            session.complete(intent, resource_id="native-created", secret=SecretStr("issued-secret"))
            another = ResourceKey(kind="command", name="rollback-failure", team_id="native-team")
            pending = session.begin(another, "create", require_secret=True)
            connection = session._connection

            class RollbackFailure:
                def __getattr__(self, name):
                    return getattr(connection, name)

                def rollback(self):
                    connection.rollback()
                    raise sqlite3.OperationalError("storage-detail-issued-secret")

            session._connection = RollbackFailure()
            connection.execute(
                "CREATE TRIGGER fail_intent BEFORE INSERT ON intents BEGIN SELECT RAISE(ABORT,'simulated storage failure'); END"
            )
            with self.assertRaises(PersistenceError) as caught:
                session.complete(
                    pending, resource_id="native-rollback-case", secret=SecretStr("issued-secret")
                )
            self.assertEqual(caught.exception.resource_id, "native-rollback-case")
            self.assertNotIn("issued-secret", str(caught.exception))
        async with self.make_store().session(self.scope) as session:
            self.assertEqual(session.intents()[0].phase, "completed")
            self.assertEqual(session.secret(self.key).get_secret_value(), "issued-secret")

    async def test_hardlinked_database_is_rejected_without_changing_target(self):
        target = Path(self.temporary.name) / "foreign-file"
        target.write_bytes(b"foreign-data")
        os.link(target, self.path / "state.sqlite3")
        with self.assertRaises(PermissionError):
            async with self.store.session(self.scope):
                self.fail("Hardlinked state admitted")
        self.assertEqual(target.read_bytes(), b"foreign-data")

    async def test_task_ownership_closed_session_and_cancelled_waiter_release_handles(self):
        async with self.store.session(self.scope) as session:

            async def wrong_task():
                with self.assertRaises(ProvisioningStoreError):
                    session.intents()

            await asyncio.create_task(wrong_task())
            waiting = asyncio.create_task(self.wait_for_lock())
            await asyncio.sleep(0.08)
            waiting.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await waiting
        with self.assertRaises(ProvisioningStoreError):
            session.intents()
        async with self.make_store().session(self.scope, timeout=0):
            pass

    async def wait_for_lock(self):
        async with self.make_store().session(self.scope, timeout=5):
            self.fail("Lock admitted a second task")

    async def test_os_lock_survives_competition_and_releases_after_process_death(self):
        process = subprocess.Popen(
            [sys.executable, "-I", "-B", "-W", "error", "-c", WORKER],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            process.stdin.write(json.dumps({"directory": str(self.path), "key": self.master.decode()}) + "\n")
            process.stdin.flush()
            line = await asyncio.wait_for(asyncio.to_thread(process.stdout.readline), 10)
            self.assertEqual(line.strip(), "locked-and-intent-committed")
            with self.assertRaises(TimeoutError):
                async with self.store.session(self.scope, timeout=0.1):
                    self.fail("Two processes acquired one store lock")
            process.kill()
            await asyncio.to_thread(process.wait, 10)
            async with self.store.session(self.scope, timeout=2) as session:
                intent = session.intents()[0]
                self.assertEqual(intent.phase, "pending")
                with self.assertRaises(UnresolvedIntentError):
                    session.begin(intent.key, "create")
        finally:
            if process.poll() is None:
                process.kill()
                await asyncio.to_thread(process.wait, 10)
            for stream in (process.stdin, process.stdout, process.stderr):
                stream.close()

    async def test_private_permissions_reject_unexpected_principal_without_repair(self):
        await self.save()
        target = self.path / "state.sqlite3"
        if os.name == "nt":
            subprocess.run(["icacls", str(target), "/grant", "*S-1-1-0:R"], check=True, capture_output=True)
        else:
            target.chmod(0o644)
        with self.assertRaises(PermissionError):
            async with self.store.session(self.scope):
                self.fail("Broadly readable state admitted")

    async def test_foreign_sqlite_and_wrong_schema_are_not_reinitialized(self):
        path = self.path / "state.sqlite3"
        from mattergram.provisioning._private_files import file

        os.close(file(path))
        with closing(sqlite3.connect(path, isolation_level=None)) as connection:
            connection.execute("CREATE TABLE foreign_data(value TEXT)")
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        with self.assertRaises(StoreIntegrityError):
            async with self.store.session(self.scope):
                self.fail("Foreign database admitted")
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), before)
