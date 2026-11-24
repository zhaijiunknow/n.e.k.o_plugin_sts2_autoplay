// Offline fidelity check for the AlphaDefect policy path. See PolicyFidelity.csproj for the why.
//
// Upstream's engine serves its pipe protocol as usual. A Harmony postfix on its Observation.Build hands
// the same live game state to the built nekospire.dll, which encodes it through its own decision builders
// and its own (vendored, patched) copy of the encoder; the two frames must be identical.
extern alias neko;

using System.Reflection;
using System.Runtime.CompilerServices;
using System.Text.Json;
using HarmonyLib;
using MegaCrit.Sts2.Core.Entities.Merchant;
using MegaCrit.Sts2.Core.Models.Events;
using MegaCrit.Sts2.Core.Rewards;
using MegaCrit.Sts2.Core.Rooms;
using MegaCrit.Sts2.Core.Runs;
using Sts2Headless;
using NekoFrame = neko::NekoComm.Policy.PolicyFrame;
using Probe = neko::NekoComm.Policy.PolicyFidelityProbe;

namespace PolicyFidelity;

internal static class Entry
{
    // Must not touch a game type: the assembly resolver is installed by GameAssemblies.Register().
    private static int Main(string[] args)
    {
        Console.SetOut(Console.Error);      // stdout carries protocol frames only
        GameAssemblies.Register();
        return Serve();
    }

    [MethodImpl(MethodImplOptions.NoInlining)]
    private static int Serve()
    {
        Bootstrap.Init();
        Vocab.Load();
        Fidelity.Install();
        Console.Error.WriteLine($"[fidelity] ready, vocabulary {Vocab.Count}");
        try { Server.Run(); }
        finally { Fidelity.WriteSummary(); }
        return 0;
    }
}

internal static class Fidelity
{
    // Header ints the network never reads and that legitimately differ: combats won in this process,
    // steps taken in this run, unknown vocabulary keys seen by this process.
    private static readonly int[] MaskedHeaderInts = [12, 13, 14];
    private const BindingFlags Private = BindingFlags.Instance | BindingFlags.NonPublic;

    private sealed class Tally { public int Frames, Equal, Skipped; public List<string> Samples = new(); }
    private static readonly SortedDictionary<string, Tally> Tallies = new();
    private static string _logPath = "";
    private static int _mismatchLines;

    public static void Install()
    {
        _logPath = Environment.GetEnvironmentVariable("POLICY_FIDELITY_LOG") ?? "";
        string data = AppContext.GetData("NekoPolicyData") as string ?? "";
        if (!NekoFrame.LoadData(data)) throw new InvalidOperationException($"nekospire policy data not found in {data}");
        var harmony = new Harmony("nekospire.policy-fidelity");
        // The mod's own observation hooks (draw-pile top, which CardSelectCmd opened a selection, potion in use).
        Probe.InstallHooks();
        harmony.Patch(typeof(Observation).GetMethod(nameof(Observation.Build))!,
            postfix: new HarmonyMethod(typeof(Fidelity).GetMethod(nameof(AfterBuild), BindingFlags.NonPublic | BindingFlags.Static)));
    }

    private static void AfterBuild(RunSession run, Decision d, Observation __result)
    {
        if (d.Kind is DecisionKind.GameOver or DecisionKind.Stuck || Observation.Privileged) return;
        var tally = Tallies.TryGetValue(d.Kind.ToString(), out var t) ? t : Tallies[d.Kind.ToString()] = new Tally();
        tally.Frames++;
        byte[]? got;
        string? skip = null;
        try
        {
            NekoFrame.Configure(Observation.FormatOverride, Observation.RouteSummary);
            got = Encode(run, d, out skip);
        }
        catch (Exception e)
        {
            Report(tally, d, $"nekospire threw: {e.GetBaseException().GetType().Name}: {e.GetBaseException().Message}");
            return;
        }
        if (skip != null) { tally.Skipped++; return; }
        if (got == null) { Report(tally, d, "nekospire produced no decision"); return; }
        byte[] want = __result.Serialize();
        Mask(want); Mask(got);
        if (want.AsSpan().SequenceEqual(got)) { tally.Equal++; return; }
        Report(tally, d, Describe(__result, want, got));
    }

    private static byte[]? Encode(RunSession run, Decision d, out string? skip)
    {
        skip = null;
        if (FailedCount(run) > 0) { skip = "engine-side failed options"; return null; }
        switch (d.Kind)
        {
            case DecisionKind.Combat:
                // Loop cut: the engine reduced the decision to "end turn" only; nekospire does not replicate it.
                if (d.Choices.Count == 1 && RunSession.LoopCut) { skip = "loop cut"; return null; }
                return Probe.Combat();
            case DecisionKind.Map: return Probe.Map();
            case DecisionKind.RestSite: return Probe.Rest(Field<bool>(run, "_restChosen"));
            case DecisionKind.Event: return Probe.Event();
            case DecisionKind.Treasure: return Probe.Treasure();
            case DecisionKind.Shop:
                if (RunSession.State.CurrentRoom is EventRoom { CanonicalEvent: FakeMerchant }) { skip = "fake merchant"; return null; }
                return Probe.Shop(RunManager.Instance.MerchantSynchronizer.GetLocalInventory());
            case DecisionKind.Rewards:
                var pending = RewardsFlow.Pending!;
                return Probe.Rewards(pending.Set, Attempted(pending));
            case DecisionKind.CardReward:
                var cr = FakeScreens.CardReward!;
                return Probe.CardReward(RewardsFlow.ActiveSet, RewardsFlow.ActiveChoice, RewardsFlow.ActiveAttempted,
                    cr.Cards.Select(c => c.Card).ToList(), cr.Alternatives.Select(a => a.OptionId).ToList());
            case DecisionKind.CardSelect:
                var pc = run.Selecting!;
                bool allowCancel = !ReferenceEquals(Field<AbstractRoom?>(run, "_cancelRoom"), RunSession.State.CurrentRoom);
                return Probe.CardSelect(pc.Options, pc.Min, pc.Max, pc.Cancelable, pc.Info?.PromptTable, pc.Info?.PromptKey,
                    run.SelectedIndices.ToList(), allowCancel);
            default:
                skip = "not implemented in nekospire";
                return null;
        }
    }

    private static T Field<T>(RunSession run, string name) => (T)typeof(RunSession).GetField(name, Private)!.GetValue(run)!;

    private static int FailedCount(RunSession run) => ((HashSet<object>)typeof(RunSession).GetField("_failed", Private)!.GetValue(run)!).Count;

    /// <summary>The primary-constructor capture of PendingRewards(set, attempted).</summary>
    private static IReadOnlySet<Reward> Attempted(PendingRewards pending) =>
        (IReadOnlySet<Reward>)typeof(PendingRewards).GetFields(Private).First(f => typeof(IReadOnlySet<Reward>).IsAssignableFrom(f.FieldType)).GetValue(pending)!;

    private static void Mask(byte[] frame)
    {
        foreach (int i in MaskedHeaderInts) Array.Clear(frame, 4 * i, 4);
    }

    /// <summary>Where the two frames first differ, in terms of the frame layout.</summary>
    private static string Describe(Observation o, byte[] want, byte[] got)
    {
        int tokens = o.NumTokens, cands = o.NumCands;
        int gotTokens = BitConverter.ToInt32(got, 8), gotCands = BitConverter.ToInt32(got, 12);
        if (tokens != gotTokens || cands != gotCands)
            return $"shape differs: tokens {tokens} vs {gotTokens}, candidates {cands} vs {gotCands}";
        int tokFeats = tokens == 0 ? 0 : o.TF.Count / tokens, candFeats = cands == 0 ? 0 : o.CF.Count / cands;
        int at = 0;
        while (at < want.Length && at < got.Length && want[at] == got[at]) at++;
        if (at >= want.Length || at >= got.Length) return $"length differs: {want.Length} vs {got.Length}";
        int word = at / 4, header = Observation.HeaderInts;
        int ti = header, tf = ti + tokens * Observation.TokInts, tp = tf + tokens * tokFeats, ci = tp + tokens * Observation.TokPriv,
            cf = ci + cands * Observation.CandInts, end = cf + cands * candFeats;
        string where =
            word < ti ? $"header[{word}]" :
            word < tf ? $"record {(word - ti) / Observation.TokInts} int {(word - ti) % Observation.TokInts}" :
            word < tp ? $"record {(word - tf) / tokFeats} (kind {o.TI[(word - tf) / tokFeats * Observation.TokInts]}, id {o.TI[(word - tf) / tokFeats * Observation.TokInts + 1]}) feature {(word - tf) % tokFeats}" :
            word < ci ? $"record {(word - tp) / Observation.TokPriv} privileged {(word - tp) % Observation.TokPriv}" :
            word < cf ? $"candidate {(word - ci) / Observation.CandInts} int {(word - ci) % Observation.CandInts}" :
            word < end ? $"candidate {(word - cf) / candFeats} feature {(word - cf) % candFeats}" : "trailer";
        bool isFloat = (word >= tf && word < ci) || (word >= cf && word < end);
        string a = isFloat ? BitConverter.ToSingle(want, word * 4).ToString("R") : BitConverter.ToInt32(want, word * 4).ToString();
        string b = isFloat ? BitConverter.ToSingle(got, word * 4).ToString("R") : BitConverter.ToInt32(got, word * 4).ToString();
        return $"{where}: upstream {a}, nekospire {b}";
    }

    private static void Report(Tally tally, Decision d, string what)
    {
        var s = RunSession.State;
        string line = $"{d.Kind} act {s.CurrentActIndex + 1} floor {s.TotalFloor} [{d.Context}]: {what}";
        if (tally.Samples.Count < 12) tally.Samples.Add(line);
        if (_mismatchLines++ < 200) Console.Error.WriteLine("[fidelity] MISMATCH " + line);
    }

    public static void WriteSummary()
    {
        var summary = Tallies.ToDictionary(kv => kv.Key, kv => new
        {
            frames = kv.Value.Frames, equal = kv.Value.Equal, skipped = kv.Value.Skipped,
            mismatched = kv.Value.Frames - kv.Value.Equal - kv.Value.Skipped, samples = kv.Value.Samples,
        });
        string json = JsonSerializer.Serialize(summary, new JsonSerializerOptions { Encoder = System.Text.Encodings.Web.JavaScriptEncoder.UnsafeRelaxedJsonEscaping });
        Console.Error.WriteLine("[fidelity] SUMMARY " + json);
        if (_logPath.Length > 0) File.AppendAllText(_logPath, json + "\n");
    }
}
