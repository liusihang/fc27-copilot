# SBC capture, solving, and execution contract

Date: 2026-09-18

## Agent and tool boundary

`FC27:sbc_query` captures and persists EA facts. `FC27:sbc_solve` applies a caller-provided objective and returns exact owned item IDs with deterministic validation evidence. The Agent chooses the challenge and objective. Saving and submission occur only through exact `FC27:execute_actions` batches.

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
| 3 | exact squad quality; values 1/2/3 are bronze/silver/gold |
| 17 | minimum/maximum/exact player count for quality 1/2/3 |
| 19 | team rating |
| 7 | distinct nation count |
| 8 | distinct league count |
| 4 | maximum players from one league |
| 5 | maximum players from one nation |
| 6 | maximum players from one club |
| 35 | chemistry; preserved as unsupported until EA eligibility readback is available |

Unknown keys, malformed tuples, unknown values, and unmapped formations are persisted in `unsupported_constraints`. The solver refuses those challenges with `SBC_SCHEMA_UNSUPPORTED`.

## Candidate objective

The objective may contain:

- `candidate_item_ids`: restrict solving to these current owned item IDs;
- `exclude_item_ids`: remove explicit items;
- `prefer_untradeable`: sort untradeable items first;
- `max_tradeable_value`: reject a squad above the limit;
- `max_item_overall`: preserve higher-rated items;
- `max_solutions`: return at most ten candidates through the tool parameter.

Protected items, loan items, Tradepile items, stale/missing items, duplicates, and cards absent from the catalog are excluded or rejected. Tradeable value uses acquisition cost, then the latest local reference price when acquisition cost is unavailable.

## Save action

`save_sbc_squad` requires `set_id`, `challenge_id`, `solution_id`, and exactly eleven ordered `item_ids`. The action must match the persisted solution and latest complete club synchronization. The page adapter loads the challenge, resolves the exact owned objects, rejects concept or missing items, saves the squad, reloads it through `sbcDAO`, and requires the same item order in readback. It does not submit.

## Submit action

`submit_sbc` accepts only a solution whose status is `saved`. It reloads the saved EA squad and requires the exact confirmed item order before calling the Web App submission service. A complete post-submit inventory synchronization must prove every consumed item is absent. Challenge/reward state and consumed item IDs are then persisted in the action and solution evidence.
