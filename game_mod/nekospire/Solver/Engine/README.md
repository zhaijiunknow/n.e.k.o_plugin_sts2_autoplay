# CombatSolver embedded simulation engine

This directory contains the combat-only prediction core imported from the local
RandomForeseer fork at upstream commit
`598dce061f26cf5659a12d3a62cb2e80cc498dfe`, then moved to the
`CombatSolver.Engine` namespace.

Included: prediction state, cards and piles, RNG, fork/state-store/history,
combat simulation, and combat mirror registries.

Excluded: mod initialization, Harmony patches, Godot UI, hover previews,
settings, telemetry, localization, integrations, debug features, and all
out-of-combat prediction features.

The embedded engine has no RandomForeseer assembly or mod dependency and is
designed to coexist with the independently installed consumer-facing
RandomForeseer mod.

The import also replaces C# 14 extension blocks with equivalent classic
extension methods so the project remains buildable with the game's .NET 9 / C#
13 toolchain. Consumer logging is routed through CombatSolver and telemetry
calls are not included.

Simulation-only model clones execute the game's `MemberwiseClone`, virtual
`DeepCloneFields`, and virtual `AfterCloned` stages directly. They intentionally
do not broadcast consumer-facing model-clone notifications. During a solver
scope, RitsuLib free-play bindings are also read from the simulated card cost
state instead of creating process-global attached-state entries for temporary
branches. Live game clones and card plays are unaffected.
