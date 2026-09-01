using MegaCrit.Sts2.Core.Entities.Creatures;

namespace CombatSolver.Engine.InCombat.Mirrors.Orbs;

internal class OrbMirrorContext : CombatMirrorContext;

internal sealed class OrbPassiveMirrorContext : OrbMirrorContext
{
    public Creature? Target { get; init; }
}
