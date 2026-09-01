namespace CombatSolver;

internal sealed class IncompatibleGameplayModException : NotSupportedException
{
    public string ModId { get; }
    public string ModName { get; }
    public string SubscriberType { get; }
    public string Scope { get; }

    public IncompatibleGameplayModException(
        string modId,
        string modName,
        string subscriberType,
        string scope)
        : base(
            $"Unsupported gameplay ModHelper {scope} subscriber {subscriberType} " +
            $"from mod {DescribeMod(modName, modId)}.")
    {
        ModId = modId;
        ModName = modName;
        SubscriberType = subscriberType;
        Scope = scope;
    }

    public string PlayerFacingModName => DescribeMod(ModName, ModId);

    private static string DescribeMod(string modName, string modId)
    {
        if (string.IsNullOrWhiteSpace(modName))
            return modId;
        return string.Equals(modName, modId, StringComparison.OrdinalIgnoreCase)
            ? modName
            : $"{modName}（{modId}）";
    }
}
