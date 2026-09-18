# Execution policy and audit contract

Date: 2026-09-18

## Authority

`policy.json` is the only execution-permission surface. Agent arguments cannot raise its limits, add action types, remove protected items, or change the execution mode.

The shipped policy is `observe` with zero spend and zero enabled action types. It rejects every account-changing batch before contacting EA or writing action-audit rows.

## Modes

- `observe`: reject every action with `EXECUTION_DISABLED`.
- `suggest`: require `confirmed=true` for the exact batch after all other input fields are fixed.
- `auto`: execute only actions that pass every policy and stale-state check.

Changing `policy.json` is a separate user-authorized configuration change. Implementing an action handler does not change the active mode.

## Required batch identity

Every `FC27:execute_actions` request contains:

- one non-empty `batch_id`;
- the latest complete `expected_sync_id`;
- an ordered non-empty `actions` array;
- one unique `action_id` and `idempotency_key` per action;
- `confirmed=true` when mode is `suggest`.

The Agent supplies exact targets and prices. The execution tool never selects a card, owned item, listing, price, SBC solution, or destination.

## Validation order

1. Validate batch and action shapes.
2. Return an existing result for an identical `batch_id` replay.
3. Load and validate the complete policy file.
4. Enforce mode and suggest-mode confirmation.
5. Compare `expected_sync_id` with the latest complete club state.
6. Enforce action count, allowed types, protected items, single/batch/daily spend, minimum coin reserve, same-card ownership, and tradepile usage.
7. Reject action IDs or idempotency keys already owned by another batch.
8. Insert batch and action audit rows.
9. Dispatch actions in order and record complete, failed, partial, or skipped results.

Any rejection before step 8 leaves the audit tables unchanged and never contacts EA.

## Replay behavior

Reusing a `batch_id` with the same `expected_sync_id` and identical ordered action objects returns the stored batch and action results with `replayed=true`. The dispatcher is not called again.

Reusing a batch ID with different actions, or reusing an action ID or idempotency key in another batch, returns `IDEMPOTENCY_CONFLICT` with recovery guidance.

## Issue #16 acceptance

- Unit tests proved observe rejection, suggest confirmation, stale-state rejection, protected-item rejection, spend and ownership limits, unique identifiers, accepted-action audit rows, and replay without redispatch.
- A simulated accepted batch produced one complete batch row and one complete action row, then returned the same result on replay while the dispatcher call count remained one.
- The live Persona runtime remained in `observe`; a complete synthetic `buy_now` request returned `EXECUTION_DISABLED` and left `action_batches/actions` at `0/0`.
- No live EA write method was invoked.
