"""The ONLY file that imports the numeric core. Everything else talks to `CoreAdapter`.

STATUS (2026-11-07): the core is not in this repo or on origin yet, so nothing here has been
run against Debby's code. `RealCoreAdapter` is written against the API sketched in note 12
(`fac_core.evaluate(rel, size) -> Opportunity`) and the file list in note 13 (venues.py,
relations.py, engine.py, guard.py, finder.py). Those two sketches do not agree with each other;
whichever is real, only this file should change. See docs/CORE_MAP.md and docs/CORE_REQUESTS.md.
"""
from __future__ import annotations

import importlib
from typing import Protocol

from .schemas import CoreResult, GuardCheck, Market, Relation, VerifiedRuleDiff


class CoreNotAvailable(RuntimeError):
    pass


class CoreAdapter(Protocol):
    name: str

    def price(self, relation: Relation, size: float) -> CoreResult: ...
    def list_open_markets(self, venues: list[str]) -> list[Market]: ...
    def parse_link(self, url: str) -> Market | None: ...
    def compare_rules(self, a: Market, b: Market) -> VerifiedRuleDiff | None:
        """Core's own --llm rule extractor, if reusable. None = frame uses its own rulediff."""
        ...


def tighten(frame_verdict_hint: str | None, core: CoreResult) -> CoreResult:
    """The frame may only escalate caution, never loosen. Used if the frame adds a flag."""
    order = ["worth_a_look", "look_closely", "blocked", "nothing_to_do"]
    if frame_verdict_hint and order.index(frame_verdict_hint) > order.index(core.verdict):
        return core.model_copy(update={"verdict": frame_verdict_hint, "flags": core.flags + ["frame_escalated"]})
    return core


# ---------------------------------------------------------------- fake core

class FakeCoreAdapter:
    """Fixed placeholder results so the frame and UI never wait on the core.

    These numbers are NOT computed from prices. They exist to exercise the pipeline, the
    grounding checker and the UI. `core_version` and the `fake_core` flag make that visible.
    """
    name = "fake"

    def __init__(self, markets: list[Market] | None = None):
        self._markets = markets or []

    def price(self, relation: Relation, size: float) -> CoreResult:
        checks = [
            GuardCheck(name="rule_match", passed=True, detail="placeholder"),
            GuardCheck(name="depth", passed=True, detail="placeholder"),
            GuardCheck(name="min_profit", passed=True, detail="placeholder"),
        ]
        if relation.relation in ("unrelated", "unclear"):
            return CoreResult(verdict="nothing_to_do", gross_edge=0.0, net_edge=0.0, max_fillable_size=0.0,
                              flags=["fake_core"], guard_checks=checks, core_version="FAKE-0")
        return CoreResult(
            verdict="look_closely", gross_edge=0.042, fees={"polymarket": 0.006, "kalshi": 0.011},
            slippage=0.004, net_edge=0.021, max_fillable_size=850.0, days_to_resolution=54.0,
            annualised_return=0.142, worst_case=-0.012, ci_low=0.009, ci_high=0.031, book_age_s=3.0,
            flags=["fake_core", "thin_depth"], guard_checks=checks, core_version="FAKE-0",
        )

    def list_open_markets(self, venues: list[str]) -> list[Market]:
        return [m for m in self._markets if m.venue in venues]

    def parse_link(self, url: str) -> Market | None:
        return next((m for m in self._markets if m.url and m.url.rstrip("/") == url.strip().rstrip("/")), None)

    def compare_rules(self, a: Market, b: Market) -> VerifiedRuleDiff | None:
        return None


# ---------------------------------------------------------------- real core (UNVERIFIED)

class RealCoreAdapter:
    """Wraps Debby's core. UNVERIFIED: every import name and field mapping below is a guess
    from the planning notes, to be replaced once docs/CORE_MAP.md is written from real code."""
    name = "real"

    def __init__(self, module_candidates: tuple[str, ...] = ("fac_core", "core", "engine")):
        self._mod = None
        errors = []
        for name in module_candidates:
            try:
                self._mod = importlib.import_module(name)
                break
            except ImportError as e:
                errors.append(f"{name}: {e}")
        if self._mod is None:
            raise CoreNotAvailable("numeric core not importable (" + "; ".join(errors) + "). Use FRAME_CORE=fake.")

    def price(self, relation: Relation, size: float) -> CoreResult:
        evaluate = getattr(self._mod, "evaluate", None)
        if evaluate is None:
            raise CoreNotAvailable(f"{self._mod.__name__} has no evaluate(); see docs/CORE_REQUESTS.md")
        core_rel = self._to_core_relation(relation)  # TODO(core): real mapping
        opp = evaluate(core_rel, size)
        return self._from_core_result(opp)

    def _to_core_relation(self, relation: Relation):
        # TODO(core): build the core's own Relation type. Passing a dict until we see it.
        return relation.model_dump()

    def _from_core_result(self, opp) -> CoreResult:
        # TODO(core): field names assumed from note 12's `Opportunity`. Verdict field name unknown.
        d = opp.model_dump() if hasattr(opp, "model_dump") else dict(vars(opp)) if not isinstance(opp, dict) else opp
        fees = d.get("fees", {})
        if isinstance(fees, list):
            fees = {f"leg{i}": float(v) for i, v in enumerate(fees)}
        d["fees"] = fees
        d.setdefault("verdict", d.get("guard_verdict", "look_closely"))
        return CoreResult.model_validate(d)

    def list_open_markets(self, venues: list[str]) -> list[Market]:
        raise CoreNotAvailable("TODO(core): wire to the core's compliant venue clients (compliance.py adapters)")

    def parse_link(self, url: str) -> Market | None:
        raise CoreNotAvailable("TODO(core): wire to venues.py link parser")

    def compare_rules(self, a: Market, b: Market) -> VerifiedRuleDiff | None:
        return None  # TODO(core): reuse relations.py --llm extractor if its output fits RuleDiff


def get_adapter(kind: str, markets: list[Market] | None = None) -> CoreAdapter:
    if kind == "real":
        return RealCoreAdapter()
    return FakeCoreAdapter(markets)
