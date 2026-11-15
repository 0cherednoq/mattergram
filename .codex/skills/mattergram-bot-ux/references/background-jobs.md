# Background jobs and progress

Read this reference when an import, batch check, export, or external operation can outlast an HTTP interaction.

## Interaction contract

1. The action/controller validates input and service authorization.
2. It creates a job, stores a `DialogRef`, switches to a queued progress window, and returns.
3. A worker performs the operation outside the callback request.
4. The worker publishes coalesced snapshots with strictly increasing revisions.
5. A terminal snapshot is persisted even when the dialog post is deleted or its intent becomes stale.

Do not use a background task as a substitute for durable storage. The post is a presentation target, not the source of truth for job state.

## Implemented Game Admin API

The feature-specific implementation is available from `mattergram.contrib.game_accounts_admin`:

- `GameAdminJobs` authorizes and enqueues imports and batch checks;
- `GameAdminJobRepository` is the durable state port;
- `GameAdminJobQueue` is the producer-side queue port;
- `GameAdminJobRunner` executes one delivered job ID;
- `CoalescingDialogJobUpdater` adapts the shared `CoalescingDialogUpdater`;
- `EphemeralMemoryGameAdminJobRepository` and `EphemeralMemoryGameAdminJobQueue` are local-demo/test adapters only.

There is no generic `JobsPlugin`. Do not import a planned plugin or generalize the feature contract until another independent consumer proves the same lifecycle.

## Composition boundary

The composition root owns repositories, queue delivery, runner lifetime, and shutdown. The dialog only receives `GameAdminJobs`:

```python
job_repository = EphemeralMemoryGameAdminJobRepository()
job_queue = EphemeralMemoryGameAdminJobQueue()
jobs = GameAdminJobs(service, job_repository, job_queue)

registry = DialogRegistry(build_admin_dialog(service, jobs=jobs))
# Construct the ordinary DialogRuntime with this registry first.
updates = CoalescingDialogUpdater(runtime, interval=0.25)
runner = GameAdminJobRunner(
    service,
    job_repository,
    CoalescingDialogJobUpdater(updates),
)
```

This snippet demonstrates local composition. It is not a production deployment recipe. Production supplies durable implementations for both job ports and invokes `runner.run(job_id)` from its queue consumer. At-least-once delivery is allowed, so the repository must enforce compare-and-swap revisions and immutable terminal state.

## Cancellation and stale posts

- Offer Cancel only when the operation checks cancellation between bounded units of work.
- A queued cancellation becomes terminal immediately.
- A running cancellation is cooperative. Do not label it cancelled before the worker observes it.
- Treat `OutdatedIntent` as detached presentation. Continue the business operation and retain terminal history.
- Do not create a replacement post automatically unless the product explicitly requires a durable terminal notification.

## Verification

Test these properties independently of a live client:

- revisions are strictly monotonic and stale writes are rejected;
- terminal snapshots cannot be mutated or duplicated in history;
- queued and running cancellation reach a terminal state;
- stale `DialogRef` stops UI delivery without losing the job result;
- snapshots contain counters and safe error types, not credentials or raw import rows;
- the initiating callback enqueues and switches state without executing the whole batch.

Run a live Mattermost check when the change also modifies client-dependent layout, forms, or plugin behavior. A repository/runner contract test alone does not verify Web, Desktop, or mobile rendering.
