"""Encrypted SQLite bindings/journal with an OS-owned local-process lock."""

from __future__ import annotations

import asyncio
import json
import math
import os
import secrets
import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import SecretBytes, SecretStr, ValidationError

from mattergram.exceptions import MattergramError

from . import _private_files as private
from .store_models import ProvisioningIntent, ProvisioningScope, ResourceBinding, ResourceKey, canonical


class ProvisioningStoreError(MattergramError):
    """Local state failure; never contains a credential or decrypted payload."""


class StoreIntegrityError(ProvisioningStoreError):
    """Wrong key, tampering, schema mismatch or invalid persisted state."""


class UnresolvedIntentError(ProvisioningStoreError):
    """Reconcile the pending/indeterminate write before any new operation."""


class PersistenceError(ProvisioningStoreError):
    def __init__(self, resource_id: str) -> None:
        self.resource_id = resource_id
        super().__init__(
            f"Could not persist resource {resource_id}; retain state and reconcile this native ID before retrying"
        )


class SecretCodec(Protocol):
    def encrypt(self, value: bytes) -> bytes: ...
    def decrypt(self, value: bytes) -> bytes: ...


class FernetCodec:
    """Authenticated recipe from cryptography; caller supplies a key outside state_dir."""

    def __init__(self, key: SecretBytes) -> None:
        if not isinstance(key, SecretBytes):
            raise TypeError("FernetCodec requires a SecretBytes external key")
        try:
            from cryptography.fernet import Fernet
        except ImportError:
            raise ImportError("Install mattergram[provisioning] to use FernetCodec") from None
        try:
            self._fernet = Fernet(key.get_secret_value())
        except (ValueError, TypeError):
            raise ValueError("Invalid Fernet key") from None

    def encrypt(self, value: bytes) -> bytes:
        result: bytes = self._fernet.encrypt(value)
        return result

    def decrypt(self, value: bytes) -> bytes:
        try:
            result: bytes = self._fernet.decrypt(value)
            return result
        except Exception:
            raise StoreIntegrityError("Cannot authenticate provisioning state with this key") from None


class SQLiteProvisioningStore:
    """One private directory, external codec, SQLite FULL commits and local OS lock.

    Sessions serialize the whole directory, including across scopes/processes.
    Use a local persistent filesystem; this is not a distributed/NFS lease.
    """

    def __init__(self, state_dir: str | Path, *, codec: SecretCodec) -> None:
        self.path = Path(state_dir).absolute()
        if ".." in self.path.parts or self.path.drive.startswith("\\\\"):
            raise ValueError("Provisioning requires an explicit local state directory")
        private.directory(self.path)
        self._codec = codec

    def _pack(self, context: object, value: dict[str, Any]) -> bytes:
        try:
            return self._codec.encrypt(canonical({"context": context, "value": value}))
        except Exception:
            raise ProvisioningStoreError("Could not encrypt provisioning state") from None

    def _unpack(self, context: object, blob: bytes) -> dict[str, Any]:
        try:
            value = json.loads(self._codec.decrypt(blob))
            if value["context"] != context or not isinstance(value["value"], dict):
                raise ValueError
            result: dict[str, Any] = value["value"]
            return result
        except Exception:
            raise StoreIntegrityError("Provisioning state authentication/context validation failed") from None

    def _connect(self) -> sqlite3.Connection:
        path = self.path / "state.sqlite3"
        for suffix in ("-journal", "-wal", "-shm"):
            sidecar = Path(str(path) + suffix)
            if sidecar.exists() or sidecar.is_symlink():
                private.verify(sidecar)
        descriptor = private.file(path)
        os.close(descriptor)
        existing = path.stat().st_size != 0
        connection = sqlite3.connect(path, timeout=0, isolation_level=None)
        try:
            connection.execute("PRAGMA trusted_schema=OFF")
            connection.execute("PRAGMA synchronous=FULL")
            if existing:
                row = connection.execute("SELECT payload FROM meta WHERE id=1").fetchone()
                if row is None or self._unpack(["meta", 1], row[0]) != {"format": 1}:
                    raise StoreIntegrityError("Unsupported provisioning state format")
            else:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    "CREATE TABLE meta (id INTEGER PRIMARY KEY CHECK(id=1), payload BLOB NOT NULL)"
                )
                connection.execute(
                    "CREATE TABLE bindings (scope TEXT, key TEXT, payload BLOB NOT NULL, PRIMARY KEY(scope,key))"
                )
                connection.execute(
                    "CREATE TABLE intents (scope TEXT, id TEXT, key TEXT, payload BLOB NOT NULL, PRIMARY KEY(scope,id))"
                )
                connection.execute("INSERT INTO meta VALUES(1,?)", (self._pack(["meta", 1], {"format": 1}),))
                connection.commit()
            return connection
        except BaseException:
            connection.close()
            raise

    @asynccontextmanager
    async def session(
        self, scope: ProvisioningScope, *, timeout: float = 30.0
    ) -> AsyncIterator[StoreSession]:
        if (
            not isinstance(scope, ProvisioningScope)
            or isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout)
            or timeout < 0
        ):
            raise ValueError("Expected a provisioning scope and finite nonnegative lock timeout")
        private.verify(self.path, is_directory=True)
        descriptor = private.file(self.path / "state.lock")
        connection = None
        session = None
        try:
            deadline = asyncio.get_running_loop().time() + timeout
            while True:
                try:
                    if os.name == "nt":
                        import msvcrt

                        os.lseek(descriptor, 0, os.SEEK_SET)
                        getattr(msvcrt, "locking")(descriptor, getattr(msvcrt, "LK_NBLCK"), 1)
                    else:
                        import fcntl

                        getattr(fcntl, "flock")(
                            descriptor, getattr(fcntl, "LOCK_EX") | getattr(fcntl, "LOCK_NB")
                        )
                    break
                except OSError as error:
                    if error.errno not in {11, 13, 35, 36}:
                        raise ProvisioningStoreError("Cannot acquire provisioning OS lock") from None
                    if asyncio.get_running_loop().time() >= deadline:
                        raise TimeoutError("Provisioning state lock is busy") from None
                    await asyncio.sleep(min(0.05, max(0.0, deadline - asyncio.get_running_loop().time())))
            connection = self._connect()
            session = StoreSession(self, scope, connection)
            yield session
        except sqlite3.Error:
            raise StoreIntegrityError(
                "Cannot open/read provisioning SQLite state; retain it for recovery"
            ) from None
        finally:
            if session is not None:
                session._active = False
            if connection is not None:
                connection.close()
            # Closing the descriptor releases the OS lock, including on process death.
            os.close(descriptor)


class StoreSession:
    """Synchronous local commits while the async context owns the process lock."""

    def __init__(
        self, store: SQLiteProvisioningStore, scope: ProvisioningScope, connection: sqlite3.Connection
    ) -> None:
        self._store, self.scope, self._connection = store, scope, connection
        self._owner, self._active = asyncio.current_task(), True

    def _check(self) -> None:
        if not self._active or asyncio.current_task() is not self._owner:
            raise ProvisioningStoreError("Provisioning session is closed or belongs to another task")

    def _context(self, kind: str, identity: str) -> list[Any]:
        return [kind, self.scope.model_dump(), identity]

    def _binding_data(self, key: ResourceKey) -> dict[str, Any] | None:
        self._check()
        row = self._connection.execute(
            "SELECT payload FROM bindings WHERE scope=? AND key=?", (self.scope.digest, key.digest)
        ).fetchone()
        return None if row is None else self._store._unpack(self._context("binding", key.digest), row[0])

    def binding(self, key: ResourceKey) -> ResourceBinding | None:
        data = self._binding_data(key)
        if data is None:
            return None
        try:
            result = ResourceBinding.model_validate(data["binding"])
            if result.key != key or result.has_secret != (data.get("secret") is not None):
                raise ValueError
            return result
        except (ValidationError, ValueError, KeyError):
            raise StoreIntegrityError("Invalid persisted resource binding") from None

    def secret(self, key: ResourceKey) -> SecretStr | None:
        binding = self.binding(key)
        if binding is None or not binding.has_secret:
            return None
        data = self._binding_data(key)
        assert data is not None
        if not isinstance(data.get("secret"), str) or not data["secret"]:
            raise StoreIntegrityError("Invalid persisted resource secret")
        return SecretStr(data["secret"])

    def intents(self) -> tuple[ProvisioningIntent, ...]:
        self._check()
        result = []
        for identifier, key, blob in self._connection.execute(
            "SELECT id,key,payload FROM intents WHERE scope=? ORDER BY rowid", (self.scope.digest,)
        ):
            try:
                intent = ProvisioningIntent.model_validate(
                    self._store._unpack(self._context("intent", identifier), blob)
                )
                if intent.id != identifier or intent.key.digest != key:
                    raise ValueError
                result.append(intent)
            except (ValidationError, ValueError):
                raise StoreIntegrityError("Invalid persisted provisioning intent") from None
        return tuple(result)

    def _save_intent(self, intent: ProvisioningIntent) -> None:
        self._connection.execute(
            "INSERT OR REPLACE INTO intents VALUES(?,?,?,?)",
            (
                self.scope.digest,
                intent.id,
                intent.key.digest,
                self._store._pack(self._context("intent", intent.id), intent.model_dump()),
            ),
        )

    def begin(
        self,
        key: ResourceKey,
        action: Literal["create", "update", "reuse"],
        *,
        fingerprint: str | None = None,
        require_secret: bool = False,
    ) -> ProvisioningIntent:
        self._check()
        if any(i.key == key and i.phase in {"pending", "indeterminate"} for i in self.intents()):
            raise UnresolvedIntentError(
                "Reconcile the existing native write before starting another operation"
            )
        binding = self.binding(key)
        if (action in {"create", "reuse"} and binding is not None) or (
            action == "update" and (binding is None or binding.ownership != "owned")
        ):
            raise ProvisioningStoreError("Operation conflicts with the current ownership/binding")
        intent = ProvisioningIntent(
            id=secrets.token_hex(16),
            key=key,
            action=action,
            fingerprint=fingerprint,
            require_secret=require_secret,
        )
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            self._save_intent(intent)
            self._connection.commit()
        except BaseException:
            self._connection.rollback()
            raise
        return intent

    def _current(self, intent: ProvisioningIntent) -> ProvisioningIntent:
        self._check()
        current = next((i for i in self.intents() if i.id == intent.id), None)
        if current is None or current.key != intent.key or current.phase not in {"pending", "indeterminate"}:
            raise ProvisioningStoreError("Intent is missing, terminal or belongs to another scope")
        return current

    def uncertain(self, intent: ProvisioningIntent, *, resource_id: str | None = None) -> None:
        current = self._current(intent)
        updated = ProvisioningIntent.model_validate(
            current.model_dump()
            | {"phase": "indeterminate", "resource_id": resource_id or current.resource_id}
        )
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            self._save_intent(updated)
            self._connection.commit()
        except BaseException:
            self._connection.rollback()
            raise

    def complete(
        self,
        intent: ProvisioningIntent,
        *,
        resource_id: str,
        secret: SecretStr | None = None,
        owner_id: str | None = None,
    ) -> ResourceBinding:
        current = self._current(intent)
        previous = self.binding(current.key)
        if current.action == "update" and (previous is None or previous.resource_id != resource_id):
            raise ProvisioningStoreError("An update cannot replace the native resource identity")
        if current.resource_id is not None and current.resource_id != resource_id:
            raise ProvisioningStoreError(
                "Reconciliation cannot replace the recorded native resource identity"
            )
        if secret is not None and (not isinstance(secret, SecretStr) or not secret.get_secret_value()):
            raise ValueError("Persist a nonempty SecretStr or preserve the existing secret")
        value = secret if secret is not None else self.secret(current.key)
        if current.require_secret and value is None:
            raise PersistenceError(resource_id)
        binding = ResourceBinding(
            key=current.key,
            resource_id=resource_id,
            ownership="reused" if current.action == "reuse" else "owned",
            owner_id=owner_id if owner_id is not None else (previous.owner_id if previous else None),
            fingerprint=current.fingerprint,
            has_secret=value is not None,
        )
        data = {
            "binding": binding.model_dump(),
            "secret": value.get_secret_value() if value is not None else None,
        }
        updated = ProvisioningIntent.model_validate(
            current.model_dump() | {"phase": "completed", "resource_id": resource_id}
        )
        try:
            self._connection.execute("BEGIN IMMEDIATE")
            self._connection.execute(
                "INSERT OR REPLACE INTO bindings VALUES(?,?,?)",
                (
                    self.scope.digest,
                    current.key.digest,
                    self._store._pack(self._context("binding", current.key.digest), data),
                ),
            )
            self._save_intent(updated)
            self._connection.commit()
        except Exception:
            try:
                self._connection.rollback()
            except sqlite3.Error:
                pass  # Preserve the native recovery ID even when rollback also fails.
            raise PersistenceError(resource_id) from None
        return binding

    def confirm_absent(self, intent: ProvisioningIntent) -> None:
        """Explicit external reconciliation decision; does not query the native API."""
        current = self._current(intent)
        if current.action == "update":
            raise ProvisioningStoreError("An absent update target requires explicit resource recovery")
        self.abort(current)

    def abort(self, intent: ProvisioningIntent) -> None:
        """Finish an operation after a conclusive response proved no native write occurred."""
        current = self._current(intent)
        updated = ProvisioningIntent.model_validate(current.model_dump() | {"phase": "aborted"})
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            self._save_intent(updated)
            self._connection.commit()
        except BaseException:
            self._connection.rollback()
            raise

    def complete_step(self, intent: ProvisioningIntent) -> None:
        """Finish a non-ID settings/membership step without inventing a native ID."""
        current = self._current(intent)
        if current.key.kind not in {"settings", "membership"} or current.require_secret:
            raise ProvisioningStoreError("This step requires a real native binding/secret")
        updated = ProvisioningIntent.model_validate(current.model_dump() | {"phase": "completed"})
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            self._save_intent(updated)
            self._connection.commit()
        except BaseException:
            self._connection.rollback()
            raise
