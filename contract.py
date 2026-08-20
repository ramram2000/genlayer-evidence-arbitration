# { "Depends": "py-genlayer:1jb45aa8ynh2a9c9xn3b7qqh8sm5q93hwfp7jqmwsfhh8jpz09h6" }
"""
EvidenceArbitration — an Intelligent Contract primitive for resolving
disputes based on written evidence, judged against an explicit criteria
statement agreed by both parties up front.

WHY THIS IS A PRIMITIVE, NOT A DEMO
------------------------------------
Two-party disagreements ("did the freelancer deliver what was agreed?",
"does this submission satisfy the bounty requirements?", "should this
appeal be upheld?") are everywhere, but they are inherently subjective —
you cannot resolve them with exact string matching or a numeric formula.

This contract gives builders a reusable, auditable state machine for
that exact situation:

  1. An initiator (e.g. a client / requester) locks in a CRITERIA
     statement that both parties can see and cannot be changed later.
  2. A respondent (e.g. a contractor / submitter) submits EVIDENCE —
     free text describing what was delivered.
  3. Either party can trigger resolution. The leader validator asks an
     LLM to judge the evidence against the criteria; every other
     validator independently re-runs the same judgment on the same
     input via `gl.eq_principle.prompt_non_comparative` and only
     agrees if the leader's verdict is one a reasonable, independent
     judge would also reach. This is what makes the outcome trustworthy
     consensus rather than one model's unverified opinion.
  4. The losing side gets exactly one APPEAL, which requires new
     evidence (you cannot re-litigate the same text) and produces a
     fresh, independent verdict.
  5. Every state transition is recorded in an on-chain history log,
     so the contract is auditable after the fact — useful for
     downstream escrow/payment contracts that only need to read
     `get_status()` / `get_verdict()`.

This contract intentionally holds no funds itself — it is a decision
primitive. A payment/escrow contract can be built on top of it by
reading `resolved` and `verdict_favors_respondent` and moving funds
accordingly. Keeping those concerns separate is what makes this
reusable across many use cases (freelance escrow, bounty review,
content-moderation appeals, SLA disputes) instead of being a one-off
demo tied to a single scenario.
"""

from genlayer import *
import typing
import json


class Status:
    CREATED = "created"           # criteria locked, waiting for evidence
    EVIDENCE_SUBMITTED = "evidence_submitted"
    RESOLVED = "resolved"
    APPEALED = "appealed"
    FINAL = "final"                # resolved after appeal, no further action


class EvidenceArbitration(gl.Contract):
    initiator: str
    respondent: str
    criteria: str

    status: str
    evidence: str
    appeal_evidence: str

    resolved: bool
    verdict_favors_respondent: bool
    verdict_reasoning: str

    appeal_used: bool
    final_verdict_favors_respondent: bool

    history: DynArray[str]

    def __init__(self, respondent: str, criteria: str):
        """
        respondent: address of the party who must submit evidence
        criteria:   plain-language description of what counts as success.
                    Locked at deployment so neither party can move the
                    goalposts after evidence is submitted.
        """
        self.initiator = gl.message.sender_address.as_hex
        self.respondent = respondent
        self.criteria = criteria

        self.status = Status.CREATED
        self.evidence = ""
        self.appeal_evidence = ""

        self.resolved = False
        self.verdict_favors_respondent = False
        self.verdict_reasoning = ""

        self.appeal_used = False
        self.final_verdict_favors_respondent = False

        self._log(f"created: criteria locked by {self.initiator}")

    def _log(self, entry: str) -> None:
        self.history.append(entry)

    def _require_respondent(self) -> None:
        sender = gl.message.sender_address.as_hex
        if sender != self.respondent:
            raise Exception("only the respondent may perform this action")

    def _require_party(self) -> None:
        sender = gl.message.sender_address.as_hex
        if sender not in (self.initiator, self.respondent):
            raise Exception("only initiator or respondent may perform this action")

    @gl.public.write
    def submit_evidence(self, evidence_text: str) -> None:
        """Respondent submits free-text evidence of what was delivered."""
        self._require_respondent()
        if self.status != Status.CREATED:
            raise Exception("evidence already submitted for this case")
        if len(evidence_text.strip()) == 0:
            raise Exception("evidence cannot be empty")

        self.evidence = evidence_text
        self.status = Status.EVIDENCE_SUBMITTED
        self._log("evidence submitted")

    @gl.public.write
    def resolve(self) -> typing.Any:
        """
        Either party can trigger resolution once evidence is in.
        Validators independently judge evidence-vs-criteria and reach
        consensus via the non-comparative Equivalence Principle.
        """
        self._require_party()
        if self.status != Status.EVIDENCE_SUBMITTED:
            raise Exception("nothing to resolve: submit evidence first")

        criteria = self.criteria
        evidence = self.evidence

        def get_input() -> str:
            return json.dumps({"criteria": criteria, "evidence": evidence})

        raw = gl.eq_principle.prompt_non_comparative(
            get_input,
            task=(
                "You are an impartial arbitrator. Given a JSON object with "
                "'criteria' (what was agreed to constitute success) and "
                "'evidence' (what the respondent claims was delivered), "
                "decide whether the evidence satisfies the criteria. "
                "Respond with ONLY a JSON object: "
                '{"favors_respondent": true|false, "reasoning": "<one sentence>"}'
            ),
            criteria="""
                The response must be valid JSON with exactly the keys
                favors_respondent (boolean) and reasoning (a single
                concise sentence, under 200 characters, that references
                specific details from the evidence and criteria rather
                than a generic statement). The verdict must be a
                defensible reading of whether the evidence, taken at
                face value, meets the stated criteria.
            """,
        )

        parsed = json.loads(raw.replace("```json", "").replace("```", "").strip())

        self.verdict_favors_respondent = bool(parsed["favors_respondent"])
        self.verdict_reasoning = str(parsed["reasoning"])[:200]
        self.resolved = True
        self.status = Status.RESOLVED
        self.final_verdict_favors_respondent = self.verdict_favors_respondent
        self._log(
            f"resolved: favors_respondent={self.verdict_favors_respondent} "
            f"reasoning={self.verdict_reasoning}"
        )
        return parsed

    @gl.public.write
    def appeal(self, new_evidence_text: str) -> None:
        """
        The losing party may appeal exactly once, and must provide
        NEW evidence (additional context, clarification, proof) —
        this contract does not allow simply re-asking the same
        question until a favorable answer appears.
        """
        self._require_party()
        if self.status != Status.RESOLVED:
            raise Exception("can only appeal a resolved case")
        if self.appeal_used:
            raise Exception("appeal already used")
        if new_evidence_text.strip() == self.evidence.strip():
            raise Exception("appeal must include new evidence, not a resubmission")
        if len(new_evidence_text.strip()) == 0:
            raise Exception("appeal evidence cannot be empty")

        sender = gl.message.sender_address.as_hex
        losing_party_is_respondent = not self.verdict_favors_respondent
        sender_is_losing_party = (
            (sender == self.respondent) == losing_party_is_respondent
        )
        if not sender_is_losing_party:
            raise Exception("only the losing party may appeal")

        self.appeal_evidence = new_evidence_text
        self.appeal_used = True
        self.status = Status.APPEALED
        self._log("appeal filed with new evidence")

    @gl.public.write
    def resolve_appeal(self) -> typing.Any:
        """Re-run judgment on criteria + combined original/appeal evidence."""
        self._require_party()
        if self.status != Status.APPEALED:
            raise Exception("no pending appeal")

        criteria = self.criteria
        combined_evidence = (
            f"Original evidence: {self.evidence}\n\n"
            f"Additional evidence submitted on appeal: {self.appeal_evidence}"
        )

        def get_input() -> str:
            return json.dumps({"criteria": criteria, "evidence": combined_evidence})

        raw = gl.eq_principle.prompt_non_comparative(
            get_input,
            task=(
                "You are an impartial appellate arbitrator reviewing a "
                "prior decision with additional evidence now included. "
                "Decide whether the full evidence satisfies the criteria. "
                "Respond with ONLY a JSON object: "
                '{"favors_respondent": true|false, "reasoning": "<one sentence>"}'
            ),
            criteria="""
                The response must be valid JSON with exactly the keys
                favors_respondent (boolean) and reasoning (a single
                concise sentence, under 200 characters, that explicitly
                references what the new evidence added). The verdict
                must be a defensible reading of the combined evidence
                against the stated criteria.
            """,
        )

        parsed = json.loads(raw.replace("```json", "").replace("```", "").strip())

        self.final_verdict_favors_respondent = bool(parsed["favors_respondent"])
        self.status = Status.FINAL
        self._log(
            f"appeal resolved: final_favors_respondent="
            f"{self.final_verdict_favors_respondent} reasoning={parsed['reasoning']}"
        )
        return parsed

    @gl.public.view
    def get_status(self) -> str:
        return self.status

    @gl.public.view
    def get_criteria(self) -> str:
        return self.criteria

    @gl.public.view
    def get_evidence(self) -> str:
        return self.evidence

    @gl.public.view
    def get_verdict(self) -> dict:
        return {
            "resolved": self.resolved,
            "favors_respondent": self.verdict_favors_respondent,
            "reasoning": self.verdict_reasoning,
            "appeal_used": self.appeal_used,
            "final_favors_respondent": self.final_verdict_favors_respondent,
        }

    @gl.public.view
    def get_history(self) -> list:
        return list(self.history)
