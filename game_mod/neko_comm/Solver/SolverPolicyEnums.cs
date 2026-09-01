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
