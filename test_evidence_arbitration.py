"""
Direct/integration tests for EvidenceArbitration, written against the
GenLayer Testing Suite (gltest). Run with `gltest` while GenLayer
Studio / your local node is running.

These tests exercise the full state machine, not just the happy path:
  - normal resolution in the respondent's favor
  - normal resolution against the respondent
  - the appeal flow (including the "no resubmission" guard)
  - access control (only respondent may submit evidence, only
    initiator/respondent may resolve)
"""

from pathlib import Path
import pytest
from gltest import get_contract_factory, get_accounts
from gltest.assertions import tx_execution_succeeded

CONTRACTS_DIR = Path(__file__).parent


def deploy(initiator_account, respondent_address, criteria):
    factory = get_contract_factory(
        contract_file_path=CONTRACTS_DIR / "contract.py"
    )
    return factory.deploy(
        args=[respondent_address, criteria],
        account=initiator_account,
    )


def test_full_success_flow():
    accounts = get_accounts()
    initiator, respondent = accounts[0], accounts[1]

    contract = deploy(
        initiator,
        respondent.address,
        criteria="The delivered logo must be a vector file (SVG) with a "
        "transparent background, in at least two color variants.",
    )

    assert contract.get_status(args=[]).call() == "created"

    tx = contract.submit_evidence(
        args=[
            "Delivered logo.svg, vector format, transparent PNG/SVG "
            "background, plus a dark-mode variant and a light-mode "
            "variant as agreed."
        ],
        account=respondent,
    ).transact()
    assert tx_execution_succeeded(tx)
    assert contract.get_status(args=[]).call() == "evidence_submitted"

    resolve_tx = contract.resolve(args=[], account=initiator).transact()
    assert tx_execution_succeeded(resolve_tx)

    verdict = contract.get_verdict(args=[]).call()
    assert verdict["resolved"] is True
    # Evidence clearly satisfies criteria -> expect it to favor respondent.
    assert verdict["favors_respondent"] is True
    assert len(verdict["reasoning"]) > 0


def test_evidence_not_meeting_criteria():
    accounts = get_accounts()
    initiator, respondent = accounts[0], accounts[1]

    contract = deploy(
        initiator,
        respondent.address,
        criteria="The article must be at least 1500 words and include "
        "three cited academic sources.",
    )

    contract.submit_evidence(
        args=["I wrote a 200-word summary with no sources."],
        account=respondent,
    ).transact()

    contract.resolve(args=[], account=initiator).transact()
    verdict = contract.get_verdict(args=[]).call()
    assert verdict["favors_respondent"] is False


def test_appeal_requires_new_evidence():
    accounts = get_accounts()
    initiator, respondent = accounts[0], accounts[1]

    contract = deploy(
        initiator,
        respondent.address,
        criteria="Must include unit tests covering the main function.",
    )

    contract.submit_evidence(
        args=["Here is the code, no tests included."], account=respondent
    ).transact()
    contract.resolve(args=[], account=initiator).transact()

    verdict = contract.get_verdict(args=[]).call()
    assert verdict["favors_respondent"] is False

    # Losing party (respondent) tries to appeal with the SAME text -> rejected
    with pytest.raises(Exception):
        contract.appeal(
            args=["Here is the code, no tests included."], account=respondent
        ).transact()

    # Appeal with genuinely new evidence succeeds
    tx = contract.appeal(
        args=[
            "Update: I also added test_main.py covering the main "
            "function's three branches, see attached diff."
        ],
        account=respondent,
    ).transact()
    assert tx_execution_succeeded(tx)
    assert contract.get_status(args=[]).call() == "appealed"

    contract.resolve_appeal(args=[], account=initiator).transact()
    assert contract.get_status(args=[]).call() == "final"


def test_only_respondent_can_submit_evidence():
    accounts = get_accounts()
    initiator, respondent, stranger = accounts[0], accounts[1], accounts[2]

    contract = deploy(initiator, respondent.address, criteria="Anything.")

    with pytest.raises(Exception):
        contract.submit_evidence(
            args=["I am not the respondent."], account=stranger
        ).transact()


def test_history_log_records_transitions():
    accounts = get_accounts()
    initiator, respondent = accounts[0], accounts[1]

    contract = deploy(initiator, respondent.address, criteria="Anything.")
    contract.submit_evidence(args=["done"], account=respondent).transact()
    contract.resolve(args=[], account=initiator).transact()

    history = contract.get_history(args=[]).call()
    assert any("created" in h for h in history)
    assert any("evidence submitted" in h for h in history)
    assert any("resolved" in h for h in history)
