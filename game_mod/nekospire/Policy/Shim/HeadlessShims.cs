// Live-game stand-ins for the parts of AlphaDefect's headless host that its observation encoder reads.
//
// Upstream's encoder (Policy/Vendor, namespace Sts2Headless) is shared source between its headless engine
// and its live bridge mod. In the headless engine these types are the fake UI layer: they intercept the
// game's screens and hold the pending choice. Here the real UI is running, so they are plain holders that
// PolicyDecisionService fills from the live screen right before a frame is encoded.
//
// Member names and shapes mirror upstream (engine/Sts2Headless/{AgentCardSelector,FakeScreens,
// Engine/RewardsFlow,Engine/RunSession,Engine/UiFlows}.cs) because the vendored encoder compiles against
// them; nothing here installs a Harmony patch or touches the game.
using MegaCrit.Sts2.Core.Context;
using MegaCrit.Sts2.Core.Entities.Players;
using MegaCrit.Sts2.Core.Entities.RestSite;
using MegaCrit.Sts2.Core.Map;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Rewards;
using MegaCrit.Sts2.Core.Rooms;
using MegaCrit.Sts2.Core.Runs;

namespace Sts2Headless;

/// <summary>What a card-selection screen is for (prompt, the CardSelectCmd method that opened it, source).</summary>
public sealed record SelectInfo(string? PromptTable, string? PromptKey, string Method, EnchantmentModel? Enchantment,
    AbstractModel? Source = null);

/// <summary>A pending "pick min..max of these cards" screen.</summary>
public sealed class PendingCardChoice(IReadOnlyList<CardModel> options, int min, int max, bool cancelable, SelectInfo? info = null)
{
    public SelectInfo? Info { get; } = info;
    public IReadOnlyList<CardModel> Options { get; } = options;
    public int Min { get; } = min;
    public int Max { get; } = max;
    public bool Cancelable { get; } = cancelable;
}

public sealed class AgentCardSelector
{
    /// <summary>The potion being used while its effect resolves (names the source of a potion-opened card selection).</summary>
    public static PotionModel? UsingPotion { get; set; }
}

/// <summary>One open rewards screen.</summary>
public sealed class PendingRewards(RewardsSet set, IReadOnlySet<Reward> attempted)
{
    public RewardsSet Set { get; } = set;
    public List<Reward> Available => Set.Rewards
        .Where(r => !r.SuccessfullySelected && !attempted.Contains(r))
        .Where(r => r is not PotionReward || Set.Player.HasOpenPotionSlots)
        .ToList();
    public bool IsTerminal => Set.Room is CombatRoom;
}

public static class RewardsFlow
{
    public static PendingRewards? Pending { get; internal set; }
    public static RewardsSet? ActiveSet { get; internal set; }
    public static Reward? ActiveChoice { get; internal set; }
    public static IReadOnlySet<Reward>? ActiveAttempted { get; internal set; }

    public static IEnumerable<Reward> Unclaimed(RewardsSet set, IReadOnlySet<Reward>? attempted, Reward? except) =>
        set.Rewards.Where(r => !r.SuccessfullySelected && r != except && (attempted == null || !attempted.Contains(r)));

    /// <summary>Relic ids (no RELIC. prefix) upstream's engine takes without asking the policy.</summary>
    public static HashSet<string> NoDownside { get; internal set; } = new();

    public static bool IsNoDownside(RelicModel? r) => r != null && NoDownside.Contains(r.Id.Entry);

    internal static void Reset() { Pending = null; ActiveSet = null; ActiveChoice = null; ActiveAttempted = null; }
}

public static class FakeScreens
{
    public static MegaCrit.Sts2.Core.Events.Custom.CrystalSphereEvent.CrystalSphereMinigame? CrystalSphere { get; internal set; }
}

public static class UiFlows
{
    /// <summary>Header field 12; the network does not read it.</summary>
    public static int CombatsWon;
}

public static class RunSessionMap
{
    /// <summary>Travelable map points, same rule as NMapScreen.RecalculateTravelability (upstream RunSession.cs).</summary>
    public static IEnumerable<MapPoint> Travelable(RunState s)
    {
        var map = s.Map;
        if (s.VisitedMapCoords.Count == 0) return new[] { map.StartingMapPoint };
        MapCoord last = s.VisitedMapCoords[^1];
        if (map.SecondBossMapPoint != null && last == map.BossMapPoint.coord) return new[] { map.SecondBossMapPoint };
        if (last.row == map.GetRowCount() - 1) return new[] { map.BossMapPoint };
        var point = map.GetPoint(last);
        return point == null ? new[] { map.StartingMapPoint } : MapTravel.GetTravelablePointsFrom(s, point);
    }
}

/// <summary>The slice of upstream's RunSession the encoder reads. One instance per encoded frame.</summary>
public sealed class RunSession
{
    public static RunState State => RunManager.Instance.DebugOnlyGetState()!;

    /// <summary>The observer. Upstream is single-player and uses run slot 0; here it is the local player.</summary>
    public static Player Me => LocalContext.GetMe((IPlayerCollection)State) ?? State.Players[0];

    public bool IsVictory { get; init; }
    /// <summary>Header field 13; the network does not read it.</summary>
    public int Steps { get; init; }
    /// <summary>Upstream's single-fight practice mode; never the case in the live game.</summary>
    public bool CombatOnly => false;
    public PendingCardChoice? Selecting { get; init; }
    public IReadOnlyList<int> SelectedIndices { get; init; } = Array.Empty<int>();

    /// <summary>Rest heal preview: (actual, nominal, extra effect lines). Same as upstream RunSession.RestHealInfo.</summary>
    public static (int actual, int nominal, int extra) RestHealInfo(Player p)
    {
        var cr = p.Creature;
        int nominal = (int)HealRestSiteOption.GetHealAmount(p);
        int actual = Math.Clamp(nominal, 0, Math.Max(0, cr.MaxHp - cr.CurrentHp));
        int extra = 0;
        foreach (var m in p.RunState.IterateHookListeners(null))
        {
            int n = m.ModifyExtraRestSiteHealText(p, Array.Empty<MegaCrit.Sts2.Core.Localization.LocString>()).Count;
            if (n > 0 && m.ModifyRestSiteHealAmount(cr, 100m) == 100m) extra += n;
        }
        return (actual, nominal, extra);
    }
}
