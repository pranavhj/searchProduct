"""Product-agnostic hard rules applied to the model's factual checks (vet.apply_rules)."""
from __future__ import annotations

from pathlib import Path

from price_watch import report, vet
from price_watch.analyze import ItemSummary
from price_watch.config import WatchItem
from price_watch.fetch import FetchResult


def _v(verdict: str = "buy", meets: str = "yes", risk: str = "low", whole: str = "yes",
       unmet: list[str] | None = None) -> vet.Vetting:
    return vet.Vetting(verdict, "s", meets_requirements=meets, knockoff_risk=risk, whole_item=whole,
                       unmet_requirements=unmet or [])


def test_unmet_requirement_forces_avoid() -> None:
    assert vet.apply_rules(_v(meets="no", unmet=["needs 4K"])).verdict == "avoid"


def test_unmet_requirement_is_named_in_rules() -> None:
    assert vet.apply_rules(_v(meets="no", unmet=["needs 4K"])).rules_applied == ["fails requirements (needs 4K)"]


def test_high_knockoff_risk_forces_avoid() -> None:
    assert vet.apply_rules(_v(risk="high")).verdict == "avoid"


def test_part_only_price_forces_avoid() -> None:
    assert vet.apply_rules(_v(whole="no")).verdict == "avoid"


def test_buy_with_unconfirmed_requirements_capped_at_ok() -> None:
    assert vet.apply_rules(_v(meets="unclear")).verdict == "ok"


def test_clean_checks_keep_model_buy() -> None:
    assert vet.apply_rules(_v()).verdict == "buy"


def test_rules_never_soften_model_avoid() -> None:
    assert vet.apply_rules(_v(verdict="avoid")).verdict == "avoid"


def test_model_verdict_kept_for_audit() -> None:
    assert vet.apply_rules(_v(risk="high")).model_verdict == "buy"


def test_checks_parsed_case_insensitively() -> None:
    v = vet.Vetting.from_json({"verdict": "ok", "summary": "s", "knockoff_risk": "HIGH "})
    assert v.knockoff_risk == "high"


def test_missing_checks_default_to_not_confirmed() -> None:
    assert vet.apply_rules(vet.Vetting.from_json({"verdict": "buy", "summary": "s"})).verdict == "ok"


def _summary(requirements: str) -> ItemSummary:
    item = WatchItem("thing", "some thing", requirements=requirements)
    return ItemSummary(item, FetchResult("thing", [], {}, 0, {}), None, None, 0, None)


def test_digest_flags_item_without_requirements() -> None:
    assert "needs the new-item questions" in report.digest([_summary("")], Path("r.md"))


def test_digest_quiet_when_requirements_set() -> None:
    assert "needs the new-item questions" not in report.digest([_summary("4K, HDMI 2.1")], Path("r.md"))
