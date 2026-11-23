// Offline smoke test for the vendored CombatSolver engine. See SolverSmoke.csproj for the why.
//
// Flow: boot the game's core layer without Godot (upstream OfflineSearchHarness bootstrap) -> start a
// real run and enter a real combat -> every player turn, ask the BUILT nekospire.dll for a plan through
// its real seam (CombatSolverFacade.BuildPlanAsync, i.e. the exact code behind GET /solver/plan) -> play
// that turn's steps on the real combat -> repeat. Fails (exit 1) when the facade refuses a live
// Play-phase root, returns an unknown search_status, or recommends a step the game rejects.
using System.Reflection;
using System.Runtime.Loader;
using System.Text.Json;
using MegaCrit.Sts2.Core.Combat;
using MegaCrit.Sts2.Core.Commands;
using MegaCrit.Sts2.Core.Context;
using MegaCrit.Sts2.Core.Entities.Cards;
using MegaCrit.Sts2.Core.Entities.Creatures;
using MegaCrit.Sts2.Core.Entities.Players;
using MegaCrit.Sts2.Core.GameActions;
using MegaCrit.Sts2.Core.Logging;
using MegaCrit.Sts2.Core.Map;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Rooms;
using MegaCrit.Sts2.Core.Runs;
using MegaCrit.Sts2.Core.Saves;
using MegaCrit.Sts2.Core.Unlocks;
using OfflineSearchHarness;

namespace SolverSmoke;

// The entry-point class must not need sts2.dll to load: the runtime prepares it before upstream's
// AssemblyBootstrap module initializer has installed the assembly resolver (a generic method constrained
// to a game type, as in SmokeRun.Resolve, is enough to trip that).
internal static class Program
{
    private static int Main(string[] args) => SmokeRun.Start(args);
}

internal static class SmokeRun
{
    // Mirrors loop_runner.SOLVER_SEARCH_STATUSES (the plugin's admission gate).
    private static readonly HashSet<string> KnownStatuses =
    [
        "complete", "budget_exceeded", "boundary_shuffle", "boundary_nocards", "boundary_unsupportedeffect",
        "boundary_dynamicresolution", "boundary_pendingchoice", "boundary_eventdefeat", "boundary_turnlimit",
        "boundary_nodelimit",
    ];

    internal static int Start(string[] args)
    {
        Console.OutputEncoding = System.Text.Encoding.UTF8;
        Options options = Options.Parse(args);
        HarnessLog.TraceInit = options.Verbose;
        HarnessLog.VerboseGameLog = options.Verbose;
        try
        {
            return Run(options);
        }
        catch (Exception error)
        {
            Console.WriteLine($"SMOKE FAIL: {error}");
            return 1;
        }
    }

    private static int Run(Options options)
    {
        MainLoopContext loop = new();
        SynchronizationContext.SetSynchronizationContext(loop);
        GameBootstrap.ApplyGodotBypasses();
        GameBootstrap.SkipGodotNodeStaticConstructors();
        Console.WriteLine($"[smoke] game core: {GameBootstrap.InitializeStaticState()}");

        Seam seam = Seam.Load(options.ThinkSeconds);
        Console.WriteLine($"[smoke] nekospire: {seam.AssemblyPath}");

        Task enter = EnterCombatAsync(options);
        loop.RunUntilCompleted(enter, TimeSpan.FromSeconds(180), "enter combat");
        CombatState combat = OfflineCombat.WaitForPlayableCombat(loop);
        Player player = LocalContext.GetMe(combat)!;
        foreach (string potionId in options.Potions)
            InjectPotion(player, potionId);
        foreach (string spec in options.Powers)
            loop.RunUntilCompleted(ApplyPowerAsync(combat, player, spec), TimeSpan.FromSeconds(30), $"apply power {spec}");
        Console.WriteLine($"[smoke] root: {OfflineCombat.DescribeRoot(combat)}");

        int plans = 0;
        for (int turn = 1; turn <= options.Turns; turn++)
        {
            JsonElement plan = seam.BuildPlan(loop, combat, player);
            plans++;
            string summary = Describe(plan);
            Console.WriteLine($"[smoke] turn {player.PlayerCombatState!.TurnNumber} plan: {summary}");
            Validate(plan);

            foreach (JsonElement step in CurrentTurnSteps(plan))
            {
                string kind = step.GetProperty("kind").GetString()!;
                if (kind == "end_turn")
                    break;
                if (step.GetProperty("choice_card_id").ValueKind == JsonValueKind.String)
                {
                    // Card-choice steps need a selector driven by the plan; out of scope for a smoke test.
                    Console.WriteLine($"[smoke]   stop before choice step {step.GetProperty("card_id")}");
                    break;
                }
                Execute(loop, combat, player, step);
                if (!CombatManager.Instance.IsInProgress)
                    break;
            }
            if (!CombatManager.Instance.IsInProgress)
            {
                Console.WriteLine($"[smoke] combat ended on turn {turn}: player_hp={player.Creature.CurrentHp}");
                break;
            }
            EndTurn(loop, combat, player);
            if (!CombatManager.Instance.IsInProgress)
            {
                Console.WriteLine($"[smoke] combat ended after turn {turn}: player_hp={player.Creature.CurrentHp}");
                break;
            }
            Console.WriteLine($"[smoke]   after turn: {OfflineCombat.DescribeRoot(combat)}");
        }

        Console.WriteLine($"SMOKE OK plans={plans}");
        return 0;
    }

    private static async Task EnterCombatAsync(Options options)
    {
        CharacterModel character = Resolve(ModelDb.AllCharacters, options.Character, "character");
        EncounterModel encounter = Resolve(ModelDb.All.OfType<EncounterModel>(), options.Encounter, "encounter");

        UnlockState unlockState = SaveManager.Instance.GenerateUnlockStateFromProgress();
        // Teammates use the game's own "fake multiplayer": a single-player net service with several
        // players. They never act, and ending the turn does not wait for them.
        List<Player> players = [Player.CreateForNewRun(character, unlockState, 1uL)];
        for (int index = 0; index < options.Teammates.Length; index++)
        {
            players.Add(Player.CreateForNewRun(
                Resolve(ModelDb.AllCharacters, options.Teammates[index], "teammate character"),
                unlockState,
                (ulong)(index + 2)));
        }
        RunState runState = RunState.CreateForNewRun(
            players,
            ActModel.GetDefaultList().Select(act => act.ToMutable()).ToList(),
            [],
            GameMode.Standard,
            false,
            options.Ascension,
            options.Seed);
        RunManager.Instance.SetUpNewSingleplayer(runState, shouldSave: false);
        await RunManager.Instance.FinalizeStartingRelics();
        RunManager.Instance.Launch();
        await RunManager.Instance.EnterAct(0, doTransition: false);

        Player player = LocalContext.GetMe(runState)!;
        foreach (Player teammate in runState.Players)
        {
            if (ReferenceEquals(teammate, player))
                continue;
            // An idle teammate must stay inert and alive: its draws would consume the shared Shuffle RNG
            // (the engine does not model teammates' piles), and in fake multiplayer a dead teammate makes
            // the game end the whole player turn immediately, skipping the local player.
            foreach (CardModel card in teammate.Deck.Cards.ToArray())
            {
                teammate.Deck.RemoveInternal(card, silent: true);
                runState.RemoveCard(card);
            }
            teammate.Creature.SetMaxHpInternal(900_000m);
            teammate.Creature.SetCurrentHpInternal(900_000m);
        }
        foreach (string relicId in options.Relics)
            await RelicCmd.Obtain(Resolve(ModelDb.AllRelics, relicId, "relic").ToMutable(), player);
        foreach (string cardId in options.Cards)
        {
            CardModel card = runState.CreateCard(Resolve(ModelDb.AllCards, cardId, "card"), player);
            if (!(await CardPileCmd.Add(card, PileType.Deck)).success)
                throw new InvalidOperationException($"game refused to add {cardId} to the deck");
        }

        RoomType roomType = options.RoomType;
        await RunManager.Instance.EnterRoomDebug(
            roomType,
            roomType switch { RoomType.Elite => MapPointType.Elite, RoomType.Boss => MapPointType.Boss, _ => MapPointType.Unassigned },
            encounter.ToMutable());
    }

    private static void InjectPotion(Player player, string potionId)
    {
        PotionModel potion = Resolve(ModelDb.AllPotions, potionId, "potion").ToMutable();
        int slot = Enumerable.Range(0, player.PotionSlots.Count)
            .FirstOrDefault(index => player.GetPotionAtSlotIndex(index) == null, -1);
        if (slot < 0 || !player.AddPotionInternal(potion, slot, silent: false).success)
            throw new InvalidOperationException($"cannot inject potion {potionId}");
    }

    /// <summary>"PowerId[:amount]" applies to the player, "enemy:PowerId[:amount]" to the first enemy.
    /// Used to put powers whose private state the root capture reads (GetInternalData) on the board.</summary>
    private static async Task ApplyPowerAsync(CombatState combat, Player player, string spec)
    {
        string[] parts = spec.Split(':');
        bool onEnemy = parts[0].Equals("enemy", StringComparison.OrdinalIgnoreCase);
        string powerId = parts[onEnemy ? 1 : 0];
        decimal amount = parts.Length > (onEnemy ? 2 : 1) ? decimal.Parse(parts[^1]) : 1m;
        Creature owner = onEnemy ? combat.Enemies[0] : player.Creature;
        PowerModel power = Resolve(ModelDb.AllPowers, powerId, "power").ToMutable();
        await PowerCmd.Apply(
            new MegaCrit.Sts2.Core.GameActions.Multiplayer.BlockingPlayerChoiceContext(),
            power, owner, amount, onEnemy ? owner : player.Creature, null);
    }

    private static IEnumerable<JsonElement> CurrentTurnSteps(JsonElement plan)
    {
        JsonElement line = plan.GetProperty("line");
        return line.GetArrayLength() == 0 ? [] : line[0].GetProperty("steps").EnumerateArray().ToArray();
    }

    private static string Describe(JsonElement plan)
    {
        string steps = string.Join(" > ", CurrentTurnSteps(plan).Select(step =>
            step.GetProperty("kind").GetString() == "end_turn"
                ? "end"
                : $"{step.GetProperty("card_id").GetString()}" +
                  (step.GetProperty("target_index").ValueKind == JsonValueKind.Number
                      ? $"@{step.GetProperty("target_index").GetInt32()}" : "")));
        string[] warnings = plan.GetProperty("warnings").EnumerateArray().Select(w => w.GetString()!).ToArray();
        return $"in_combat={plan.GetProperty("in_combat").GetBoolean()} status={plan.GetProperty("search_status")} " +
               $"ms={plan.GetProperty("budget_ms")} nodes={plan.GetProperty("nodes_expanded")} " +
               $"horizon={plan.GetProperty("horizon")} win_prob={plan.GetProperty("win_prob")} " +
               $"fingerprint={plan.GetProperty("state_fingerprint")} steps=[{steps}] " +
               $"warnings=[{string.Join(" | ", warnings.Select(w => w.Length > 300 ? w[..300] + "..." : w))}]";
    }

    private static void Validate(JsonElement plan)
    {
        if (!plan.GetProperty("in_combat").GetBoolean())
            throw new InvalidOperationException("facade refused a live Play-phase root (in_combat=false) — see warnings above");
        string? status = plan.GetProperty("search_status").GetString();
        if (status == null || !KnownStatuses.Contains(status))
            throw new InvalidOperationException($"search_status '{status}' is outside the plugin's accepted set");
        if (!CurrentTurnSteps(plan).Any())
            throw new InvalidOperationException("plan has no steps for the current turn");
    }

    private static void Execute(MainLoopContext loop, CombatState combat, Player player, JsonElement step)
    {
        string kind = step.GetProperty("kind").GetString()!;
        string id = step.GetProperty("card_id").GetString()!;
        Creature? target = step.GetProperty("target_index").ValueKind == JsonValueKind.Number
            ? combat.Enemies[step.GetProperty("target_index").GetInt32()]
            : null;
        if (kind == "play_card")
        {
            // Same resolution the consumers use: the first hand card with the plan's card id.
            CardModel card = player.PlayerCombatState!.Hand.Cards.FirstOrDefault(c => c.Id.Entry == id)
                ?? throw new InvalidOperationException($"plan plays {id} but it is not in the live hand");
            if (!card.TryManualPlay(target))
            {
                card.CanPlay(out UnplayableReason reason, out AbstractModel? preventer);
                throw new InvalidOperationException(
                    $"game rejected plan step play {id} target={target?.Monster?.Id.Entry ?? "-"} reason={reason} preventer={preventer?.Id.Entry ?? "-"}");
            }
        }
        else if (kind == "use_potion")
        {
            PotionModel potion = Enumerable.Range(0, player.PotionSlots.Count)
                .Select(player.GetPotionAtSlotIndex)
                .FirstOrDefault(p => p != null && p.Id.Entry == id)
                ?? throw new InvalidOperationException($"plan uses potion {id} but the player does not hold it");
            potion.EnqueueManualUse(target);
        }
        else
        {
            throw new InvalidOperationException($"unknown step kind {kind}");
        }
        Settle(loop, combat, player, turnBefore: null);
        Console.WriteLine($"[smoke]   did {kind} {id}{(target != null ? " -> " + target.Monster?.Id.Entry : "")}");
    }

    private static void EndTurn(MainLoopContext loop, CombatState combat, Player player)
    {
        int turnBefore = player.PlayerCombatState!.TurnNumber;
        CombatManager.Instance.OnEndedTurnLocally();
        RunManager.Instance.ActionQueueSynchronizer.RequestEnqueue(new EndPlayerTurnAction(player, turnBefore));
        Settle(loop, combat, player, turnBefore);
    }

    /// <summary>Pump until the action queue is idle and the combat is back at a stable Play-phase
    /// boundary (or over). Same stability condition as upstream RealCombatDriver.RunRealCombatAction.</summary>
    private static void Settle(MainLoopContext loop, CombatState combat, Player player, int? turnBefore)
    {
        ActionExecutor executor = RunManager.Instance.ActionExecutor;
        DateTime deadline = DateTime.UtcNow.AddSeconds(60);
        while (true)
        {
            loop.DrainPending();
            Task finished = executor.FinishedExecutingActions();
            if (finished.IsFaulted)
                finished.GetAwaiter().GetResult();
            bool idle = finished.IsCompleted && !executor.IsRunning;
            if (idle && !CombatManager.Instance.IsInProgress && !CombatManager.Instance.IsEnding)
                return;
            if (idle
                && CombatManager.Instance.IsInProgress
                && !CombatManager.Instance.IsOverOrEnding
                && combat.CurrentSide == CombatSide.Player
                && player.PlayerCombatState is { Phase: PlayerTurnPhase.Play } pcs
                && !CombatManager.Instance.PlayerActionsDisabled
                && !CombatManager.Instance.IsPlayerReadyToEndTurn(player)
                && (turnBefore == null || pcs.TurnNumber > turnBefore))
            {
                loop.DrainPending();
                if (!executor.IsRunning && executor.FinishedExecutingActions().IsCompleted)
                    return;
            }
            if (DateTime.UtcNow > deadline)
                throw new TimeoutException(
                    $"combat did not settle: side={combat.CurrentSide} phase={player.PlayerCombatState?.Phase} " +
                    $"turn={player.PlayerCombatState?.TurnNumber} running={executor.IsRunning}");
            loop.PumpOnce(1);
        }
    }

    private static TModel Resolve<TModel>(IEnumerable<TModel> candidates, string input, string kind)
        where TModel : AbstractModel
    {
        TModel[] matches = candidates
            .Where(candidate => candidate.Id.Entry.Equals(input, StringComparison.OrdinalIgnoreCase)
                || candidate.GetType().Name.Equals(input, StringComparison.OrdinalIgnoreCase))
            .DistinctBy(candidate => candidate.Id)
            .Take(2)
            .ToArray();
        return matches.Length == 1
            ? matches[0]
            : throw new InvalidOperationException($"{kind} '{input}' matched {matches.Length} models");
    }
}

/// <summary>The built nekospire.dll, driven through its real internal seam by reflection (the DLL is
/// the shipping artifact: no InternalsVisibleTo, no publicizer).</summary>
internal sealed class Seam
{
    private readonly MethodInfo _buildPlan;
    private readonly object _options;

    public string AssemblyPath { get; }

    private Seam(string path, MethodInfo buildPlan, object options)
    {
        AssemblyPath = path;
        _buildPlan = buildPlan;
        _options = options;
    }

    public static Seam Load(double thinkSeconds)
    {
        string path = Path.GetFullPath((string?)AppContext.GetData("NekospireDll")
            ?? throw new InvalidOperationException("NekospireDll runtime option missing"));
        if (!File.Exists(path))
            throw new FileNotFoundException("build the mod first: dotnet build game_mod/nekospire/nekospire.csproj -c Release", path);
        Assembly assembly = AssemblyLoadContext.Default.LoadFromAssemblyPath(path);

        // What CombatSolverRuntime.Install() does for the engine, minus Harmony.PatchAll (nekospire's own
        // patches target Godot UI nodes that do not exist offline).
        Type entry = assembly.GetType("CombatSolver.Entry", throwOnError: true)!;
        entry.GetProperty("Logger", BindingFlags.Static | BindingFlags.Public)!
            .SetValue(null, new Logger("nekospire-solver", LogType.Generic));

        Type optionsType = assembly.GetType("NekoComm.Game.SolverFacadeOptions", throwOnError: true)!;
        object options = Activator.CreateInstance(optionsType)!;
        optionsType.GetProperty("MaxThinkSeconds")!.SetValue(options, thinkSeconds);

        Type facade = assembly.GetType("NekoComm.Game.CombatSolverFacade", throwOnError: true)!;
        MethodInfo buildPlan = facade.GetMethod("BuildPlanAsync", BindingFlags.Static | BindingFlags.Public | BindingFlags.NonPublic)
            ?? throw new MissingMethodException("CombatSolverFacade.BuildPlanAsync");
        return new Seam(path, buildPlan, options);
    }

    public JsonElement BuildPlan(MainLoopContext loop, CombatState combat, Player player)
    {
        Task task = (Task)_buildPlan.Invoke(null, [combat, player, _options, CancellationToken.None])!;
        loop.RunUntilCompleted(task, TimeSpan.FromSeconds(120), "CombatSolverFacade.BuildPlanAsync");
        object payload = task.GetType().GetProperty("Result")!.GetValue(task)!;
        return JsonSerializer.SerializeToElement(payload, payload.GetType());
    }
}

internal sealed record Options
{
    public string Character { get; init; } = "IRONCLAD";
    public string Encounter { get; init; } = "";
    public string Seed { get; init; } = "SMOKE1";
    public int Ascension { get; init; }
    public RoomType RoomType { get; init; } = RoomType.Monster;
    public string[] Relics { get; init; } = [];
    public string[] Cards { get; init; } = [];
    public string[] Potions { get; init; } = [];
    public string[] Powers { get; init; } = [];
    public string[] Teammates { get; init; } = [];
    public int Turns { get; init; } = 3;
    public double ThinkSeconds { get; init; } = 2;
    public bool Verbose { get; init; }

    public static Options Parse(string[] args)
    {
        Options options = new();
        for (int index = 0; index < args.Length; index++)
        {
            string Next() => index + 1 < args.Length ? args[++index] : throw new ArgumentException($"{args[index]} needs a value");
            string[] List() => Next().Split(',', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries);
            options = args[index] switch
            {
                "--character" => options with { Character = Next() },
                "--encounter" => options with { Encounter = Next() },
                "--seed" => options with { Seed = Next() },
                "--ascension" => options with { Ascension = int.Parse(Next()) },
                "--room" => options with { RoomType = Enum.Parse<RoomType>(Next(), ignoreCase: true) },
                "--relics" => options with { Relics = List() },
                "--cards" => options with { Cards = List() },
                "--potions" => options with { Potions = List() },
                "--powers" => options with { Powers = List() },
                "--teammates" => options with { Teammates = List() },
                "--turns" => options with { Turns = int.Parse(Next()) },
                "--think" => options with { ThinkSeconds = double.Parse(Next(), System.Globalization.CultureInfo.InvariantCulture) },
                "--verbose" => options with { Verbose = true },
                _ => throw new ArgumentException($"unknown option {args[index]}"),
            };
        }
        if (options.Encounter.Length == 0)
            throw new ArgumentException("--encounter <id> is required (e.g. an EncounterModel type name)");
        return options;
    }
}
