namespace Sts2Headless;

public enum DecisionKind
{
    /// <summary>游戏在推进中却没有可做的决策：说明有未接管的选择入口，属于引擎缺陷。</summary>
    Stuck,
    Combat,
    CardSelect,
    CardReward,
    Bundle,
    Rewards,
    Event,
    RestSite,
    Shop,
    Treasure,
    /// <summary>事件里的小游戏（目前只有 Crystal Sphere）。</summary>
    Minigame,
    Map,
    GameOver,
}

/// <summary>候选项的动作类型（导出给训练端，数值只追加不改）。</summary>
public enum ActionKind
{
    None = 0,
    EndTurn = 1,
    PlayCard = 2,        // A=手牌，B=目标生物（可无）
    UsePotion = 3,       // A=药水，B=目标生物（可无）
    DiscardPotion = 4,   // A=药水
    SelectCard = 5,      // A=卡牌（选牌界面中逐张选择）
    ConfirmSelection = 6,
    PickCardReward = 7,  // A=卡牌
    CardRewardAlt = 8,   // Key=替代项（跳过、重抽等）
    PickBundle = 9,      // A=卡包第一张牌，F0=卡包序号
    TakeReward = 10,     // A=奖励
    Leave = 11,
    EventOption = 12,    // A=事件选项
    RestOption = 13,     // A=休息处选项
    Buy = 14,            // A=商品（卡牌、遗物、药水或删牌），F0=价格
    MapMove = 15,        // A=地图节点
    PickRelic = 16,      // A=遗物
    SkipRelic = 17,
    MinigameCell = 18,   // F0=x，F1=y，F2=工具（0 小 1 大）
}

/// <summary>
/// 一个候选项。Run 在消息泵上执行，执行后游戏继续推进到下一个决策点。
/// A、B 是候选引用的游戏对象（卡牌、生物、药水、奖励、地图节点等），由观测编码器映射成实体记录的下标。
/// </summary>
public sealed record Choice(string Label, Func<Task> Run)
{
    public ActionKind Kind { get; init; }
    public object? A { get; init; }
    public object? B { get; init; }
    /// <summary>不对应实体时的词表键（如卡牌奖励的替代项）。</summary>
    public string? Key { get; init; }
    public float F0 { get; init; }
    public float F1 { get; init; }
    public float F2 { get; init; }
}

/// <summary>
/// 一个决策点，所有决策都是单选。选牌界面的多选由 RunSession 拆成“逐张选 + 确认”。
/// </summary>
public sealed class Decision(DecisionKind kind)
{
    public DecisionKind Kind { get; } = kind;
    public List<Choice> Choices { get; } = new();
    /// <summary>选牌界面中已选的张数（其余决策为 0）。</summary>
    public int Selected { get; init; }
    public string Context { get; init; } = "";
    /// <summary>界面上看得到、但不是候选的对象（如药水栏满时待领、待买的药水，卡包里的每张牌）：按给定区域编码，价格写进药水的特征 2。</summary>
    public List<(object Obj, Zone Zone, float Price)> Shown { get; } = new();
    /// <summary>版本 19（决策记录 227）：商店里买不起的商品（卡牌、遗物、药水本身，删牌为条目）与价格；观测按能力类记录挂在全局记录上。</summary>
    public List<(object Obj, float Price)> Locked { get; } = new();

    public Decision Add(ActionKind kind, string label, Func<Task> run, object? a = null, object? b = null, string? key = null, float f0 = 0, float f1 = 0, float f2 = 0)
    {
        Choices.Add(new Choice(label, run) { Kind = kind, A = a, B = b, Key = key, F0 = f0, F1 = f1, F2 = f2 });
        return this;
    }

    public Decision Add(ActionKind kind, string label, Action run, object? a = null, object? b = null, string? key = null, float f0 = 0, float f1 = 0, float f2 = 0) =>
        Add(kind, label, () => { run(); return Task.CompletedTask; }, a, b, key, f0, f1, f2);

    public override string ToString() => $"{Kind}{(Context.Length > 0 ? " " + Context : "")}：{Choices.Count} 项";
}
