// Encodes a PolicyDecision into the binary observation frame the AlphaDefect network reads, and exposes
// the logic-only entry points game_mod/PolicyFidelity uses to compare those frames with upstream's engine.
using System.Runtime.CompilerServices;
using MegaCrit.Sts2.Core.Combat;
using MegaCrit.Sts2.Core.Entities.Merchant;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Rewards;
using MegaCrit.Sts2.Core.Runs;
using Sts2Headless;

[assembly: InternalsVisibleTo("PolicyFidelity")]

namespace NekoComm.Policy;

internal static class PolicyFrame
{
    public static bool VocabLoaded { get; private set; }

    /// <summary>
    /// Loads the vocabulary (and its aliases, and the no-downside relic list) from <paramref name="dataDir"/>.
    /// Returns false when vocab.txt is missing: without it every id encodes as "unknown".
    /// </summary>
    public static bool LoadData(string dataDir)
    {
        string vocab = Path.Combine(dataDir, "vocab.txt");
        if (!File.Exists(vocab)) return false;
        Vocab.Load(vocab);
        string relics = Path.Combine(dataDir, "no_downside_relics.txt");
        RewardsFlow.NoDownside = File.Exists(relics)
            ? File.ReadAllLines(relics).Select(l => l.Trim()).Where(l => l.Length > 0 && !l.StartsWith('#')).ToHashSet()
            : new HashSet<string>();
        VocabLoaded = Vocab.Count > 2;
        return VocabLoaded;
    }

    /// <summary>Evaluation settings of the released networks: public information only, route summaries on.</summary>
    public static void Configure(int? formatOverride, bool routeSummary = true)
    {
        Observation.FormatOverride = formatOverride;
        Observation.RouteSummary = routeSummary;
        Observation.Privileged = false;
        Observation.FutureContent = false;
        Observation.Oracle = false;
    }

    public static Observation Encode(PolicyDecision d) =>
        Observation.Build(new RunSession { Selecting = d.Selecting, SelectedIndices = d.SelectedIndices }, d.Decision, labels: false);
}

/// <summary>
/// Logic-only frame builders for the offline fidelity check. Arguments are game or BCL types only, so the
/// check (which hosts upstream's headless engine) can hand over that engine's pending choices.
/// </summary>
internal static class PolicyFidelityProbe
{
    public static int LastCandidates { get; private set; }
    /// <summary>The candidates of the last decision built here, as GET /policy/decision would list them.</summary>
    public static List<(string Kind, string Label, int? TargetIndex)>? LastChoices { get; private set; }

    public static void ResetLast() => LastChoices = null;

    static byte[]? Frame(PolicyDecision? d)
    {
        LastCandidates = d?.Decision.Choices.Count ?? -1;
        LastChoices = d?.Decision.Choices.Select((c, i) => (c.Kind.ToString(), c.Label, d.Actions[i]?.TargetIndex)).ToList();
        return d == null ? null : PolicyFrame.Encode(d).Serialize();
    }

    public static byte[]? Combat() =>
        Frame(PolicyDecisionBuilder.Combat(RunSession.Me, CombatManager.Instance.DebugOnlyGetState()!));

    public static byte[]? Map() => Frame(PolicyDecisionBuilder.Map(RunSession.State, RunSession.Me, null));

    public static byte[]? Rest(bool alreadyChose) =>
        Frame(PolicyDecisionBuilder.RestSite(RunManager.Instance.RestSiteSynchronizer.GetLocalOptions(), RunSession.Me, alreadyChose, null));

    public static byte[]? Event() =>
        Frame(PolicyDecisionBuilder.Event(RunManager.Instance.EventSynchronizer.GetLocalEvent(), RunSession.Me));

    public static byte[]? Shop(MerchantInventory inventory) =>
        Frame(PolicyDecisionBuilder.Shop(inventory, RunSession.Me, null, null));

    public static byte[]? Treasure() =>
        Frame(PolicyDecisionBuilder.Treasure(RunManager.Instance.TreasureRoomRelicSynchronizer.CurrentRelics!, RunSession.Me, null));

    public static byte[]? Rewards(RewardsSet set, IReadOnlySet<Reward> attempted)
    {
        var pending = new PendingRewards(set, attempted);
        RewardsFlow.Pending = pending;
        try { return Frame(PolicyDecisionBuilder.Rewards(pending, RunSession.Me, null, canLeave: false)); }
        finally { RewardsFlow.Reset(); }
    }

    public static byte[]? CardReward(RewardsSet? activeSet, Reward? activeChoice, IReadOnlySet<Reward>? activeAttempted,
        IReadOnlyList<CardModel> cards, IReadOnlyList<string> alternativeIds)
    {
        RewardsFlow.ActiveSet = activeSet; RewardsFlow.ActiveChoice = activeChoice; RewardsFlow.ActiveAttempted = activeAttempted;
        try { return Frame(PolicyDecisionBuilder.CardRewardChoice(cards, alternativeIds, null)); }
        finally { RewardsFlow.Reset(); }
    }

    /// <summary>
    /// What opened the selection (CardSelectCmd method, enchantment, source, potion in use) is NOT passed in:
    /// it comes from nekospire's own PolicyHooks, exactly as in the live game, so the comparison covers them.
    /// </summary>
    public static byte[]? CardSelect(IReadOnlyList<CardModel> options, int min, int max, bool cancelable,
        string? promptTable, string? promptKey, IReadOnlyList<int> selected, bool allowCancel)
    {
        var call = PolicyHooks.LastSelect;
        var info = call is { HasInfo: true } ? new SelectInfo(promptTable, promptKey, call.Method, call.Enchantment, call.Source) : null;
        var pending = new PendingCardChoice(options, min, max, cancelable, info);
        return Frame(PolicyDecisionBuilder.CardSelect(pending, selected, allowCancel, null, null, null));
    }

    public static void InstallHooks() => PolicyHooks.Install();

    /// <summary>
    /// The combat decision as JSON (frame + candidates with their nekospire action indices), for
    /// game_mod/SolverSmoke --policy: it drives the built DLL by reflection and cannot see internal types.
    /// </summary>
    public static string CombatJson(int? obsFormat)
    {
        PolicyFrame.Configure(obsFormat);
        var d = PolicyDecisionBuilder.Combat(RunSession.Me, CombatManager.Instance.DebugOnlyGetState()!);
        return System.Text.Json.JsonSerializer.Serialize(new
        {
            frame = Convert.ToBase64String(PolicyFrame.Encode(d).Serialize()),
            candidates = d.Decision.Choices.Select((c, i) => new
            {
                label = c.Label,
                action = d.Actions[i]?.Kind,
                card_index = d.Actions[i]?.CardIndex,
                target_index = d.Actions[i]?.TargetIndex,
                option_index = d.Actions[i]?.OptionIndex,
            }),
        });
    }
}
