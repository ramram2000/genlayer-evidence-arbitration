# EvidenceArbitration

A reusable GenLayer Intelligent Contract primitive for resolving
**subjective, two-party disputes** — "did the work meet the agreed
criteria?" — using GenLayer's consensus over non-deterministic (LLM)
judgment, instead of a single unverifiable off-chain opinion.

## The problem it solves

Lots of real interactions boil down to: two parties agree on a
criteria ahead of time, one side later claims they met it, and there's
no cheap, trustworthy way to check that claim on-chain, because "did
this satisfy that" is a judgment call, not something you can check
with `==`. Examples this primitive is meant to be reused for:

- Freelance/bounty escrow ("does the delivered work meet the brief?")
- Content moderation appeals ("does this post actually violate the rule
  it was flagged for?")
- SLA / service disputes ("was the response time / quality clause met?")

This contract does **not** move funds itself — it's a decision
primitive. A separate escrow contract can read `get_verdict()` and
`get_status()` and release funds accordingly. Keeping the arbitration
logic separate from the payment logic is what makes this composable
across use cases instead of being a single-purpose demo.

## How consensus is used

The core judgment call — "does this evidence satisfy this criteria?"
— is made with `gl.eq_principle.prompt_non_comparative`:

1. The **leader** validator sends the criteria + evidence to an LLM and
   gets back a verdict (`favors_respondent`) and a one-sentence
   `reasoning`.
2. Every **other validator** independently re-evaluates the *same*
   input against an explicit `criteria` string (in the Equivalence
   Principle sense — the rules for what a valid, well-formed verdict
   looks like) and only agrees with the leader if the leader's verdict
   is one a reasonable, independent judge would also reach given the
   same evidence.
3. Because the judgment is inherently subjective, `strict_eq` (exact
   match) is intentionally **not** used — it would fail whenever two
   validators' LLMs phrase the same conclusion differently. Instead,
   the contract enforces structure (valid JSON, specific reasoning,
   no generic filler) while leaving room for validators to agree on
   substance rather than exact wording.

This is the same design pattern used by GenLayer's own
`LlmHelloWorldNonComparative` example, applied to a real decision
rather than a greeting.

## State machine

```
created --submit_evidence()--> evidence_submitted --resolve()--> resolved
                                                                      |
                                                                appeal()
                                                                      v
                                                                  appealed --resolve_appeal()--> final
```

- `criteria` is locked at deployment (constructor argument) so neither
  party can change what "success" means after evidence is in.
- Only the `respondent` can submit evidence; only `initiator` or
  `respondent` can trigger resolution or appeals (see `_require_*`
  guards in `contract.py`).
- Exactly **one appeal** is allowed, and it must include genuinely new
  evidence — the contract rejects an appeal that resubmits the exact
  same evidence text, so it can't be used to just re-roll the LLM
  until a favorable answer comes up.
- Every transition is appended to an on-chain `history` log
  (`get_history()`), so the whole case is auditable after the fact.

## Contract interface

| Method | Type | Description |
|---|---|---|
| `__init__(respondent, criteria)` | constructor | Locks in the criteria and who must respond |
| `submit_evidence(evidence_text)` | write (respondent only) | Submits free-text evidence |
| `resolve()` | write (either party) | Triggers the consensus judgment |
| `appeal(new_evidence_text)` | write (losing party only) | Files one appeal with new evidence |
| `resolve_appeal()` | write (either party) | Re-judges with combined evidence |
| `get_status()` | view | Current state machine status |
| `get_criteria()` / `get_evidence()` | view | Read back the locked inputs |
| `get_verdict()` | view | Full verdict dict (resolved, favor, reasoning, appeal state) |
| `get_history()` | view | Full audit log of state transitions |

## Testing

`test_evidence_arbitration.py` uses the GenLayer Testing Suite
(`gltest`) and covers:

- the full happy-path resolution (evidence clearly meets criteria)
- resolution against the respondent (evidence clearly fails criteria)
- the appeal flow, including the guard against resubmitting identical
  evidence
- access control (only the respondent may submit evidence)
- that the history log correctly records every transition

Run with:

```bash
pip install genlayer-test
gltest test_evidence_arbitration.py
```

(requires a running GenLayer Studio / local node, per GenLayer's
[testing docs](https://docs.genlayer.com/developers/decentralized-applications/testing)).

## Notes / limitations

- This contract deliberately does not handle payments — it's meant to
  be composed with an escrow contract, not to be one.
- The appeal mechanism allows exactly one round; extending it to
  N rounds or adding a stake/bond to discourage frivolous appeals
  would be a natural next iteration.
