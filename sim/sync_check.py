"""Port-sync checker: how much of the game's card surface the C# Sim model actually covers.

The mod's deterministic resolver (game_mod/neko_comm/Game/Sim/) models a finite type surface. This
tool reads the static game data (cards.json / powers.json) and classifies each card into:

  exact    -- every declared var is recognised AND every power it applies has a behaviour in the
              Sim model (SimPower registry / SimBuild field handlers). Fully simulated.
  inferred -- vars recognised but a power it applies has NO behaviour in the model -> stored but
              effectively a no-op in the resolver (honestly flagged as not-yet-faithful).
  unmapped -- a var name the model does not even recognise (e.g. new/custom mechanics).

It is NOT a correctness check (that is capture-vs-replay). It quantifies the type-surface coverage
gap, which is what the /solver/plan `coverage` block reports per-hand. Run:

    python -m sim.sync_check [cards.json path] [powers.json path]
"""
from __future__ import annotations
import json
import re
import sys
from pathlib import Path

# Mirrors the C# Sim model's modelled surfaces (SimBuild / SimPower). Keep in sync by hand.
NUMERIC_VARS = {
    "Damage", "Block", "Cards", "Draw", "DrawCards", "Energy", "HpLoss",
    "MaxHp", "Heal", "ExtraDamage", "Stars", "Repeat",
}
POWER_VARS = {
    "VulnerablePower", "WeakPower", "StrengthPower", "DexterityPower", "FocusPower",
    "PoisonPower", "IntangiblePower", "BufferPower", "RitualPower", "RegenPower",
    "PlatingPower", "PlatedArmorPower", "ThornsPower", "DoomPower", "DemonFormPower",
}
# behaviour registry keys (lower snake) — a power var's key must be here to actually take effect.
POWER_BEHAVIORS = {
    "strength_power", "dexterity_power", "weak_power", "vulnerable_power", "frail_power",
    "intangible_power", "buffer_power", "ritual_power", "regen_power", "plating_power",
    "plated_armor_power", "thorns_power", "doom_power", "demon_form_power", "poison_power",
    "focus_power",
}

_KNOWN = NUMERIC_VARS | POWER_VARS


def _camel_to_snake(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def _power_var_behaviour_key(var_name: str) -> str:
    # "ThornsPower" -> "thorns_power"; a bare "Thorns" -> "thorns".
    return _camel_to_snake(var_name).removesuffix("_power") + "_power"


def classify(card: dict) -> str:
    vars_ = card.get("vars") or {}
    if not isinstance(vars_, dict):
        return "unmapped"
    unmatched = [k for k in vars_ if k not in _KNOWN]
    if unmatched:
        return "unmapped"
    for k in vars_:
        if k in POWER_VARS:
            if _power_var_behaviour_key(k) not in POWER_BEHAVIORS:
                return "inferred"
    return "exact"


def report(cards_path: Path, powers_path: Path | None = None) -> dict:
    cards = json.loads(cards_path.read_text(encoding="utf-8"))
    counts = {"exact": 0, "inferred": 0, "unmapped": 0}
    by_id: dict[str, list[str]] = {"exact": [], "inferred": [], "unmapped": []}
    for card in cards:
        cls = classify(card)
        counts[cls] += 1
        if len(by_id[cls]) < 25:
            by_id[cls].append(str(card.get("id")))
    total = len(cards)
    return {
        "total": total,
        "counts": counts,
        "coverage_pct": {"exact": counts["exact"] / max(1, total)},
        "samples": by_id,
    }


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    data_dir = Path(argv[0]) if argv else Path("game_mod/mcp_server/data/eng")
    cards_path = data_dir / "cards.json"
    powers_path = data_dir / "powers.json" if (data_dir / "powers.json").exists() else None
    if not cards_path.exists():
        print(f"cards.json not found at {cards_path}")
        return 1
    r = report(cards_path, powers_path)
    c = r["counts"]
    print(f"cards.json: {r['total']} cards")
    print(f"  exact    {c['exact']:4d} ({c['exact']/max(1,r['total']):.0%})")
    print(f"  inferred {c['inferred']:4d} (recognised vars, power has no behaviour)")
    print(f"  unmapped {c['unmapped']:4d} (var name not recognised)")
    print("\nexact samples:", ", ".join(r["samples"]["exact"][:12]) or "none")
    print("inferred (no behaviour):", ", ".join(r["samples"]["inferred"][:12]) or "none")
    print("unmapped names:", ", ".join(r["samples"]["unmapped"][:12]) or "none")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
