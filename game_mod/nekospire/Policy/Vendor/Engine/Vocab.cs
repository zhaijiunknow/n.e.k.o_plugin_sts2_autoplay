using MegaCrit.Sts2.Core.Models;

namespace Sts2Headless;

/// <summary>
/// 全局词表：字符串键 → 整数 ID。0 为填充，1 为未知。词表文件只追加、不重排，
/// 旧检查点里的 ID 永远指向同一个内容（版本更新时用于“手术”式扩展）。
/// 键的形式：模型为“类别.条目”（如 CARD.STRIKE_IRONCLAD），其他为“前缀:名字”（如 EVENTOPT:NEOW.pages.INITIAL.options.X）。
/// </summary>
public static class Vocab
{
    public const int Pad = 0, Unknown = 1;
    static readonly Dictionary<string, int> Ids = new();
    static readonly List<string> Keys = new();
    /// <summary>运行中遇到的未知键（及次数），供 catalog 命令追加进词表。</summary>
    public static readonly Dictionary<string, int> Missing = new();

    public static string DefaultPath => AppContext.GetData("Sts2Vocab") as string ?? "vocab.txt";
    public static int Count => Keys.Count;
    public static IReadOnlyList<string> AllKeys => Keys;

    public static void Load(string? path = null)
    {
        Ids.Clear(); Keys.Clear();
        Add("<pad>"); Add("<unk>");
        path ??= DefaultPath;
        if (!File.Exists(path)) return;
        foreach (string line in File.ReadLines(path).Skip(2))
            if (line.Length > 0) Add(line);
        LoadAliases(Path.Combine(Path.GetDirectoryName(Path.GetFullPath(path)) ?? ".", "aliases.txt"));
    }

    /// <summary>
    /// 别名（v0.113.0 起）：游戏改了名、意思没变的内容（如火箭的 LASER_MOVE → ROCKET_CLAW_MOVE），新键照旧追加进词表，
    /// 但观测里用老键的 ID，旧检查点学到的嵌入接着用。aliases.txt 每行“新键 老键”（空白分隔，# 起为注释），与 vocab.txt 同目录；
    /// 文件不在时没有别名（实机桥接模组等只带词表的地方行为不变）。老键不在词表里的行忽略。
    /// </summary>
    static readonly Dictionary<string, int> Aliases = new();
    public static IReadOnlyDictionary<string, int> AliasIds => Aliases;

    static void LoadAliases(string path)
    {
        Aliases.Clear();
        if (!File.Exists(path)) return;
        foreach (string raw in File.ReadLines(path))
        {
            string line = raw.Split('#')[0].Trim();
            if (line.Length == 0) continue;
            var f = line.Split((char[]?)null, StringSplitOptions.RemoveEmptyEntries);
            if (f.Length == 2 && Ids.TryGetValue(f[1], out int old)) Aliases[f[0]] = old;
        }
    }

    /// <summary>追加一个键（已存在则返回原 ID）。</summary>
    public static int Add(string key)
    {
        if (Ids.TryGetValue(key, out int id)) return id;
        id = Keys.Count;
        Keys.Add(key);
        Ids[key] = id;
        return id;
    }

    public static void Save(string path) => File.WriteAllLines(path, Keys);

    public static int Id(string? key)
    {
        if (key == null) return Pad;
        if (Aliases.TryGetValue(key, out int alias)) return alias;
        if (Ids.TryGetValue(key, out int id)) return id;
        Missing[key] = Missing.GetValueOrDefault(key) + 1;
        return Unknown;
    }

    /// <summary>同 Id，但词表里没有时只返回 Unknown、不记进 Missing（头部 14）：给明知不在词表里、信息另有记录的键用
    /// （古神的遗物选项、建筑师对话、人偶房第二页、通用眩晕招式，见 Obs/Events.cs、Obs/Creatures.cs 的 F113）。</summary>
    public static int IdQuiet(string? key)
    {
        if (key == null) return Pad;
        if (Aliases.TryGetValue(key, out int alias)) return alias;      // 与 Id 同顺序：先别名
        return Ids.TryGetValue(key, out int id) ? id : Unknown;
    }

    public static string Model(AbstractModel m) => $"{m.Id.Category}.{m.Id.Entry}";
    public static int Id(AbstractModel? m) => m == null ? Pad : Id(Model(m));

    public static string EventOption(string textKey) => "EVENTOPT:" + textKey;
    public static string Rest(string optionId) => "REST:" + optionId;
    public static string Alt(string optionId) => "ALT:" + optionId.ToUpperInvariant();
    public static string Reward(string typeName) => "REWARD:" + typeName;
    public static string MapNode(string pointType) => "MAPNODE:" + pointType;
    public static string Intent(string intentType) => "INTENT:" + intentType;
    /// <summary>怪物招式：MOVE:怪物.招式状态 ID（招式 ID 只在同一个怪物内唯一）。</summary>
    public static string Move(AbstractModel monster, string stateId) => $"MOVE:{monster.Id.Entry}.{stateId}";
    public const string ShopRemoval = "SHOP:CARD_REMOVAL";
}
