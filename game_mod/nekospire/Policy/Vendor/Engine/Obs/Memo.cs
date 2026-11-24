using System.Runtime.CompilerServices;
using MegaCrit.Sts2.Core.Combat;
using MegaCrit.Sts2.Core.Entities.Cards;
using MegaCrit.Sts2.Core.Entities.Creatures;
using MegaCrit.Sts2.Core.Localization.DynamicVars;
using MegaCrit.Sts2.Core.Models;

namespace Sts2Headless;

// 一次 Encode 之内的记忆化（2026-11-16，引擎提速）。
//
// 观测编码对同一张牌、同一个目标反复问游戏同样的问题：界面数值预览（回合结束预演的背包按“牌 × 敌人”问一遍，每个出牌候选的 4–7、8、
// Fool 的附带伤害、敌人反应、拔箭又各问一遍）、打出次数、关键字（每张牌的保留、奇巧各问两遍）、能否打出、费用、可攻击的敌人……
// 每问一次游戏都要把全部钩子监听者走一遍（第三幕 Boss 战一百多个），一帧问上千次。
//
// 这里把这些问题的答案记在本次 Encode 里（Encode 开头启用、结尾清空，不跨帧）。为什么结果不变：
//   1. 编码只读局面：Encode 期间游戏状态不变。例外只有两处临时改动，都已排除：
//      - 凯撒蟹战按转身之后的朝向重算回合结束预演（FlippedLoss 临时改 SurroundedPower 的朝向）：改动期间记忆停用（Off），既不读也不记；
//      - 招式预告临时往怪物的出招记录里加假想的招式（AddMoves，finally 里删掉）：期间不调用这里的任何函数。
//   2. 这些都是游戏的只读查询（钩子的 Modify* / Should* 在预览模式下不改状态），同样的局面、同样的参数，答案相同。
//      调用抛异常时不记，下次照样重新调用（照样抛），异常处的行为不变。
//   3. 界面数值预览（CardModel.UpdateDynamicVarPreview）会改这张牌各个 DynamicVar 的 PreviewValue（编码有几处随后直接读它）：
//      记忆命中时把第一次预览之后各 DynamicVar 的 PreviewValue 原样写回，牌的状态与真的再预览一遍完全相同（同一个目标的预览结果相同；
//      没被预览改动的 DynamicVar 写回的就是它现在的值；EnchantedValue 每次预览都设成同一个值）。只有整个预览没抛异常时才记。
// 记忆表是线程静态的（编码不重入），不在局面复制的根里（ClonePolicy.SkipStatic），Encode 结束时清空，不留对游戏对象的引用。
public sealed partial class Observation
{
    sealed class RefPair : IEqualityComparer<(object a, object? b)>
    {
        public static readonly RefPair Instance = new();
        public bool Equals((object a, object? b) x, (object a, object? b) y) => ReferenceEquals(x.a, y.a) && ReferenceEquals(x.b, y.b);
        public int GetHashCode((object a, object? b) x) => HashCode.Combine(RuntimeHelpers.GetHashCode(x.a), x.b == null ? 0 : RuntimeHelpers.GetHashCode(x.b));
    }

    sealed class PreviewMemo
    {
        public float Dmg, Block, Hits;
        public DynamicVarSet Set = null!;
        public DynamicVar[] Vars = null!;
        public decimal[] Values = null!;
    }

    sealed class EncodeMemo
    {
        public readonly Dictionary<(object, object?), PreviewMemo> Preview = new(RefPair.Instance);
        public readonly Dictionary<(object, object?), int> Plays = new(RefPair.Instance);
        public readonly Dictionary<CardModel, IReadOnlySet<CardKeyword>> Keywords = new(ReferenceEqualityComparer.Instance);
        public readonly Dictionary<CardModel, bool> CanPlay = new(ReferenceEqualityComparer.Instance);
        public readonly Dictionary<CardModel, int> Cost = new(ReferenceEqualityComparer.Instance);
        public readonly Dictionary<CardModel, int> StarCost = new(ReferenceEqualityComparer.Instance);
        public readonly Dictionary<Creature, bool> Hittable = new(ReferenceEqualityComparer.Instance);
        public readonly Dictionary<(object, object?), (object targets, int v)> AtkSingle = new(RefPair.Instance), AtkTotal = new(RefPair.Instance);
        public object? HittableOf, MelodyOf;
        public readonly bool?[] Melody = new bool?[3];
        public IReadOnlyList<Creature>? HittableEnemies;
        /// <summary>大于 0 时停用（临时改了局面的时候，见文件头）。</summary>
        public int Off;

        public void Clear()
        {
            Preview.Clear(); Plays.Clear(); Keywords.Clear(); CanPlay.Clear(); Cost.Clear(); StarCost.Clear(); Hittable.Clear(); AtkSingle.Clear(); AtkTotal.Clear();
            HittableOf = null; HittableEnemies = null; Off = 0;
            MelodyOf = null; Melody[0] = Melody[1] = Melody[2] = null;
        }
    }

    [ThreadStatic] static EncodeMemo? _memoStore;
    /// <summary>本次 Encode 的记忆表；不在 Encode 里或已停用时为 null。</summary>
    [ThreadStatic] static EncodeMemo? _memo;
    static EncodeMemo? Memo => _memo is { Off: 0 } m ? m : null;

    /// <summary>为假时不记忆（bench-encode --ab 在同一进程里交替开关、对照耗时与帧）。</summary>
    public static bool MemoEnabled = true;
    static void MemoBegin() { FrozenEpoch++; if (!MemoEnabled) { _memo = null; return; } var m = _memoStore ??= new EncodeMemo(); m.Clear(); _memo = m; }
    static void MemoEnd() { FrozenEpoch++; _memo?.Clear(); _memo = null; }

    /// <summary>
    /// 正在编码、局面不变（记忆启用且没停用）时为真。无头引擎的钩子监听者池（PerfPatches.HookListenerPool）据此把这一帧的监听者序列
    /// 只算一遍、之后共用（编码期间监听者的集合与 Contains 的结果都不变，理由同上）；实机桥接没有那个补丁，这里只是个标记。
    /// </summary>
    public static bool Frozen => _memo is { Off: 0 };
    /// <summary>每次 Encode 开头、结尾各加一（冻结的监听者序列按它作废）。</summary>
    [ThreadStatic] public static int FrozenEpoch;
    /// <summary>临时改局面期间停用记忆（成对调用）。</summary>
    static void MemoPause() { if (_memo != null) _memo.Off++; }
    static void MemoResume() { if (_memo != null) _memo.Off--; }

    /// <summary>界面数值预览（见 PreviewCore），同一次 Encode 里同一张牌、同一个目标只真算一次；命中时把各 DynamicVar 的 PreviewValue 写回。</summary>
    static (float dmg, float block, float hits) Preview(CardModel c, Creature? target)
    {
        var m = Memo;
        if (m == null) return PreviewCore(c, target, out _);
        if (m.Preview.TryGetValue((c, target), out var e) && ReferenceEquals(e.Set, c.DynamicVars))
        {
            for (int i = 0; i < e.Vars.Length; i++) e.Vars[i].PreviewValue = e.Values[i];
            return (e.Dmg, e.Block, e.Hits);
        }
        var r = PreviewCore(c, target, out bool ok);
        if (ok)
        {
            var set = c.DynamicVars;
            var vars = set.Values.ToArray();
            var vals = new decimal[vars.Length];
            for (int i = 0; i < vars.Length; i++) vals[i] = vars[i].PreviewValue;
            m.Preview[(c, target)] = new PreviewMemo { Dmg = r.dmg, Block = r.block, Hits = r.hits, Set = set, Vars = vars, Values = vals };
        }
        return r;
    }

    /// <summary>这张牌的全部关键字（CardModel.Keywords：本地的加上战斗中别的模型给的，每次都走一遍钩子）。抛异常时不记。</summary>
    static IReadOnlySet<CardKeyword> Kw(CardModel c)
    {
        var m = Memo;
        if (m == null) return c.Keywords;
        if (m.Keywords.TryGetValue(c, out var k)) return k;
        k = c.Keywords;
        m.Keywords[c] = k;
        return k;
    }

    /// <summary>CardModel.CanPlay()。抛异常时不记。</summary>
    static bool CanPlayM(CardModel c)
    {
        var m = Memo;
        if (m == null) return c.CanPlay();
        if (m.CanPlay.TryGetValue(c, out bool v)) return v;
        v = c.CanPlay();
        m.CanPlay[c] = v;
        return v;
    }

    /// <summary>c.EnergyCost.GetWithModifiers(CostModifiers.All)（过费用钩子）。抛异常时不记。</summary>
    static int CostAll(CardModel c)
    {
        var m = Memo;
        if (m == null) return c.EnergyCost.GetWithModifiers(CostModifiers.All);
        if (m.Cost.TryGetValue(c, out int v)) return v;
        v = c.EnergyCost.GetWithModifiers(CostModifiers.All);
        m.Cost[c] = v;
        return v;
    }

    /// <summary>c.GetStarCostWithModifiers()（过星费钩子）。抛异常时不记。</summary>
    static int StarCost(CardModel c)
    {
        var m = Memo;
        if (m == null) return c.GetStarCostWithModifiers();
        if (m.StarCost.TryGetValue(c, out int v)) return v;
        v = c.GetStarCostWithModifiers();
        m.StarCost[c] = v;
        return v;
    }

    /// <summary>Creature.IsHittable（过 Hook.ShouldAllowHitting）。抛异常时不记。</summary>
    static bool IsHittableM(Creature c)
    {
        var m = Memo;
        if (m == null) return c.IsHittable;
        if (m.Hittable.TryGetValue(c, out bool v)) return v;
        v = c.IsHittable;
        m.Hittable[c] = v;
        return v;
    }

    /// <summary>CombatState.HittableEnemies（每次新建列表、逐个敌人过 IsHittable）。编码只读这个列表，同一次 Encode 里共用一份。抛异常时不记。</summary>
    static IReadOnlyList<Creature> Hittable(ICombatState cs)
    {
        var m = Memo;
        if (m == null) return cs.HittableEnemies;
        if (m.HittableEnemies != null && ReferenceEquals(m.HittableOf, cs)) return m.HittableEnemies;
        var v = cs.HittableEnemies;
        m.HittableOf = cs; m.HittableEnemies = v;
        return v;
    }

    /// <summary>怪物攻击意图的单次伤害（AttackIntent.GetSingleDamage：过一遍 Hook.ModifyDamage）。回合结束预演、生物记录、招式预告第 0 步对同一意图各问一遍。
    /// 招式预告往后推时会临时改出招记录，那段停用记忆（AddMoves）。抛异常时不记。</summary>
    static int AtkSingle(MegaCrit.Sts2.Core.MonsterMoves.Intents.AttackIntent atk, IEnumerable<Creature> targets, Creature owner)
    {
        var m = Memo;
        if (m == null) return atk.GetSingleDamage(targets, owner);
        if (m.AtkSingle.TryGetValue((atk, owner), out var e) && ReferenceEquals(e.targets, targets)) return e.v;
        int v = atk.GetSingleDamage(targets, owner);
        m.AtkSingle[(atk, owner)] = (targets, v);
        return v;
    }

    /// <summary>怪物攻击意图的总伤害（AttackIntent.GetTotalDamage），同 AtkSingle。</summary>
    static int AtkTotal(MegaCrit.Sts2.Core.MonsterMoves.Intents.AttackIntent atk, IEnumerable<Creature> targets, Creature owner)
    {
        var m = Memo;
        if (m == null) return atk.GetTotalDamage(targets, owner);
        if (m.AtkTotal.TryGetValue((atk, owner), out var e) && ReferenceEquals(e.targets, targets)) return e.v;
        int v = atk.GetTotalDamage(targets, owner);
        m.AtkTotal[(atk, owner)] = (targets, v);
        return v;
    }

    /// <summary>本回合打出过带哇 / 啾 / 叽标签的牌（MelodyCmd.PlayedWah / Cho / KiThisTurn：每问一次扫一遍整场的出牌历史）。
    /// Fool 的每个出牌候选都问三遍（FoolPreview），战斗计数又问一遍。抛异常时不记。</summary>
    static bool PlayedWah(MegaCrit.Sts2.Core.Entities.Players.Player p) => Played(p, 0);
    static bool PlayedCho(MegaCrit.Sts2.Core.Entities.Players.Player p) => Played(p, 1);
    static bool PlayedKi(MegaCrit.Sts2.Core.Entities.Players.Player p) => Played(p, 2);
    static bool Played(MegaCrit.Sts2.Core.Entities.Players.Player p, int k)
    {
        static bool Ask(MegaCrit.Sts2.Core.Entities.Players.Player p, int k) => k switch
        {
            0 => MegaCrit.Sts2.Core.Commands.MelodyCmd.PlayedWahThisTurn(p),
            1 => MegaCrit.Sts2.Core.Commands.MelodyCmd.PlayedChoThisTurn(p),
            _ => MegaCrit.Sts2.Core.Commands.MelodyCmd.PlayedKiThisTurn(p),
        };
        var m = Memo;
        if (m == null) return Ask(p, k);
        if (!ReferenceEquals(m.MelodyOf, p)) { m.MelodyOf = p; m.Melody[0] = m.Melody[1] = m.Melody[2] = null; }
        return m.Melody[k] ??= Ask(p, k);
    }

    // CardModel 的私有属性 HasSingleTurnRetain / HasSingleTurnSly（本回合临时的保留、奇巧）：ShouldRetainThisTurn = Keywords 含保留 || HasSingleTurnRetain，
    // IsSlyThisTurn 同理。直接读它们，省掉 ShouldRetainThisTurn / IsSlyThisTurn 里各一遍关键字钩子。找不到时（游戏改名）为 null，调用处照原来的写法。
    static readonly Func<CardModel, bool>? SingleTurnRetain = Getter("HasSingleTurnRetain");
    static readonly Func<CardModel, bool>? SingleTurnSly = Getter("HasSingleTurnSly");
    static Func<CardModel, bool>? Getter(string name)
    {
        var p = GameCompat.Property(typeof(CardModel), name, System.Reflection.BindingFlags.NonPublic | System.Reflection.BindingFlags.Instance, "本回合临时的保留、奇巧（编码提速）");
        return p?.GetMethod is { } g && p.PropertyType == typeof(bool) ? (Func<CardModel, bool>)Delegate.CreateDelegate(typeof(Func<CardModel, bool>), g) : null;
    }

    /// <summary>同 c.ShouldRetainThisTurn（关键字取 Kw 的记忆）。</summary>
    static bool RetainThisTurn(CardModel c) => SingleTurnRetain != null ? Kw(c).Contains(CardKeyword.Retain) || SingleTurnRetain(c) : c.ShouldRetainThisTurn;
}
