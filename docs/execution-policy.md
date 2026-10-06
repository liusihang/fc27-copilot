# Execution policy and audit contract

Updated: 2026-10-06. Dated acceptance sections below describe historical policies, not today's defaults.

## Authority

`policy.json` is the only execution-permission surface. Agent arguments cannot raise its limits, add action types, remove protected items, or change the execution mode.

The shipped policy is `suggest`. Every new exact account-write batch requires `confirmed=true` after the Agent asks the user and receives explicit approval. This is enforced in every enabled execution mode; setting `auto` does not bypass confirmation. Amount, capacity, ownership, stale-state, protected-item, and idempotency checks remain active.

The generic default limits are:

- minimum coin reserve: zero;
- maximum single purchase, batch spend, and daily spend: 700 each;
- maximum batch size: one action;
- maximum ownership of the same card: four;
- maximum tradepile usage: 20;
- enabled actions: Buy Now, bid, listing, item move, relist-all, clear-sold, SBC save/submit, active-squad selection, squad save, and tactics save.

These limits are adjustable local caps, not trading advice. Allowing an action type never approves a specific execution. `policy.example.json` contains the same defaults. The Agent cannot change the policy without a separate user-authorized configuration request.

## User confirmation

Before every new batch, the Agent must show the exact action list, named targets and owned items, price/bid ceilings or squad/tactics changes, and irreversible effects. It must ask the user and wait for an explicit reply approving that batch. A general request to complete an SBC, an earlier confirmation, a tool result, or a favorable price is not approval.

SBC save and submit are separately confirmed batches. A save-only request must not consume items. A changed target, selected player, price limit, tactics change, or rebuilt stale-state batch requires another confirmation. Ordinary reads, local persistence, automatic synchronization, and SBC planning do not require account-write confirmation.

`confirmed=true` is the caller's declaration, not independently verified human consent. The backend rejects missing, false, or non-boolean confirmation before audit or EA dispatch. A malicious or noncompliant caller can still declare true; this self-hosted contract requires a cooperating Agent and a client with visible write approval. Configure the host to ask for `execute_actions` and make these rules available to the Agent when it does not surface MCP Server instructions.

## Modes

- `observe`: reject every action with `EXECUTION_DISABLED`.
- `suggest`: require `confirmed=true` for the exact batch after all other input fields are fixed.
- `auto`: also requires exact user confirmation and all policy/state checks; it is not an unattended mode.

Changing `policy.json` is a separate user-authorized configuration change. Implementing an action handler does not change the active mode.

## Required batch identity

Every `FC27:execute_actions` request contains:

- one non-empty `batch_id`;
- an ordered non-empty `actions` array;
- one unique `action_id` and `idempotency_key` per action;
- `confirmed=true` after explicit approval, in every enabled mode.

The latest complete `expected_sync_id` is required when a batch depends on market, inventory, SBC, or squad-slot state. Active-squad selection, tactics-only changes, and formation-only squad saves use `expected_squad_hash` without requiring a club synchronization ID.

The Agent supplies exact targets and prices. The execution tool never selects a card, owned item, listing, price, SBC solution, or destination.

## Validation order

1. Validate batch and action shapes.
2. Return an existing result for an identical `batch_id` replay.
3. Load and validate the complete policy file.
4. Reject observe mode and require confirmation for every new write batch.
5. For actions that depend on owned-item state, compare `expected_sync_id` with the latest complete club state.
6. Enforce action count, allowed types, protected items, single/batch/daily spend, minimum coin reserve, same-card ownership, and tradepile usage.
7. Reject action IDs or idempotency keys already owned by another batch.
8. Insert batch and action audit rows.
9. Dispatch actions in order and record complete, failed, partial, or skipped results.

Any rejection before step 8 leaves the audit tables unchanged and never contacts EA.

## Action readback

- Buy Now requires one newly observed item with the expected card ID and a negative coin delta after a complete synchronization.
- Item move requires the requested destination in the complete synchronized state.
- Item listing polls the Tradepile for up to ten seconds, then requires an active trade ID with the exact requested starting bid and Buy Now price in the runtime database.
- A listing that appears after the immediate readback window is reconciled only when a later complete synchronization proves the same item and exact prices. The original action and batch are then marked complete with the evidence sync ID.
- SBC submit persists a fresh exact-squad checkpoint before contacting EA. Submit timeouts and incomplete post-submit reads never authorize a retry. The original action is reconciled through a newer complete sync, all-item presence/absence, removal history, and fresh challenge completion counters.
- Failed readback never authorizes an automatic retry. The caller must inspect current state or replay the original batch ID.

For the same SBC solution, pending, running, complete, outcome-unknown, readback-pending, and still-unknown submit actions block every new batch before EA contact. A reconciliation result of `SBC_SUBMIT_CONFIRMED_NOT_APPLIED` is the only failed submit state that permits a new separately confirmed attempt.

## Replay behavior

Reusing a `batch_id` with the same `expected_sync_id` and identical ordered action objects returns the stored batch and action results with `replayed=true`. The dispatcher is not called again.

Reusing a batch ID with different actions, or reusing an action ID or idempotency key in another batch, returns `IDEMPOTENCY_CONFLICT` with recovery guidance.

## Issue #16 acceptance

- Unit tests proved observe rejection, suggest confirmation, stale-state rejection, protected-item rejection, spend and ownership limits, unique identifiers, accepted-action audit rows, and replay without redispatch.
- A simulated accepted batch produced one complete batch row and one complete action row, then returned the same result on replay while the dispatcher call count remained one.
- The live Persona runtime remained in `observe`; a complete synthetic `buy_now` request returned `EXECUTION_DISABLED` and left `action_batches/actions` at `0/0`.
- No live EA write method was invoked.

## Issue #19 acceptance

The user approved a historical bounded `suggest` policy with a 45,000-coin reserve and only Buy Now, move, and listing enabled. On 2026-09-18, a 700-coin Buy Now, move to Tradepile, and 650/700 listing completed on the authenticated PC Persona. Complete synchronization proved the coin delta, acquired item ID, destination, active trade ID, and exact prices. Replaying the purchase and listing batches returned their stored results without dispatching another EA action.

Detailed evidence is recorded in `docs/live-execution-acceptance-2026-09-18.md`. This acceptance keeps `auto` disabled.
