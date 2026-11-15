from __future__ import annotations

import unittest
from types import MappingProxyType

from mattergram.native import OPERATIONS, NativeAPI, NativeOperation


class NativeOperationTests(unittest.IsolatedAsyncioTestCase):
    def test_generated_operation_renders_only_exact_encoded_path_parameters(self) -> None:
        self.assertIsInstance(OPERATIONS, MappingProxyType)
        operation = OPERATIONS["GetUserByUsername"]
        self.assertEqual(operation.path_parameters, ("username",))
        self.assertEqual(
            operation.render_path({"username": "name/with space"}), "users/username/name%2Fwith%20space"
        )
        for parameters in ({}, {"username": "name", "extra": "value"}, {"username": ""}):
            with self.subTest(parameters=parameters), self.assertRaises(ValueError):
                operation.render_path(parameters)

    def test_operation_metadata_rejects_unsafe_or_unverified_shapes(self) -> None:
        base = {
            "operation_id": "GetThing",
            "method": "GET",
            "path_template": "things/{thing_id}",
            "success_statuses": (200,),
            "body": "forbidden",
            "source": "api/v4/source/things.yaml",
            "source_sha256": "a" * 64,
        }
        for change in (
            {"operation_id": "bad-id"},
            {"path_template": "../things"},
            {"success_statuses": (404,)},
            {"success_statuses": (200, 200)},
            {"body": "required"},
            {"source": "outside.yaml"},
            {"source_sha256": "bad"},
        ):
            with self.subTest(change=change), self.assertRaises(ValueError):
                NativeOperation(**(base | change))

    async def test_client_rejects_unknown_operation_body_and_path_before_network(self) -> None:
        client = NativeAPI(token="fixture", base_url="http://127.0.0.1:1")
        self.addAsyncCleanup(client.close)
        with self.assertRaisesRegex(ValueError, "Unknown generated"):
            await client.call("Missing")
        with self.assertRaisesRegex(ValueError, "requires a JSON"):
            await client.call("CreatePost")
        with self.assertRaisesRegex(ValueError, "does not accept"):
            await client.call("GetUser", path={"user_id": "me"}, body={})
        with self.assertRaisesRegex(ValueError, "missing"):
            await client.call("GetUser")
        with self.assertRaisesRegex(TypeError, "JSON values"):
            await client.call("CreatePost", body={"bad": object()})
        with self.assertRaisesRegex(ValueError, "finite"):
            await client.call("CreatePost", body={"bad": float("nan")})
        recursive: list[object] = []
        recursive.append(recursive)
        with self.assertRaisesRegex(ValueError, "cycle"):
            await client.call("CreatePost", body=recursive)

    async def test_client_rejects_unregistered_metadata_and_unsafe_query_before_network(self) -> None:
        client = NativeAPI(token="fixture", base_url="http://127.0.0.1:1")
        self.addAsyncCleanup(client.close)
        registered = OPERATIONS["GetUser"]
        forged = NativeOperation(
            operation_id=registered.operation_id,
            method=registered.method,
            path_template=registered.path_template,
            success_statuses=registered.success_statuses,
            body=registered.body,
            source=registered.source,
            source_sha256="b" * 64,
        )
        with self.assertRaisesRegex(ValueError, "not from the generated registry"):
            await client.call(forged, path={"user_id": "me"})
        with self.assertRaisesRegex(TypeError, "operationId"):
            await client.call(123)  # type: ignore[arg-type]
        for query, error in (
            ({"bad name": "value"}, ValueError),
            ({"empty": ()}, ValueError),
            ({"bad": object()}, TypeError),
            ({"bad": float("nan")}, ValueError),
            ({"bad": ("ok", object())}, TypeError),
        ):
            with self.subTest(query=query), self.assertRaises(error):
                await client.call("GetUser", path={"user_id": "me"}, query=query)  # type: ignore[arg-type]
        self.assertIsNone(client._session)


if __name__ == "__main__":
    unittest.main()
