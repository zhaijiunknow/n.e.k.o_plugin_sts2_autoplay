// Lattice shim. In the upstream repository `SolverPotionPolicy` is declared inside
// src/Runtime/SolverSettings.cs (a Godot-coupled file we do NOT vendor). The vendored Search closure
// (SearchPolicySnapshot, CombatSearchCoordinator, CombatBeamSolver.*, PotionUsePolicy) references it.
namespace CombatSolver;

internal enum SolverPotionPolicy
{
    Disabled,
    Smart,
    RequireAtLeastOne,
}

// Lattice shim. `BossHpStrategy` is declared upstream inside src/Runtime/SolverSettings.cs (a Godot-coupled
// file we do NOT vendor). The vendored Search closure (ActEndingBossPolicy, SearchPolicySnapshot) references
// it, so it lives here alongside SolverPotionPolicy.
internal enum BossHpStrategy
{
    ProgressionFirst,
    MinimizeHpLoss,
}
