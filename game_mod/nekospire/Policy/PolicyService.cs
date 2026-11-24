// AlphaDefect policy seam: turns the live screen into the decision the network scores (GET /policy/decision)
// and carries out the candidate it picked (POST /action {"action":"policy_act"}).
//
// What is decided here and what is not:
//   * PolicyDecisionBuilder decides WHICH candidates exist and which game objects they refer to. That part
//     is logic-only and is checked byte for byte against upstream's engine (game_mod/PolicyFidelity).
//   * This file reads the live Godot screens to find those logic objects and to translate a candidate into
//     an existing nekospire action (hand index, reward button index, ...). No offline check covers it.
//   * Steps upstream's engine takes without asking the network (taking gold, taking a no-downside relic,
//     opening the chest, leaving a finished room) come back as "forced": one executable candidate, no frame.
//   * A screen with no adapter, or a candidate with no nekospire action, is reported as such; the caller
//     falls back to its own logic for that step. Nothing is guessed.
using System.Reflection;
using System.Runtime.CompilerServices;
using Godot;
using HarmonyLib;
using MegaCrit.Sts2.Core.CardSelection;
using MegaCrit.Sts2.Core.Combat;
using MegaCrit.Sts2.Core.Entities.CardRewardAlternatives;
using MegaCrit.Sts2.Core.Entities.Cards;
using MegaCrit.Sts2.Core.Entities.Merchant;
using MegaCrit.Sts2.Core.Entities.Players;
using MegaCrit.Sts2.Core.Logging;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Nodes;
using MegaCrit.Sts2.Core.Nodes.Combat;
using MegaCrit.Sts2.Core.Nodes.GodotExtensions;
using MegaCrit.Sts2.Core.Nodes.Rooms;
using MegaCrit.Sts2.Core.Nodes.Screens;
using MegaCrit.Sts2.Core.Nodes.Screens.CardSelection;
using MegaCrit.Sts2.Core.Nodes.Screens.Map;
using MegaCrit.Sts2.Core.Nodes.Screens.Overlays;
using MegaCrit.Sts2.Core.Nodes.Screens.ScreenContext;
using MegaCrit.Sts2.Core.Nodes.Screens.Shops;
using MegaCrit.Sts2.Core.Nodes.Screens.TreasureRoomRelic;
using MegaCrit.Sts2.Core.Rewards;
using MegaCrit.Sts2.Core.Rooms;
using MegaCrit.Sts2.Core.Runs;
using NekoComm.Game;
using NekoComm.Server;
using Sts2Headless;

namespace NekoComm.Policy;

/// <summary>Body of POST /policy/data: file name -> text (vocab.txt, aliases.txt, no_downside_relics.txt).</summary>
internal sealed class PolicyDataRequest
{
    public Dictionary<string, string>? files { get; set; }
}

internal sealed class PolicyCandidatePayload
{
    public int index { get; init; }
    public string kind { get; init; } = "";
    public string label { get; init; } = "";
    /// <summary>False: nekospire has no action for this candidate; if the network picks it, fall back.</summary>
    public bool executable { get; init; }
    public string? action { get; init; }
    public int? card_index { get; init; }
    public int? target_index { get; init; }
    public int? option_index { get; init; }
}

internal sealed class PolicyDecisionPayload
{
    public bool available { get; init; }
    /// <summary>Why no decision is offered (available=false). Always set in that case.</summary>
    public string? reason { get; init; }
    /// <summary>
    /// With available=false: the policy owns this moment but nothing can be done yet (enemy turn, an action
    /// still resolving). The caller should wait and ask again, NOT hand the step to its fallback.
    /// </summary>
    public bool wait { get; init; }
    /// <summary>Which vocabulary the mod encodes with (see PolicyService.DataId); null: none installed yet.</summary>
    public string? data_id { get; init; }
    /// <summary>Echo back as `command` in policy_act; a changed screen invalidates it.</summary>
    public string? decision_id { get; init; }
    public string? decision_kind { get; init; }
    public string? character { get; init; }
    public int ascension { get; init; }
    public bool multiplayer { get; init; }
    public int obs_format { get; init; }
    /// <summary>Base64 of the binary observation frame (upstream's layout). Null for forced steps.</summary>
    public string? frame { get; init; }
    public PolicyCandidatePayload[] candidates { get; init; } = Array.Empty<PolicyCandidatePayload>();
    /// <summary>Set when the step needs no network: execute this candidate.</summary>
    public int? forced_index { get; init; }
    /// <summary>Ids the vocabulary does not know (third-party content, or a newer game than the vocabulary).</summary>
    public int vocab_missing { get; init; }
}

internal static class PolicyService
{
    private const string LogPrefix = "[NekoComm.Policy]";
    public const string ActionName = "policy_act";
    private const string SkipChoice = "policy_skip_choice";

    /// <summary>Null when the policy seam is usable; otherwise why it is not.</summary>
    public static string? Unavailable { get; private set; } = "policy seam not initialized";

    private sealed record Live(PolicyDecision? Decision, string Kind, string? Reason = null, PolicyAction? Forced = null, string ForcedLabel = "", bool Wait = false);

    private sealed record Cached(string Id, string Signature, List<PolicyAction?> Actions);
    private static Cached? _last;

    // Room / rewards-set progress upstream keeps in RunSession and RewardsFlow.
    private static AbstractRoom? _shopBrowsedRoom;
    // Purchases that failed in the current room (upstream RunSession._failed): not offered again until
    // another purchase succeeds or the room changes.
    private static AbstractRoom? _failedRoom;
    private static readonly HashSet<object> Failed = new(ReferenceEqualityComparer.Instance);
    private static readonly ConditionalWeakTable<RewardsSet, HashSet<Reward>> Attempted = new();

    /// <summary>
    /// Identifies the vocabulary in use (first 16 hex digits of the SHA-256 of vocab.txt); null when none is
    /// loaded. The client compares it with its own copy: ids from two different vocabularies do not line up.
    /// </summary>
    public static string? DataId { get; private set; }

    // The vocabulary is game-derived data and is not shipped with the mod: the client that owns the network
    // downloads it together with the network's content tables and hands it over (POST /policy/data).
    private static readonly string[] DataFiles = ["vocab.txt", "aliases.txt", "no_downside_relics.txt"];

    private static string DataDirectory =>
        System.Environment.GetEnvironmentVariable("NEKO_POLICY_DATA") ?? ProjectSettings.GlobalizePath("user://NekoSpire/policy");

    public static void Initialize()
    {
        try
        {
            string dir = DataDirectory;
            if (!PolicyFrame.LoadData(dir))
            {
                DataId = null;
                Unavailable = $"policy data not installed (no vocab.txt in {dir}); the client sends it with POST /policy/data";
                Log.Info($"{LogPrefix} disabled: {Unavailable}");
                return;
            }
            DataId = Convert.ToHexString(System.Security.Cryptography.SHA256.HashData(File.ReadAllBytes(Path.Combine(dir, "vocab.txt"))), 0, 8).ToLowerInvariant();
            PolicyHooks.Install();
            Unavailable = null;
            Log.Info($"{LogPrefix} ready: vocabulary {Vocab.Count} ({DataId}), data {dir}");
        }
        catch (Exception ex)
        {
            Unavailable = "policy seam failed to initialize: " + ex.GetBaseException().Message;
            Log.Error($"{LogPrefix} {Unavailable}\n{ex}");
        }
    }

    /// <summary>POST /policy/data: store the vocabulary files the client sent and (re)load them. Game thread.</summary>
    public static object InstallData(IReadOnlyDictionary<string, string>? files)
    {
        if (files == null || !files.ContainsKey("vocab.txt") || files.Keys.Any(name => !DataFiles.Contains(name)))
            throw new ApiException(400, "invalid_request", "files must contain vocab.txt and may contain aliases.txt and no_downside_relics.txt.");
        string dir = DataDirectory;
        Directory.CreateDirectory(dir);
        foreach (string name in DataFiles)
        {
            string path = Path.Combine(dir, name);
            if (!files.TryGetValue(name, out var text)) { File.Delete(path); continue; }
            File.WriteAllBytes(path + ".new", new System.Text.UTF8Encoding(false).GetBytes(text));
            File.Move(path + ".new", path, overwrite: true);
        }
        _last = null;
        Initialize();
        return new { available = Unavailable == null, reason = Unavailable, data_id = DataId, vocabulary = Vocab.Count };
    }

    // ---------------------------------------------------------------- GET /policy/decision

    public static PolicyDecisionPayload BuildDecision(int? obsFormat)
    {
        if (Unavailable != null) return No(Unavailable);
        var run = RunManager.Instance.DebugOnlyGetState();
        var me = GameStateService.GetLocalPlayer(run);
        if (run == null || me == null) return No("no run in progress");
        try
        {
            PolicyFrame.Configure(obsFormat);
            Live live = Resolve(run, me);
            if (live.Forced != null)
                return Remember(run, me, live.Kind, [live.Forced], [("Forced", live.ForcedLabel)], frame: null, forced: 0);
            if (live.Decision == null) return No(live.Reason ?? "no decision", live.Wait);
            var d = live.Decision;
            var labels = d.Decision.Choices.Select(c => (c.Kind.ToString(), c.Label)).ToList();
            // One candidate: nothing to score (upstream's search steps these without the network as well).
            if (d.Decision.Choices.Count == 1 && d.Actions[0] != null)
                return Remember(run, me, live.Kind, d.Actions, labels, frame: null, forced: 0);
            var frame = PolicyFrame.Encode(d).Serialize();
            return Remember(run, me, live.Kind, d.Actions, labels, frame, forced: null);
        }
        catch (Exception ex)
        {
            Log.Warn($"{LogPrefix} decision failed: {ex}");
            return No("decision failed: " + ex.GetBaseException().Message);
        }
        finally
        {
            RewardsFlow.Reset();
            FakeScreens.CrystalSphere = null;
        }
    }

    private static PolicyDecisionPayload No(string reason, bool wait = false) =>
        new() { available = false, reason = reason, wait = wait, data_id = DataId };

    private static PolicyDecisionPayload Remember(RunState run, Player me, string kind, List<PolicyAction?> actions,
        List<(string Kind, string Label)> labels, byte[]? frame, int? forced)
    {
        string signature = Signature(kind, actions, labels);
        // Derived from the candidates alone, so polling an unchanged screen keeps returning the same id.
        string id = Convert.ToHexString(System.Security.Cryptography.SHA1.HashData(System.Text.Encoding.UTF8.GetBytes(signature)), 0, 8);
        _last = new Cached(id, signature, actions);
        return new PolicyDecisionPayload
        {
            available = true,
            data_id = DataId,
            decision_id = id,
            decision_kind = kind,
            character = me.Character.Id.Entry,
            ascension = run.AscensionLevel,
            multiplayer = run.Players.Count > 1,
            obs_format = Observation.FormatOverride ?? Observation.FormatFor(me.Character),
            frame = frame == null ? null : Convert.ToBase64String(frame),
            forced_index = forced,
            vocab_missing = Vocab.Missing.Count,
            candidates = actions.Select((a, i) => new PolicyCandidatePayload
            {
                index = i,
                kind = labels[i].Kind,
                label = labels[i].Label,
                executable = a != null,
                action = a?.Kind,
                card_index = a?.CardIndex,
                target_index = a?.TargetIndex,
                option_index = a?.OptionIndex,
            }).ToArray(),
        };
    }

    private static string Signature(string kind, List<PolicyAction?> actions, List<(string Kind, string Label)> labels) =>
        kind + "|" + string.Join(";", actions.Select((a, i) => $"{labels[i].Kind}:{labels[i].Label}>{a}"));

    // ---------------------------------------------------------------- live screen -> decision

    /// <summary>Same precedence as upstream RunSession.GetDecisionOnce: pending selections first, then combat, then the room.</summary>
    private static Live Resolve(RunState run, Player me)
    {
        if (GameStateService.GetOpenModal() != null) return new Live(null, "MODAL", "a modal is open");
        var screen = ActiveScreenContext.Instance.GetCurrentScreen();
        var combat = CombatManager.Instance.DebugOnlyGetState();

        if (GameStateService.TryGetCombatHandSelectionMetadata(screen, out var hand, out _) && hand != null)
            return HandSelect(screen, hand);
        switch (screen)
        {
            case NChooseACardSelectionScreen choose: return ChooseACard(choose);
            case NCardGridSelectionScreen grid: return GridSelect(grid);
            case NCardRewardSelectionScreen cardReward: return CardRewardScreen(cardReward);
            case NChooseABundleSelectionScreen: return new Live(null, "BUNDLE", "card bundles have no policy adapter yet");
            case NRewardsScreen rewards: return RewardsScreen(rewards, me);
        }
        if (screen?.GetType().Name.Contains("CrystalSphere") == true)
            return new Live(null, "MINIGAME", "the Crystal Sphere minigame has no policy adapter yet");

        if (CombatManager.Instance.IsInProgress)
        {
            if (screen is not NCombatRoom || combat == null || me.PlayerCombatState == null)
                return new Live(null, "COMBAT", "combat is running behind another screen");
            if (!GameStateService.CanEndTurn(screen, combat))
                return new Live(null, "COMBAT", "not the local player's play phase", Wait: true);
            if (!GameActionService.AreGameActionsSettled())
                return new Live(null, "COMBAT", "game actions are still resolving", Wait: true);
            return new Live(PolicyDecisionBuilder.Combat(me, combat), "COMBAT");
        }

        switch (screen)
        {
            case NEventRoom: return EventScreen(screen, me);
            case NRestSiteRoom: return RestScreen(screen, run, me);
            case NMerchantRoom or NMerchantInventory: return ShopScreen(screen, run, me);
            case NTreasureRoom or NTreasureRoomRelicCollection: return TreasureScreen(screen, me);
            case NMapScreen or NMapRoom: return MapScreen(screen, run, me);
        }
        return new Live(null, "UNKNOWN", $"no policy adapter for screen {screen?.GetType().Name ?? "null"}");
    }

    private static Live MapScreen(IScreenContext? screen, RunState run, Player me)
    {
        var nodes = GameStateService.GetAvailableMapNodes(screen, run);
        if (nodes.Count == 0) return new Live(null, "MAP", "no map node can be chosen right now");
        var d = PolicyDecisionBuilder.Map(run, me, p =>
        {
            for (int i = 0; i < nodes.Count; i++) if (nodes[i].Point.coord == p.coord) return i;
            return null;
        });
        return d == null ? new Live(null, "MAP", "no travelable map point") : new Live(d, "MAP");
    }

    private static Live RestScreen(IScreenContext? screen, RunState run, Player me)
    {
        bool canProceed = GameStateService.CanProceed(screen);
        var d = PolicyDecisionBuilder.RestSite(RunManager.Instance.RestSiteSynchronizer.GetLocalOptions(), me, alreadyChose: canProceed,
            o => GameStateService.RestOptionRequiresTarget(o, run, me));
        if (d != null) return new Live(d, "REST_SITE");
        return canProceed
            ? new Live(null, "REST_SITE", Forced: new PolicyAction("proceed"), ForcedLabel: "leave the rest site")
            : new Live(null, "REST_SITE", "no rest option is enabled and the room cannot be left yet");
    }

    private static Live EventScreen(IScreenContext? screen, Player me)
    {
        var ev = RunManager.Instance.EventSynchronizer.GetLocalEvent();
        if (ev == null) return new Live(null, "EVENT", "no local event");
        if (ev.IsFinished)
            return GameStateService.CanChooseEventOption(screen)
                ? new Live(null, "EVENT", Forced: new PolicyAction("choose_event_option", OptionIndex: 0), ForcedLabel: "leave the finished event")
                : new Live(null, "EVENT", "the event is finished and cannot be left yet");
        var d = PolicyDecisionBuilder.Event(ev, me);
        return d == null ? new Live(null, "EVENT", "the event offers no selectable option") : new Live(d, "EVENT");
    }

    private static Live ShopScreen(IScreenContext? screen, RunState run, Player me)
    {
        var inventoryScreen = GameStateService.GetMerchantInventoryScreen(screen);
        var inv = GameStateService.GetMerchantInventory(screen);
        if (screen is NMerchantInventory && inventoryScreen?.IsOpen == true && inv != null)
        {
            _shopBrowsedRoom = run.CurrentRoom;
            if (!ReferenceEquals(_failedRoom, run.CurrentRoom)) { _failedRoom = run.CurrentRoom; Failed.Clear(); }
            var cards = GameStateService.GetMerchantCardEntries(screen);
            var relics = GameStateService.GetMerchantRelicEntries(screen);
            var potions = GameStateService.GetMerchantPotionEntries(screen);
            PolicyAction? Entry(MerchantEntry e) => Map(e) is { } action ? action with { Subject = e } : null;
            PolicyAction? Map(MerchantEntry e) => e switch
            {
                MerchantCardEntry c when cards.ToList().IndexOf(c) is >= 0 and var i => new PolicyAction("buy_card", OptionIndex: i),
                MerchantRelicEntry r when relics.ToList().IndexOf(r) is >= 0 and var i => new PolicyAction("buy_relic", OptionIndex: i),
                MerchantPotionEntry p when potions.ToList().IndexOf(p) is >= 0 and var i => new PolicyAction("buy_potion", OptionIndex: i),
                MerchantCardRemovalEntry => new PolicyAction("remove_card_at_shop"),
                _ => null,
            };
            return new Live(PolicyDecisionBuilder.Shop(inv, me, Entry, new PolicyAction("close_shop_inventory"), Failed.Contains), "SHOP");
        }
        // Inventory closed: open it once per room so the network sees the shelves, then leave.
        if (!ReferenceEquals(_shopBrowsedRoom, run.CurrentRoom) && GameStateService.CanOpenShopInventory(screen))
            return new Live(null, "SHOP", Forced: new PolicyAction("open_shop_inventory"), ForcedLabel: "open the shop");
        return GameStateService.CanProceed(screen)
            ? new Live(null, "SHOP", Forced: new PolicyAction("proceed"), ForcedLabel: "leave the shop")
            : new Live(null, "SHOP", "the shop can neither be opened nor left right now");
    }

    private static Live TreasureScreen(IScreenContext? screen, Player me)
    {
        if (GameStateService.CanOpenChest(screen))
            return new Live(null, "TREASURE", Forced: new PolicyAction("open_chest"), ForcedLabel: "open the chest");
        if (GameStateService.CanChooseTreasureRelic(screen))
        {
            var relics = RunManager.Instance.TreasureRoomRelicSynchronizer.CurrentRelics!;
            if (relics.Count == 1 && RewardsFlow.IsNoDownside(relics[0]))
                return new Live(null, "TREASURE", Forced: new PolicyAction("choose_treasure_relic", OptionIndex: 0), ForcedLabel: "take " + relics[0].Id.Entry);
            // nekospire has no "skip the relic" action, so that candidate stays unexecutable.
            return new Live(PolicyDecisionBuilder.Treasure(relics, me, skip: null), "TREASURE");
        }
        return GameStateService.CanProceed(screen)
            ? new Live(null, "TREASURE", Forced: new PolicyAction("proceed"), ForcedLabel: "leave the treasure room")
            : new Live(null, "TREASURE", "the chest is neither openable nor finished");
    }

    private static Live RewardsScreen(NRewardsScreen screen, Player me)
    {
        if (Private<RewardsSet>(screen, "_rewardsSet") is not { } set) return new Live(null, "REWARDS", "rewards screen without a rewards set");
        var buttons = GameStateService.GetRewardButtons(screen);
        int? ButtonIndex(Reward r)
        {
            for (int i = 0; i < buttons.Count; i++) if (ReferenceEquals(buttons[i].Reward, r) && buttons[i].IsEnabled) return i;
            return null;
        }
        var attempted = Attempted.GetOrCreateValue(set);
        // Taken by upstream's engine without asking the network: gold, and relics on its no-downside list.
        foreach (var r in set.Rewards)
        {
            if (r.SuccessfullySelected || attempted.Contains(r) || ButtonIndex(r) is not int index) continue;
            if (r is GoldReward || (r is RelicReward { ParentRewardSet: null } rr && RewardsFlow.IsNoDownside(rr.Relic)))
                return new Live(null, "REWARDS", Forced: new PolicyAction("claim_reward", OptionIndex: index), ForcedLabel: "take " + r.GetType().Name);
        }
        var pending = new PendingRewards(set, attempted);
        RewardsFlow.Pending = pending;      // header field 30 reads it during encoding; cleared by BuildDecision
        bool canLeave = GameStateService.IsProceedButtonUsable(GameStateService.GetRewardProceedButton(screen));
        var d = PolicyDecisionBuilder.Rewards(pending, me, ButtonIndex, canLeave);
        // As upstream (RewardsFlow.Select: attempted.Add(choice) for EVERY choice): a reward that was tried is
        // not offered again, whether or not taking it worked. Without this a potion reward that can never be
        // taken (a relic that forbids potions) is picked forever.
        for (int i = 0; i < d.Actions.Count; i++)
            if (d.Actions[i] is { Kind: "claim_reward" } claim && d.Decision.Choices[i].A is Reward reward)
                d.Actions[i] = claim with { Subject = (set, reward) };
        return new Live(d, "REWARDS");
    }

    private static Live CardRewardScreen(NCardRewardSelectionScreen screen)
    {
        var options = Private<IReadOnlyList<CardCreationResult>>(screen, "_options");
        var extra = Private<IReadOnlyList<CardRewardAlternative>>(screen, "_extraOptions") ?? Array.Empty<CardRewardAlternative>();
        if (options == null || options.Count == 0) return new Live(null, "CARD_REWARD", "card reward screen without options");
        var cards = options.Select(o => o.Card).ToList();
        var holders = GameStateService.GetCardRewardOptions(screen);
        // The rewards screen underneath: the encoder also shows what else that set still offers.
        var stack = NOverlayStack.Instance;
        var under = stack == null ? null : GameStateService.FindDescendants<NRewardsScreen>(stack).LastOrDefault(GodotObject.IsInstanceValid);
        if (under != null && Private<RewardsSet>(under, "_rewardsSet") is { } set)
        {
            var open = set.Rewards.OfType<CardReward>().FirstOrDefault(r => !r.SuccessfullySelected && r.Cards.Any(c => ReferenceEquals(c, cards[0])));
            var attempted = Attempted.GetOrCreateValue(set);
            // As upstream: a card reward that was opened is not offered again if it is skipped.
            if (open != null) attempted.Add(open);
            RewardsFlow.ActiveSet = set; RewardsFlow.ActiveChoice = open; RewardsFlow.ActiveAttempted = attempted;
        }
        var d = PolicyDecisionBuilder.CardRewardChoice(cards, extra.Select(a => a.OptionId).ToList(), c =>
        {
            for (int i = 0; i < holders.Count; i++) if (ReferenceEquals(holders[i].CardModel, c)) return i;
            return null;
        });
        return new Live(d, "CARD_REWARD");
    }

    // ---------------------------------------------------------------- card selection screens

    private static SelectInfo? Info(CardSelectorPrefs prefs)
    {
        var call = PolicyHooks.LastSelect;
        if (call is not { HasInfo: true }) return null;
        return new SelectInfo(prefs.Prompt?.LocTable, prefs.Prompt?.LocEntryKey, call.Method, call.Enchantment, call.Source);
    }

    private static Func<CardModel, int?> HolderIndex(IScreenContext? screen)
    {
        var holders = GameStateService.GetDeckSelectionOptions(screen);
        return c =>
        {
            for (int i = 0; i < holders.Count; i++) if (ReferenceEquals(holders[i].CardModel, c)) return i;
            return null;
        };
    }

    private static Live Select(PendingCardChoice pc, IReadOnlyList<int> selected, Func<CardModel, int?> index, PolicyAction? confirm)
    {
        // Cancelling is never executed here, so "this room already used its one cancel" never becomes true.
        var d = PolicyDecisionBuilder.CardSelect(pc, selected, allowCancel: true, index, confirm, cancel: null);
        return d == null
            ? new Live(null, "CARD_SELECT", "nothing to choose on this selection screen (upstream resolves it without the network)")
            : new Live(d, "CARD_SELECT");
    }

    /// <summary>In-combat hand selection (exhaust / discard / upgrade ...): pick one at a time, then confirm.</summary>
    private static Live HandSelect(IScreenContext? screen, NPlayerHand hand)
    {
        if (Private<object>(hand, "_prefs") is not CardSelectorPrefs prefs) return new Live(null, "CARD_SELECT", "hand selection without prefs");
        var picked = Private<List<CardModel>>(hand, "_selectedCards") ?? new List<CardModel>();
        var remaining = GameStateService.GetDeckSelectionOptions(screen).Select(h => h.CardModel!).ToList();
        var pc = new PendingCardChoice(picked.Concat(remaining).ToList(), prefs.MinSelect, prefs.MaxSelect, prefs.Cancelable, Info(prefs));
        var confirm = GameStateService.CanConfirmSelection(screen) ? new PolicyAction("confirm_selection") : null;
        return Select(pc, Enumerable.Range(0, picked.Count).ToList(), HolderIndex(screen), confirm);
    }

    /// <summary>The three-card screen (FromChooseACardScreen): pick one, or skip when the screen allows it.</summary>
    private static Live ChooseACard(NChooseACardSelectionScreen screen)
    {
        var cards = Private<IReadOnlyList<CardModel>>(screen, "_cards");
        if (cards == null || cards.Count == 0) return new Live(null, "CARD_SELECT", "choose-a-card screen without cards");
        bool canSkip = Private<object>(screen, "_canSkip") is true;
        var pc = new PendingCardChoice(cards, canSkip ? 0 : 1, 1, false, new SelectInfo(null, null, nameof(MegaCrit.Sts2.Core.Commands.CardSelectCmd.FromChooseACardScreen), null));
        return Select(pc, Array.Empty<int>(), HolderIndex(screen), canSkip ? new PolicyAction(SkipChoice) : null);
    }

    /// <summary>Deck / grid selections (upgrade, remove, transform, enchant, pick from a pile). Single picks only.</summary>
    private static Live GridSelect(NCardGridSelectionScreen screen)
    {
        var cards = Private<IReadOnlyList<CardModel>>(screen, "_cards");
        if (cards == null || Private<object>(screen, "_prefs") is not CardSelectorPrefs prefs)
            return new Live(null, "CARD_SELECT", $"no policy adapter for {screen.GetType().Name}");
        if (prefs.MaxSelect != 1)
            return new Live(null, "CARD_SELECT", $"multi-card grid selections ({prefs.MinSelect}..{prefs.MaxSelect}) have no policy adapter yet");
        if (Private<System.Collections.IEnumerable>(screen, "_selectedCards")?.Cast<object>().Any() == true)
            return new Live(null, "CARD_SELECT", "a card is already selected on this screen");
        var pc = new PendingCardChoice(cards, prefs.MinSelect, prefs.MaxSelect, prefs.Cancelable, Info(prefs));
        // "Confirm with nothing selected" (optional picks) has no nekospire action.
        return Select(pc, Array.Empty<int>(), HolderIndex(screen), confirm: null);
    }

    /// <summary>A private field declared on the object's type or a base type; null when absent or of another type.</summary>
    private static T? Private<T>(object target, string name) where T : class =>
        AccessTools.Field(target.GetType(), name)?.GetValue(target) as T;

    // ---------------------------------------------------------------- POST /action policy_act

    public static async Task<ActionResponsePayload> ExecuteAsync(ActionRequest request)
    {
        var cached = _last;
        if (Unavailable != null || cached == null || request.command != cached.Id)
            throw new ApiException(409, "stale_policy_decision", "policy_act must echo the decision_id of the latest /policy/decision.",
                new { action = ActionName, decision_id = request.command, latest = cached?.Id }, retryable: true);
        if (request.option_index is not int index || index < 0 || index >= cached.Actions.Count)
            throw new ApiException(409, "invalid_target", "option_index is not a candidate of that decision.",
                new { action = ActionName, option_index = request.option_index, candidates = cached.Actions.Count });
        var action = cached.Actions[index]
            ?? throw new ApiException(409, "policy_candidate_not_executable", "nekospire has no action for that candidate.",
                new { action = ActionName, option_index = index });

        // The decision must still describe the screen: indices are positions in live lists.
        var run = RunManager.Instance.DebugOnlyGetState();
        var me = GameStateService.GetLocalPlayer(run);
        string now;
        try
        {
            Live live = run == null || me == null ? new Live(null, "NONE") : Resolve(run, me);
            now = live.Forced != null
                ? Signature(live.Kind, [live.Forced], [("Forced", live.ForcedLabel)])
                : live.Decision == null ? "" : Signature(live.Kind, live.Decision.Actions, live.Decision.Decision.Choices.Select(c => (c.Kind.ToString(), c.Label)).ToList());
        }
        finally
        {
            RewardsFlow.Reset();
        }
        if (now != cached.Signature)
            throw new ApiException(409, "stale_policy_decision", "The screen changed since that decision was built.",
                new { action = ActionName, decision_id = cached.Id }, retryable: true);
        _last = null;

        switch (action.Kind)
        {
            case PolicyAction.LeaveRewards:
                return await Click(GameStateService.GetRewardProceedButton(ActiveScreenContext.Instance.GetCurrentScreen()), "rewards proceed button");
            case PolicyAction.RewardAlternative:
                var container = ActiveScreenContext.Instance.GetCurrentScreen() is NCardRewardSelectionScreen s ? Private<Node>(s, "_rewardAlternativesContainer") : null;
                var alternatives = container?.GetChildren().OfType<NCardRewardAlternativeButton>().Where(GodotObject.IsInstanceValid).ToList();
                return await Click(alternatives != null && action.OptionIndex is int a && a < alternatives.Count ? alternatives[a] : null, "card reward alternative");
            case SkipChoice:
                return await Click(ActiveScreenContext.Instance.GetCurrentScreen() is NChooseACardSelectionScreen c ? Private<NButton>(c, "_skipButton") : null, "skip button");
            default:
                ActionResponsePayload result;
                if (action.Subject is ValueTuple<RewardsSet, Reward> tried) Attempted.GetOrCreateValue(tried.Item1).Add(tried.Item2);
                try
                {
                    result = await GameActionService.ExecuteAsync(new ActionRequest
                    {
                        action = action.Kind,
                        card_index = action.CardIndex,
                        target_index = action.TargetIndex,
                        option_index = action.OptionIndex,
                    });
                }
                catch (ApiException) when (action.Subject is MerchantEntry)
                {
                    // The game refused the purchase and nothing changed: stop offering it in this room.
                    Failed.Add(action.Subject);
                    Log.Info($"{LogPrefix} purchase failed, not offered again in this room: {action.Kind} #{action.OptionIndex}");
                    throw;
                }
                if (action.Subject is MerchantEntry) Failed.Clear();     // as upstream: a successful purchase changes the situation
                return new ActionResponsePayload { action = ActionName, status = result.status, stable = result.stable, message = $"{action.Kind}: {result.message}", state = result.state };
        }
    }

    private static async Task<ActionResponsePayload> Click(NButton? button, string what)
    {
        if (button == null || !GodotObject.IsInstanceValid(button) || !button.IsEnabled || !button.IsVisibleInTree())
            throw new ApiException(409, "invalid_action", $"The {what} is not available.", new { action = ActionName }, retryable: true);
        var before = ActiveScreenContext.Instance.GetCurrentScreen();
        button.ForceClick();
        bool stable = false;
        var deadline = DateTime.UtcNow + TimeSpan.FromSeconds(5);
        while (DateTime.UtcNow < deadline && NGame.Instance != null)
        {
            await NGame.Instance.ToSignal(NGame.Instance.GetTree(), SceneTree.SignalName.ProcessFrame);
            if (!ReferenceEquals(ActiveScreenContext.Instance.GetCurrentScreen(), before)) { stable = true; break; }
        }
        return new ActionResponsePayload
        {
            action = ActionName,
            status = stable ? "completed" : "pending",
            stable = stable,
            message = stable ? $"Pressed the {what}." : $"Pressed the {what}; the screen has not changed yet.",
            state = GameStateService.BuildStatePayload(),
        };
    }
}
