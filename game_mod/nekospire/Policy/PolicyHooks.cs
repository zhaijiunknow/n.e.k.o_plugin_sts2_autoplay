// Observation-only Harmony hooks the AlphaDefect encoder depends on. None of them changes game state.
//
// In upstream's headless engine the same facts come from its fake UI layer (GameShim / HeadlessFixes):
//   * which CardSelectCmd method opened a card selection, with which prompt / enchantment / source —
//     the selection screen itself does not say whether it is an upgrade, a removal or an exhaust;
//   * which potion is being used while its effect resolves (the source of a potion-opened selection);
//   * which cards the player knows to be on top of the draw pile (upstream's Observation.DrawTop).
using System.Reflection;
using HarmonyLib;
using MegaCrit.Sts2.Core.CardSelection;
using MegaCrit.Sts2.Core.Commands;
using MegaCrit.Sts2.Core.Models;
using Sts2Headless;

namespace NekoComm.Policy;

internal static class PolicyHooks
{
    /// <summary>The most recent CardSelectCmd call, as upstream's HeadlessFixes.SetCancelable records it.</summary>
    /// <param name="HasInfo">False for calls upstream leaves without a SelectInfo (no CardSelectorPrefs parameter).</param>
    internal sealed record SelectCall(string Method, bool HasInfo, EnchantmentModel? Enchantment, AbstractModel? Source);

    public static SelectCall? LastSelect { get; private set; }
    public static bool Installed { get; private set; }

    static int _prefsDepth;

    public static void Install()
    {
        if (Installed) return;
        var h = new Harmony("nekospire.policy");
        Observation.DrawTop.Install(h);
        foreach (var m in typeof(CardSelectCmd).GetMethods(BindingFlags.Public | BindingFlags.Static)
                     .Where(m => !m.ContainsGenericParameters && m.Name.StartsWith("From", StringComparison.Ordinal)))
        {
            bool prefs = m.GetParameters().Any(p => p.ParameterType == typeof(CardSelectorPrefs));
            if (prefs) h.Patch(m, prefix: Own(nameof(BeforeSelectWithPrefs)), finalizer: Own(nameof(AfterSelectWithPrefs)));
            else h.Patch(m, prefix: Own(nameof(BeforeSelectWithoutPrefs)));
        }
        h.Patch(AccessTools.Method(typeof(PotionModel), nameof(PotionModel.OnUseWrapper)) ?? throw new MissingMethodException("PotionModel.OnUseWrapper"),
            prefix: Own(nameof(PotionStart)), postfix: Own(nameof(PotionWrap)));
        Installed = true;
    }

    static HarmonyMethod Own(string name) =>
        new(typeof(PolicyHooks).GetMethod(name, BindingFlags.NonPublic | BindingFlags.Static) ?? throw new MissingMethodException(nameof(PolicyHooks), name));

    static void BeforeSelectWithPrefs(object[] __args, MethodBase __originalMethod)
    {
        _prefsDepth++;
        var ps = __originalMethod.GetParameters();
        int si = Array.FindIndex(ps, q => q.Name == "source");
        LastSelect = new SelectCall(__originalMethod.Name, true, __args.OfType<EnchantmentModel>().FirstOrDefault(),
            si >= 0 && si < __args.Length ? __args[si] as AbstractModel : null);
    }

    static Exception? AfterSelectWithPrefs(Exception? __exception)
    {
        if (_prefsDepth > 0) _prefsDepth--;
        return __exception;
    }

    /// <summary>
    /// Calls without a CardSelectorPrefs parameter. Upstream records a SelectInfo only for the three-card
    /// screen; the others leave whatever an enclosing prefs call recorded, or nothing.
    /// </summary>
    static void BeforeSelectWithoutPrefs(MethodBase __originalMethod)
    {
        if (__originalMethod.Name == nameof(CardSelectCmd.FromChooseACardScreen))
            LastSelect = new SelectCall(__originalMethod.Name, true, null, null);
        else if (_prefsDepth == 0)
            LastSelect = new SelectCall(__originalMethod.Name, false, null, null);
    }

    static void PotionStart(PotionModel __instance) => AgentCardSelector.UsingPotion = __instance;

    static void PotionWrap(PotionModel __instance, ref Task __result) => __result = PotionDone(__instance, __result);

    static async Task PotionDone(PotionModel p, Task t)
    {
        try { await t; }
        finally { if (AgentCardSelector.UsingPotion == p) AgentCardSelector.UsingPotion = null; }
    }
}
