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
| 27 | count of players at exactly one OVR threshold |
| 28 | count of players at or below one OVR threshold |
| 7 | distinct nation count |
| 8 | distinct league count |
| 9 | distinct club count |
| 10 | count of players from one specific nation |
| 11 | count of players from one specific league |
| 12 | count of players from one of a specific set of clubs |
| 4 | largest number of players sharing one nation/region, compared through scope |
| 5 | largest number of players sharing one league, compared through scope |
| 6 | largest number of players sharing one club, compared through scope |
| 35 | minimum/maximum/exact squad chemistry |
| 36 | minimum/maximum/exact chemistry points required for every player |

These numeric identities and scope values are decoded from the current FC27 Web App `SBCEligibilityKey` and `SBCEligibilityScope` enums. Known Web App keys that are not implemented are reported by enum name; unknown keys, malformed tuples, unknown values, missing or contradictory player counts, and partial squads without exact fillable field-slot evidence are persisted in `unsupported_constraints`. The solver refuses those challenges with `SBC_SCHEMA_UNSUPPORTED`.

## Candidate objective

The objective may contain:

- `candidate_item_ids`: restrict solving to these current owned item IDs;
- `required_item_ids`: require these exact current owned item IDs in every candidate;
- `exclude_item_ids`: remove explicit items;
- `max_tradeable_value`: reject a squad above the limit;
- `max_item_overall`: preserve higher-rated items;
- `max_solutions`: return at most ten candidates through the tool parameter.

Protected items, loan items, Tradepile items, stale/missing items, duplicate owned item IDs, duplicate `base_player_ea_id` values, special cards, Evolution cards, every player in the current active squad, and cards absent from the catalog are excluded or rejected. The active squad is read fresh before solving and before save or submission. Tradeable value uses acquisition cost, then the latest local reference price when acquisition cost is unavailable.

The Agent resolves named players through `FC27:club_query` and passes concrete `item_id` values. This keeps duplicate owned copies unambiguous. A required item remains subject to every normal safety and objective rule. Required/excluded overlap, required items outside an explicit candidate pool, protected or loan required items, missing current items, and mandatory value above the hard limit fail before model construction.

Owned-only optimization uses one 180-second shared deadline. A feasible incumbent returned at the deadline remains independently validated and reports `FEASIBLE_UNPROVEN`; only a completed proof reports `OPTIMAL_PROVEN`.

When one SBC set contains multiple unfinished challenges, the Agent plans them sequentially. Every later `sbc_solve` call places all `item_id` values selected for earlier unsubmitted challenges in `objective.exclude_item_ids`. This produces disjoint saved squads. After any challenge is submitted, the Agent performs a complete club synchronization and re-solves every remaining challenge against the new inventory before saving it again.

The optimizer models the normalized quality, quality-count, team-rating, nation, league, club, specific-nation, specific-league, specific-club, same-attribute, chemistry, squad-size, mandatory-item, and tradeable-value constraints. Exact fillable slot indices and position metadata come from the loaded EA challenge squad. Formation names are descriptive and do not define write order.

For ordinary cards, only a player assigned to a preferred or alternate position earns chemistry and contributes to club, league, and nation thresholds. Club thresholds are 2/4/7, league thresholds are 3/5/8, and nation thresholds are 2/5/8. Individual chemistry is capped at three and team chemistry is the sum of field-slot chemistry. SBC squads have no manager contribution. Special, Icon, Hero, Hall of FUT, and Evolution chemistry modifiers stay outside this standard-card solver because those cards are excluded from automatic candidates.

When no chemistry or explicit position requirement exists, the optimizer does not restrict item-to-slot assignment. When chemistry is required, it uses complete Hall capacity constraints to prove that every selected player can be assigned to one real slot with exactly the solved in-position or out-of-position state. It then recovers one deterministic concrete matching and independently recomputes chemistry. Each planning level fixes the exact purchase count, then minimizes the complete descending rating vector, owned tradeable count, owned opportunity cost, purchase cost, and deterministic item identity.

Owned-only results report `OPTIMAL_PROVEN`, `FEASIBLE_UNPROVEN`, `INFEASIBLE_PROVEN`, or `UNKNOWN_NO_SOLUTION_FOUND`. Market-backed plans report `OPTIMAL_WITHIN_REALIZATION_SET` or `VALIDATED_FEASIBLE`, and their proof names the exact residual realization branch. Every feasible model result passes the separate local validator before persistence. Unsupported EA requirements remain blocking; supplying a mandatory item never converts an unknown requirement into verified evidence.

## Planning domains

`purchase_budget` defaults to zero. A zero-budget request solves only current eligible owned items and does not request FUT.GG prices. A positive request computes every exact purchase level from zero through the requested budget so the Agent can compare the cost of each additional market card. The purchase budget may not exceed the captured challenge player count.

Each positive level starts from at most three representative plans from the preceding validated frontier. Market cards already selected by that branch remain mandatory. The planner groups the complete priced catalog into residual role columns using explicit challenge identities, branch club/league/nation counts, slot-position coverage, rating, quality, and price. It validates every one-card replacement of an eligible owned slot and retains the nondominated local frontier over rating vector, purchase cost, owned opportunity cost, owned tradeable count, and chemistry margin. A complete neighborhood returns `LOCAL_OPTIMUM`; CP-SAT runs on the reduced realization branch only when the local neighborhood has no valid plan.

For positive budgets, the price adapter refreshes the current FUT.GG snapshot for every catalog candidate and persists observed prices. A market card is eligible only when the account platform has a positive `market_or_normal` price and is not extinct. Missing and non-market prices remain unavailable. Exact-feature groups retain the cheapest option for each base player and then at most the squad-size number of distinct bases; the full base-player uniqueness constraint remains in every realization branch.

Only all-owned plans receive a persisted `solution_id` and `executable=true`. Hybrid and market plans return exact `card_ea_id` purchase targets, FUT.GG estimates and observation times, `market_verification_required=true`, and `executable=false`. After a user chooses a hybrid plan, the Agent verifies each card through `market_search`; purchase, synchronization, item binding, save, and submission remain separate confirmed operations.

## Save action

`save_sbc_squad` requires `set_id`, `challenge_id`, `solution_id`, and the persisted solution's ordered `item_ids`. The solution contains one EA field `slot_index` for every item. The page adapter reloads the challenge, confirms the same fillable slot layout, resolves the exact owned objects, rejects concept or missing items, and verifies placement before sending the save. After EA acknowledges the save, the dispatcher issues a separate read-only request that reloads the set list, challenge list, and saved challenge squad through EA services.

The fresh save readback contains the set/challenge identity, current status, formation, rating, chemistry, occupied field-slot indices, and exact item order. Save acceptance requires matching set/challenge identity, exact items and slots, and positive results from every reloaded challenge `isRequirementMet` check. Current-page identity and a visible enabled Submit control are separate submission evidence and remain required by guarded verification before any submit action.

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
