# SBC capture, solving, and execution contract

Date: 2026-09-20

## Agent and tool boundary

`FC27:sbc_refresh` captures and persists EA facts. `FC27:sbc_query` reads the persisted local cache without contacting EA. `FC27:sbc_solve` requires both `set_id` and `challenge_id`, applies a caller-provided objective through OR-Tools CP-SAT, and returns exact owned item IDs with solver and independent validation evidence. The Agent chooses the challenge, objective, and any mandatory owned items. Saving and submission occur only through exact `FC27:execute_actions` batches.

Normal SBC responses omit raw EA evidence. `include_raw=true` is reserved for unsupported-requirement diagnosis and contract development.

The active policy does not enable `save_sbc_squad` or `submit_sbc`.

## Captured requirement encoding

FC27 returns each requirement as one key/value tuple plus `count` and `scope`. The accepted scope mapping is:

| Scope | Operator |
|---:|---|
| 0 | minimum |
| 1 | maximum |
| 2 | exact |

The current normalized requirement keys are:

| Key | Meaning |
|---:|---|
| 3 | minimum/maximum/exact squad quality; values 1/2/3 are bronze/silver/gold |
| 17 | minimum/maximum/exact player count for quality 1/2/3 |
| 19 | team rating |
| 26 | count of players at or above one OVR threshold |
| 28 | count of players at or below one OVR threshold |
| 7 | distinct nation count |
| 8 | distinct league count |
| 9 | distinct club count |
| 10 | count of players from one specific nation |
| 11 | count of players from one specific league |
| 12 | count of players from one of a specific set of clubs |
| 4 | maximum players from one league |
| 5 | maximum players from one nation |
| 6 | maximum players from one club |
| 35 | minimum/maximum/exact squad chemistry |

Unknown keys, malformed tuples, unknown values, missing or contradictory player counts, and partial squads without exact fillable field-slot evidence are persisted in `unsupported_constraints`. The solver refuses those challenges with `SBC_SCHEMA_UNSUPPORTED`.

## Candidate objective

The objective may contain:

- `candidate_item_ids`: restrict solving to these current owned item IDs;
- `required_item_ids`: require these exact current owned item IDs in every candidate;
- `exclude_item_ids`: remove explicit items;
- `prefer_untradeable`: sort untradeable items first;
- `max_tradeable_value`: reject a squad above the limit;
- `max_item_overall`: preserve higher-rated items;
- `max_solutions`: return at most ten candidates through the tool parameter.

Protected items, loan items, Tradepile items, stale/missing items, duplicates, and cards absent from the catalog are excluded or rejected. Tradeable value uses acquisition cost, then the latest local reference price when acquisition cost is unavailable.

The Agent resolves named players through `FC27:club_query` and passes concrete `item_id` values. This keeps duplicate owned copies unambiguous. A required item remains subject to every normal safety and objective rule. Required/excluded overlap, required items outside an explicit candidate pool, protected or loan required items, missing current items, and mandatory value above the hard limit fail before model construction.

The optimizer models the normalized quality, quality-count, team-rating, nation, league, club, specific-nation, specific-league, specific-club, same-attribute, chemistry, squad-size, mandatory-item, and tradeable-value constraints. Chemistry uses the FC squad thresholds for clubs (2/4/7), nations (2/5/8), and leagues (3/5/8), with no manager contribution in an SBC squad. It minimizes, in order, tradeable-card use when requested, tradeable opportunity cost, rating consumption, and deterministic item identity. Accepted item sets are excluded and the model is resolved to produce additional candidates under one bounded deadline.

Each result reports `optimal`, `feasible`, `infeasible`, or `unknown`. Every feasible model result passes the separate local validator before persistence. Unsupported EA requirements remain blocking; supplying a mandatory item never converts an unknown requirement into verified evidence.

## Save action

`save_sbc_squad` requires `set_id`, `challenge_id`, `solution_id`, and the persisted solution's ordered `item_ids`. The solution contains one EA field `slot_index` for every item. The page adapter reloads the challenge, confirms the same fillable slot layout, resolves the exact owned objects, rejects concept or missing items, and verifies placement before sending the save. After EA acknowledges the save, the dispatcher issues a separate read-only request that reloads the set list, challenge list, and saved challenge squad through EA services.

The fresh readback contains only the set/challenge identity, current status, formation, rating, chemistry, occupied field-slot indices, and exact item order. Eligibility is evaluated through the reloaded challenge's own `isRequirementMet` results, bound to the active controller's matching set/challenge identity, plus a visible and enabled Submit control within that controller's root view.

The daemon does not expose a generic browser-method RPC. Browser write methods are reachable only through audited action dispatch. A new save is rejected before EA contact when the solution is no longer `validated` or when the same solution already has a pending, running, completed, timed-out, or readback-pending save action. Submission additionally requires a matching completed save action inside a completed batch, with fresh trusted evidence for the same sync, set, challenge, and exact item order.

All save-call timeout sources (`BRIDGE_TIMEOUT`, `PAGE_BRIDGE_TIMEOUT`, and `EA_SERVICE_TIMEOUT`) are normalized to `SBC_SAVE_OUTCOME_UNKNOWN`. That state permits only fresh read-only reconciliation. Public full synchronization and account execution share the same lock, and submit dispatch revalidates the batch's original `expected_sync_id` immediately before contacting EA.

`submit_sbc` remains disabled in the active policy while Issue #23 completes live acceptance. The implementation includes outcome-unknown handling, duplicate-submit rejection, and read-only challenge/inventory reconciliation. Live submission still requires separate approval for permanent item consumption.

If the browser bridge times out after EA has already accepted the save, the action is recorded as failed and is not retried. Reconciliation accepts only the original `action_id`; the daemon derives the target from the failed audit row and performs its own fresh EA read. It requires the original batch sync to remain current, the solution to remain `validated`, and the set, challenge, persisted item order, fillable slot indices, freshness markers, and positive eligibility evidence to match. The action does not submit.

When EA acknowledges the save but the separate fresh read fails, the action records `SBC_SAVE_READBACK_PENDING` together with the save acknowledgement. The same `action_id` can run only the fresh read reconciliation path; it never calls `saveSbcSquad` again.

A completed saved action can be freshly verified by `action_id`. The daemon performs the same independent EA read and replaces the solution's canonical execution evidence with `ea_webapp_fresh`. Submission requires this source, all three freshness markers, challenge-native positive requirements, matching controller identity, and an available Submit control.

## Submit action

`submit_sbc` accepts only a solution whose status is `saved`, whose canonical save evidence matches the current complete sync, and whose completed save audit matches the same set, challenge, ordered item IDs, and fillable slot indices. Before the write call, the dispatcher performs another fresh saved-squad read and persists it as the action's pre-submit checkpoint. The checkpoint contains exact item order and slot layout, eligibility evidence, repeatability, completion counters, rewards, and set/challenge identity.

`BRIDGE_TIMEOUT`, `PAGE_BRIDGE_TIMEOUT`, and `EA_SERVICE_TIMEOUT` during the write call become `SBC_SUBMIT_OUTCOME_UNKNOWN`. A successful EA response followed by incomplete inventory or challenge readback becomes `SBC_SUBMIT_READBACK_PENDING`. Both states keep the solution `saved`, block every new submit action for that solution, and permit only reconciliation through the original `action_id`.

After an acknowledged submit, a complete synchronization must advance beyond the original `expected_sync_id`. Every confirmed item must be absent from current inventory and have a `removed` event in a complete post-attempt synchronization. A fresh matching set/challenge read must then prove completion progress. Only after all evidence passes does the solution transition atomically from `saved` to `submitted`.

### Repeatable outcome classification

For repeatable SBCs, status reset and `completed=false` are expected after submission and do not prove failure. Every lifetime `timesCompleted` counter present on either side of the checkpoint must have a valid before/after pair, and all available deltas must agree. `challengesCompletedCount` describes progress in the current repeatable cycle and may reset or remain zero after a successful completion, so it is recorded but does not classify the submission outcome:

| Outcome | Completion counter | Inventory evidence | Saved squad evidence |
| --- | --- | --- | --- |
| success | exactly `before + 1` | all confirmed items absent; complete removal history | not required |
| confirmed not applied | unchanged | all confirmed items present after a newer complete sync | same ordered IDs and slots, positive fresh eligibility |
| still unknown | missing, conflicting, or any other delta | partial/contradictory evidence | absent or contradictory |

`timesCompleted` increasing by more than one remains unknown because the action cannot be uniquely attributed. Static reward definitions do not prove reward delivery.

Immediately before the page invokes the EA submit command, it rechecks the exact ordered item IDs, challenge-native eligibility, current controller identity, visible enabled Submit control, and every non-null baseline counter. A changed or missing value aborts the write.

### Submit reconciliation

The internal daemon RPC `reconcile_sbc_submit` accepts only `action_id`. It derives set, challenge, solution, item IDs, fillable slots, original sync, and checkpoint from the audit database; callers cannot provide evidence. Under the execution lock it performs a newer complete full sync, reads fresh submission state, and reads the saved squad only when all confirmed items remain.

Reconciliation can produce:

- `complete`: exactly one completion is proven; the action and batch complete and the solution becomes `submitted`;
- `SBC_SUBMIT_CONFIRMED_NOT_APPLIED`: completion is unchanged and the exact saved squad remains eligible; the solution remains `saved`, its save evidence is refreshed to the new sync, and a new confirmed submit may be created;
- `SBC_SUBMIT_STILL_UNKNOWN`: evidence is incomplete or contradictory; the solution remains `saved` and new submits remain blocked.

Repeated reconciliation of a completed or confirmed-not-applied action returns the stored result without another EA read or submission.
