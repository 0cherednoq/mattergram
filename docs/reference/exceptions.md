# Exceptions

Reference for implemented SDK exceptions. Catch the narrowest class that matches the recovery action.

| Exception | Base | Meaning | Source |
| --- | --- | --- | --- |
| `APIError` | `MattergramError` | A definite HTTP response; writes are never retried automatically. | [src/mattergram/exceptions.py](../../src/mattergram/exceptions.py#L10) |
| `AuthenticationError` | `APIError` | The bot credential was rejected (401). | [src/mattergram/exceptions.py](../../src/mattergram/exceptions.py#L28) |
| `BotBindingError` | `MattergramError` | A shortcut needs a bot explicitly bound by from_wire/normalization. | [src/mattergram/exceptions.py](../../src/mattergram/exceptions.py#L56) |
| `ClientClosedError` | `MattergramError` | A terminally closed bot cannot create another client session. | [src/mattergram/exceptions.py](../../src/mattergram/exceptions.py#L52) |
| `ContextCollisionError` | `MattergramError, ValueError` | Application/filter data attempted to replace reserved system context. | [src/mattergram/exceptions.py](../../src/mattergram/exceptions.py#L72) |
| `DataNotDictLikeError` | `MattergramError, TypeError` | FSM data must be a mapping. | [src/mattergram/exceptions.py](../../src/mattergram/exceptions.py#L64) |
| `IngressAuthenticationError` | `MattergramError` | A request failed source, token, signature, scope or expiry validation. | [src/mattergram/exceptions.py](../../src/mattergram/exceptions.py#L92) |
| `IngressCapacityError` | `MattergramError` | No bounded processing/deferred capacity is available. | [src/mattergram/exceptions.py](../../src/mattergram/exceptions.py#L96) |
| `MattergramError` | `Exception` | Base for errors crossing the SDK boundary. | [src/mattergram/exceptions.py](../../src/mattergram/exceptions.py#L6) |
| `PermissionDeniedError` | `APIError` | The server refused the operation (403). | [src/mattergram/exceptions.py](../../src/mattergram/exceptions.py#L32) |
| `ProtocolError` | `MattergramError` | Malformed wire data or a response that contradicts the operation contract. | [src/mattergram/exceptions.py](../../src/mattergram/exceptions.py#L40) |
| `RateLimitError` | `APIError` | The server returned 429; retry_after preserves its header without retrying. | [src/mattergram/exceptions.py](../../src/mattergram/exceptions.py#L36) |
| `RequestOutcomeUnknown` | `TransportError` | A write may have reached the server. Reconcile before deciding to retry. | [src/mattergram/exceptions.py](../../src/mattergram/exceptions.py#L48) |
| `ResponseDeadlineError` | `ResponseStateError, TimeoutError` | The initial integration response deadline has expired. | [src/mattergram/exceptions.py](../../src/mattergram/exceptions.py#L84) |
| `ResponseStateError` | `MattergramError, RuntimeError` | An HTTP response is unbound, already completed or no longer usable. | [src/mattergram/exceptions.py](../../src/mattergram/exceptions.py#L80) |
| `ShutdownTimeoutError` | `MattergramError, TimeoutError` | An in-flight handler did not finish within the configured shutdown bound. | [src/mattergram/exceptions.py](../../src/mattergram/exceptions.py#L76) |
| `StorageDataError` | `MattergramError, ValueError` | Stored FSM data has an invalid shape or encoding; raw data is not included. | [src/mattergram/exceptions.py](../../src/mattergram/exceptions.py#L68) |
| `TransportError` | `MattergramError` | A read failed or the connection could not be established. | [src/mattergram/exceptions.py](../../src/mattergram/exceptions.py#L44) |
| `UnsupportedCapabilityError` | `MattergramError` | The selected server profile does not implement this operation. | [src/mattergram/exceptions.py](../../src/mattergram/exceptions.py#L88) |
| `UnsupportedKeywordArgument` | `MattergramError, TypeError` | An observer received unsupported registration arguments. | [src/mattergram/exceptions.py](../../src/mattergram/exceptions.py#L60) |

`RequestOutcomeUnknown` means a write may have reached Mattermost. Reconcile server state before retrying.

`ResponseStateError` and `ResponseDeadlineError` indicate interaction lifecycle errors. They do not mean that a second response is safe.
