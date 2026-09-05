# Generation module map

This extraction separates deterministic rules from orchestration while keeping
the existing service constructor, public API, task lifecycle and transaction
boundaries. It does not change quota rules, quality thresholds, retry budgets,
publishing rules or the model-routing architecture.

## Game service

| Module under `packages/game-service/src/game/` | Responsibility |
| --- | --- |
| `game-runtime.policy.ts` | Orientation, runtime contract normalization and profile hints |
| `game-generation-payload.ts` | Intent and iteration payloads, bundle history and metadata |
| `game-quality.policy.ts` | Existing quality thresholds and result checks |
| `game-failure.policy.ts` | Error normalization, failure classification and terminal states |
| `game-access.policy.ts` | Visibility, publishability, entitlement and quota calculations |
| `game-service.types.ts` | Shared value contracts |
| `generation-retry.ts` | Transient request retries and task-abort/supersession errors |

`game.service.ts` delegates policy calculations through its existing private
method signatures. Database operations, locks, timers, quota transactions and
task execution remain in the service. It is still large: extracting those
stateful concerns requires a separate design and integration-test pass.

Policy modules do not import `GameService`, own database connections or register
background work. Their dependencies form a directed acyclic graph.

## AI engine

| Module under `packages/ai-engine/src/engine/` | Responsibility |
| --- | --- |
| `pipeline_v2_runner.py` | Create/iterate coordination and external execution seams |
| `pipeline_v2_quality_policy.py` | Quality decisions and targeted repair behavior |
| `pipeline_v2_specification.py` | Spec merging, profile ranking and runtime contract composition |
| `pipeline_v2_validation.py` | Contract checks, QA reports and artifact formatting |
| `pipeline_v2_support.py` | Shared constants and value types |
| `code_generator.py` | Model calls, generation attempts and iteration execution |
| `code_generation_prompts.py` | Prompt construction and layout/design formatting |
| `code_generation_support.py` | Prompt constants and standalone text helpers |

The concern mixins have no constructors and own no independent runtime state.
`V2PipelineRunner` and `CodeGenerator` retain their constructor and method APIs.
Prompt lookup and runtime-QA seams used by existing tests stay on their original
modules. Composition here is a mechanical extraction, not a new pipeline.

## Verification

Local verification before submission:

- All 66 extracted TypeScript function bodies match the original after removing
  the method receiver and formatting; 47 behavior comparisons passed using the
  same dependency mocks for both versions.
- The service and policy modules link and execute with those dependency mocks.
- All 170 Python methods retain identical ASTs, including signatures and
  decorators. Extracted Python modules compile and have no unresolved global
  references in the static symbol check.

These checks do not establish full TypeScript type safety or replace Jest/pytest.
The `Generation refactor regression` PR workflow separately builds the game
service and runs the relevant existing game/session and AI pipeline/prompt tests.
It has read-only repository permissions and no deployment steps.
