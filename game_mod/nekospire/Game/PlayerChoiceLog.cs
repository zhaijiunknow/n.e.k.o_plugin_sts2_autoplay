// What the local player just chose, read from the game's own records (GET /choices).
//
// The plugin compares these with what it would have recommended at that moment (the end-of-run review).
// Nothing here guesses from screen diffs and nothing changes game state:
//   * combat: CombatManager.History (card plays and potion uses, with their targets) and the
//     PlayerEndedTurn event;
//   * everything else: the local player's PlayerMapPointHistoryEntry of the current map point — the lists
//     the game keeps for its own run-history screen (card / relic choices, event options, rest site
//     options, purchases, removed and upgraded cards).
// A choice made through POST /action shows up here as well: the plugin knows which steps it made itself.
using MegaCrit.Sts2.Core.Combat;
using MegaCrit.Sts2.Core.Combat.History;
using MegaCrit.Sts2.Core.Combat.History.Entries;
using MegaCrit.Sts2.Core.Entities.Creatures;
using MegaCrit.Sts2.Core.Entities.Players;
using MegaCrit.Sts2.Core.Logging;
using MegaCrit.Sts2.Core.Runs;

namespace NekoComm.Game;

internal sealed class PlayerChoicePayload
{
    public long seq { get; init; }
    public long at_ms { get; init; }
    /// <summary>
    /// play_card | use_potion | end_turn | discard_potion | choose_map_node | card_choice | relic_choice |
    /// event_option | rest_option | buy_card | buy_relic | buy_potion | card_removed | card_upgraded
    /// </summary>
    public string kind { get; init; } = "";
    /// <summary>Model id (card / potion / relic), option id, event title key, or "col,row" for a map node. Null: skipped.</summary>
    public string? id { get; init; }
    public int? upgrade { get; init; }
    /// <summary>"player" or the monster's model id; null for an untargeted play.</summary>
    public string? target { get; init; }
    /// <summary>Position in combat.enemies[] (the index play_card / use_potion take); null when not an enemy or already gone.</summary>
    public int? target_index { get; init; }
    /// <summary>card_choice / relic_choice: everything that was offered.</summary>
    public string[]? offered { get; init; }
    public int floor { get; init; }
    public int act { get; init; }
    public int? round { get; init; }
}

internal sealed class PlayerChoicesPayload
{
    public long latest_seq { get; init; }
    public PlayerChoicePayload[] choices { get; init; } = Array.Empty<PlayerChoicePayload>();
}

internal static class PlayerChoiceLog
{
    private const string LogPrefix = "[NekoComm.Choices]";
    private const int Capacity = 256;

    private static readonly object Gate = new();
    private static readonly List<PlayerChoicePayload> Buffer = new();
    private static long _seq;
    private static bool _installed;

    // Combat history is cleared between combats: a count below the cursor means a new combat began.
    private static int _combatCursor;
    // Potions used in combat are reported from the combat history (it has the target); the same use also
    // lands in the map point entry's PotionUsed list, where it is skipped.
    private static int _combatPotions;

    /// <summary>Who the choices belong to. The offline harness has no network identity and points this at the run's player.</summary>
    internal static Func<RunState?, Player?> LocalPlayer = run => GameStateService.GetLocalPlayer(run);

    private static RunState? _run;
    private static int _points;
    private static PlayerMapPointHistoryEntry? _entry;
    private static readonly Dictionary<string, int> Counts = new();

    /// <summary>Game thread, once. The combat events are instance events of a singleton that outlives combats.</summary>
    public static void Install()
    {
        if (_installed) return;
        _installed = true;
        try
        {
            CombatManager.Instance.History.Changed += OnCombatHistoryChanged;
            CombatManager.Instance.PlayerEndedTurn += OnPlayerEndedTurn;
        }
        catch (Exception ex)
        {
            Log.Warn($"{LogPrefix} combat hooks not installed: {ex.Message}");
        }
    }

    /// <summary>GET /choices?since=N. Game thread.</summary>
    public static PlayerChoicesPayload Read(long since)
    {
        Collect();
        lock (Gate)
            return new PlayerChoicesPayload { latest_seq = _seq, choices = Buffer.Where(c => c.seq > since).ToArray() };
    }

    private static void Add(RunState run, string kind, string? id = null, int? upgrade = null, Creature? target = null,
        bool hasTarget = false, string[]? offered = null, int? round = null)
    {
        int? targetIndex = null;
        string? targetLabel = null;
        if (hasTarget && target != null)
        {
            targetLabel = target.IsPlayer ? "player" : target.Monster?.Id.Entry;
            int index = CombatManager.Instance.DebugOnlyGetState()?.Enemies.ToList().IndexOf(target) ?? -1;
            if (index >= 0) targetIndex = index;
        }
        lock (Gate)
        {
            Buffer.Add(new PlayerChoicePayload
            {
                seq = ++_seq,
                at_ms = DateTimeOffset.UtcNow.ToUnixTimeMilliseconds(),
                kind = kind,
                id = id,
                upgrade = upgrade,
                target = targetLabel,
                target_index = targetIndex,
                offered = offered,
                floor = run.TotalFloor,
                act = run.CurrentActIndex + 1,
                round = round ?? (CombatManager.Instance.IsInProgress ? CombatManager.Instance.DebugOnlyGetState()?.RoundNumber : null),
            });
            if (Buffer.Count > Capacity) Buffer.RemoveRange(0, Buffer.Count - Capacity);
        }
    }

    // ---------------------------------------------------------------- combat

    private static void OnCombatHistoryChanged()
    {
        try
        {
            var run = RunManager.Instance.DebugOnlyGetState();
            var me = LocalPlayer(run);
            var entries = CombatManager.Instance.History.Entries as IReadOnlyList<CombatHistoryEntry> ?? CombatManager.Instance.History.Entries.ToList();
            if (entries.Count < _combatCursor) _combatCursor = 0;
            for (int i = _combatCursor; i < entries.Count; i++)
            {
                if (run == null || me == null) continue;
                switch (entries[i])
                {
                    // A replay (PlayIndex > 0) or a card another effect played is not a choice.
                    case CardPlayStartedEntry { CardPlay: { IsAutoPlay: false, IsFirstInSeries: true } play } when play.Player == me:
                        Add(run, "play_card", play.Card.Id.Entry, play.Card.CurrentUpgradeLevel, play.Target, hasTarget: true);
                        break;
                    case PotionUsedEntry potion when potion.Actor == me.Creature:
                        _combatPotions++;
                        Add(run, "use_potion", potion.Potion.Id.Entry, target: potion.Target, hasTarget: true);
                        break;
                }
            }
            _combatCursor = entries.Count;
        }
        catch (Exception ex)
        {
            Log.Warn($"{LogPrefix} combat history read failed: {ex.Message}");
        }
    }

    private static void OnPlayerEndedTurn(Player player, bool _)
    {
        try
        {
            var run = RunManager.Instance.DebugOnlyGetState();
            if (run != null && player == LocalPlayer(run)) Add(run, "end_turn");
        }
        catch (Exception ex)
        {
            Log.Warn($"{LogPrefix} end turn read failed: {ex.Message}");
        }
    }

    // ---------------------------------------------------------------- everything else

    /// <summary>Reads what the map point history gained since the last call. Game thread; cheap when nothing changed.</summary>
    public static int Collect()
    {
        try
        {
            var run = RunManager.Instance.DebugOnlyGetState();
            var me = LocalPlayer(run);
            if (run == null || me == null)
            {
                _run = null;
                _entry = null;
                Counts.Clear();
                return 0;
            }
            long before = _seq;
            var current = CurrentEntry(run, me);
            if (!ReferenceEquals(run, _run))
            {
                // First look at this run (a new run, or the mod started mid-run): what already happened is not replayed.
                _run = run;
                _points = run.TotalFloor;
                _entry = current;
                _combatPotions = 0;
                Baseline(current);
                return 0;
            }
            // The previous map point may have gained its last choices after we last looked.
            if (_entry != null && !ReferenceEquals(_entry, current)) Diff(run, _entry);
            if (run.TotalFloor > _points)
            {
                _points = run.TotalFloor;
                if (run.CurrentMapCoord is { } coord) Add(run, "choose_map_node", $"{coord.col},{coord.row}");
            }
            if (!ReferenceEquals(_entry, current))
            {
                _entry = current;
                Counts.Clear();
            }
            if (current != null) Diff(run, current);
            return (int)(_seq - before);
        }
        catch (Exception ex)
        {
            Log.Warn($"{LogPrefix} collect failed: {ex.Message}");
            return 0;
        }
    }

    private static PlayerMapPointHistoryEntry? CurrentEntry(RunState run, Player me) =>
        run.CurrentMapPointHistoryEntry?.PlayerStats.FirstOrDefault(p => p.PlayerId == me.NetId);

    private static int Seen(string list, int count)
    {
        int old = Counts.GetValueOrDefault(list);
        Counts[list] = count;
        return Math.Min(old, count);
    }

    private static void Baseline(PlayerMapPointHistoryEntry? e)
    {
        Counts.Clear();
        if (e == null) return;
        Counts["cards"] = e.CardChoices.Count;
        Counts["relics"] = e.RelicChoices.Count;
        Counts["events"] = e.EventChoices.Count;
        Counts["rest"] = e.RestSiteChoices.Count;
        Counts["gained"] = e.CardsGained.Count;
        Counts["spent"] = e.GoldSpent;
        Counts["bought_relics"] = e.BoughtRelics.Count;
        Counts["bought_potions"] = e.BoughtPotions.Count;
        Counts["removed"] = e.CardsRemoved.Count;
        Counts["upgraded"] = e.UpgradedCards.Count;
        Counts["potions_used"] = e.PotionUsed.Count;
        Counts["potions_discarded"] = e.PotionDiscarded.Count;
    }

    private static void Diff(RunState run, PlayerMapPointHistoryEntry e)
    {
        // One reward screen writes all its cards at once: the new entries are one choice.
        int from = Seen("cards", e.CardChoices.Count);
        if (e.CardChoices.Count > from)
        {
            var fresh = e.CardChoices.Skip(from).ToList();
            var picked = fresh.Where(c => c.wasPicked).Select(c => c.Card).FirstOrDefault();
            Add(run, "card_choice", picked?.Id?.Entry, picked?.CurrentUpgradeLevel,
                offered: fresh.Select(c => c.Card.Id?.Entry ?? "").ToArray());
        }
        from = Seen("relics", e.RelicChoices.Count);
        if (e.RelicChoices.Count > from)
        {
            var fresh = e.RelicChoices.Skip(from).ToList();
            var picked = fresh.Where(c => c.wasPicked).Select(c => c.choice.Entry).FirstOrDefault();
            Add(run, "relic_choice", picked, offered: fresh.Select(c => c.choice.Entry).ToArray());
        }
        for (int i = Seen("events", e.EventChoices.Count); i < e.EventChoices.Count; i++)
            Add(run, "event_option", e.EventChoices[i].Title?.LocEntryKey);
        for (int i = Seen("rest", e.RestSiteChoices.Count); i < e.RestSiteChoices.Count; i++)
            Add(run, "rest_option", e.RestSiteChoices[i]);

        // A card that came with gold spent was bought; card rewards and event gains are covered above.
        int spentBefore = Counts.GetValueOrDefault("spent");
        Counts["spent"] = e.GoldSpent;
        from = Seen("gained", e.CardsGained.Count);
        if (e.GoldSpent > spentBefore)
            for (int i = from; i < e.CardsGained.Count; i++)
                Add(run, "buy_card", e.CardsGained[i].Id?.Entry, e.CardsGained[i].CurrentUpgradeLevel);
        for (int i = Seen("bought_relics", e.BoughtRelics.Count); i < e.BoughtRelics.Count; i++)
            Add(run, "buy_relic", e.BoughtRelics[i].Entry);
        for (int i = Seen("bought_potions", e.BoughtPotions.Count); i < e.BoughtPotions.Count; i++)
            Add(run, "buy_potion", e.BoughtPotions[i].Entry);
        for (int i = Seen("removed", e.CardsRemoved.Count); i < e.CardsRemoved.Count; i++)
            Add(run, "card_removed", e.CardsRemoved[i].Id?.Entry, e.CardsRemoved[i].CurrentUpgradeLevel);
        for (int i = Seen("upgraded", e.UpgradedCards.Count); i < e.UpgradedCards.Count; i++)
            Add(run, "card_upgraded", e.UpgradedCards[i].Entry);
        for (int i = Seen("potions_used", e.PotionUsed.Count); i < e.PotionUsed.Count; i++)
        {
            if (_combatPotions > 0) { _combatPotions--; continue; }
            Add(run, "use_potion", e.PotionUsed[i].Entry);
        }
        for (int i = Seen("potions_discarded", e.PotionDiscarded.Count); i < e.PotionDiscarded.Count; i++)
            Add(run, "discard_potion", e.PotionDiscarded[i].Entry);
    }
}
