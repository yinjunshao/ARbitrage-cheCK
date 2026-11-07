"""Human approval.

The engine never sends a paper order (or a basket) without asking an
``approver``. An approver is any function ``ApprovalRequest -> Approval``.

* :func:`auto_approve` is for backtests. The audit log records that it, not a
  person, approved each order, so automated results are never mistaken for
  human-reviewed ones.
* :func:`cli_approver` asks a person in the terminal and records their name.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

AUTO = "auto_approve (backtest)"


@dataclass(frozen=True)
class ApprovalRequest:
    kind: str  # "order" | "basket"
    summary: str  # one plain-English line for the person approving
    details: dict = field(default_factory=dict)
    sim_ts: float = 0.0


@dataclass(frozen=True)
class Approval:
    approved: bool
    by: str
    note: str = ""


Approver = Callable[[ApprovalRequest], Approval]


def auto_approve(request: ApprovalRequest) -> Approval:
    return Approval(True, AUTO)


def deny_all(request: ApprovalRequest) -> Approval:
    return Approval(False, "deny_all", "every order refused")


def cli_approver(name: str, input_fn: Callable[[str], str] = input, print_fn=print) -> Approver:
    """Ask `name` in the terminal before each order or basket. Anything but 'y' is a no."""
    if not name.strip():
        raise ValueError("cli_approver needs the approving person's name")

    def approve(request: ApprovalRequest) -> Approval:
        print_fn(f"\n[PAPER] approval needed ({request.kind}): {request.summary}")
        for k, v in request.details.items():
            print_fn(f"    {k}: {v}")
        answer = input_fn(f"{name}, approve? [y/N] ").strip().lower()
        return Approval(answer in ("y", "yes"), name, "" if answer else "no answer")

    return approve
