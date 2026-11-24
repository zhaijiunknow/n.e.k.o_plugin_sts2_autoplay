using MegaCrit.Sts2.Core.Combat;
using MegaCrit.Sts2.Core.Entities.Powers;
using MegaCrit.Sts2.Core.Entities.Cards;
using MegaCrit.Sts2.Core.Entities.Creatures;
using MegaCrit.Sts2.Core.Entities.Merchant;
using MegaCrit.Sts2.Core.Entities.Players;
using MegaCrit.Sts2.Core.Entities.Potions;
using MegaCrit.Sts2.Core.Entities.RestSite;
using MegaCrit.Sts2.Core.Events;
using MegaCrit.Sts2.Core.Map;
using MegaCrit.Sts2.Core.Models;
using System.Collections;
using System.Reflection;
using MegaCrit.Sts2.Core.MonsterMoves.Intents;
using MegaCrit.Sts2.Core.MonsterMoves.MonsterMoveStateMachine;
using MegaCrit.Sts2.Core.Rewards;
using MegaCrit.Sts2.Core.Rooms;
using MegaCrit.Sts2.Core.Runs;


namespace Sts2Headless;

// 事件选项、休息处选项、Crystal Sphere 小游戏、选牌说明、滑脚木桥的候选池。（从 Observation.cs 拆出，见 引擎模块与游戏更新适配.md）
public sealed partial class Observation
{
    /// <summary>
    /// Crystal Sphere 的棋盘，按玩家在 NCrystalSphereScreen 上看到的输出：每个已揭开的格子一条记录。
    /// 物品画在迷雾下面，揭开的格子露出物品图片的对应部分，所以给出格子里物品的类别（看图可辨：遗物、普通/稀有药水、
    /// 普通/罕见/稀有卡牌、诅咒、小/大金币）和这一格在物品里的位置；还没揭开的格子不给任何信息（它们就是候选）。
    /// 特征：0 x，1 y，2 有物品，3–11 物品类别（独热），12–13 这一格在物品里的列、行，14 物品已整个揭开，15 剩余占卜次数。
    /// 版本 15（决策记录 194）：16–17 物品的宽、高，18 这件物品还有几格没揭开（看到一角就能从图片定出整件物品的位置和大小）；
    /// 另按游戏的固定物品清单（CrystalSphereMinigame.PopulateItems）给每件还没露面的物品一条记录：19 为 1，2–11、16–17 同上，
    /// 18 为它在现在的棋盘上还有几种摆法（只看哪些格子还没揭开、哪些已被露面的物品占着，不看物品实际在哪）。
    /// </summary>
    void AddCrystalSphere(MegaCrit.Sts2.Core.Events.Custom.CrystalSphereEvent.CrystalSphereMinigame m)
    {
        _sphere = SphereView.Of(m);
        for (int x = 0; x < m.GridSize.X; x++)
        for (int y = 0; y < m.GridSize.Y; y++)
        {
            var cell = m.cells[x, y];
            if (cell.IsHidden) continue;
            Begin();
            _f[0] = x; _f[1] = y; _f[15] = m.DivinationCount;
            if (cell.Item is { } it)
            {
                _f[2] = 1; _f[3 + SphereCategory(it)] = 1;
                _f[12] = x - it.Position.X; _f[13] = y - it.Position.Y;
                bool all = true;
                for (int i = 0; i < it.Size.X && all; i++)
                for (int j = 0; j < it.Size.Y; j++)
                    if (m.cells[it.Position.X + i, it.Position.Y + j].IsHidden) { all = false; break; }
                _f[14] = all ? 1 : 0;
                _f[16] = it.Size.X; _f[17] = it.Size.Y; _f[18] = _sphere.Hidden.GetValueOrDefault(it);
            }
            int tok = Emit(null, TokenKind.SphereCell, Vocab.Pad, Zone.None, -1, 0);
            if (cell.Item is { } seen) _sphere.Tok.TryAdd(seen, tok);
        }
        foreach (var u in _sphere.Unseen)
        {
            Begin();
            _f[2] = 1; _f[3 + u.cat] = 1; _f[15] = m.DivinationCount; _f[16] = u.w; _f[17] = u.h; _f[18] = u.placements; _f[19] = 1;
            Emit(null, TokenKind.SphereCell, Vocab.Pad, Zone.None, -1, 0);
        }
    }

    SphereView? _sphere;

    /// <summary>
    /// 占卜棋盘上玩家看得到的推断（版本 15）。只用公开信息：哪些格子已揭开、揭开的格子露出什么（物品类别与这一格在图里的位置，
    /// 由此定出整件物品的位置和大小）、游戏规则里固定的物品清单与各物品的大小；还没露面的物品实际在哪不看。
    /// 还没露面的物品：每件在“没揭开、也不属于已露面物品”的格子上数所有摆法，按摆法覆盖算每格被它占着的比例，
    /// 各件按独立近似合成每格被某件还没露面的物品占着的概率 P。
    /// </summary>
    sealed class SphereView
    {
        // 规则里的物品清单：类别（同记录 3–11）、宽、高、件数（CrystalSphereMinigame.PopulateItems 与各物品的 Size）
        static readonly (int cat, int w, int h, int n)[] Roster =
            { (0, 4, 4, 1), (1, 1, 3, 2), (2, 2, 2, 1), (3, 2, 2, 1), (4, 2, 2, 1), (5, 2, 2, 1), (6, 2, 2, 1), (7, 1, 1, 5), (8, 2, 1, 2) };
        public readonly Dictionary<MegaCrit.Sts2.Core.Events.Custom.CrystalSphereEvent.CrystalSphereItem, int> Hidden = new(), Tok = new();
        public MegaCrit.Sts2.Core.Events.Custom.CrystalSphereEvent.CrystalSphereItem?[,] Known = null!;     // 没揭开、属于已露面物品的格子
        public float[,] P = null!;
        public readonly List<(int cat, int w, int h, int placements)> Unseen = new();
        public int W, H;

        public static SphereView Of(MegaCrit.Sts2.Core.Events.Custom.CrystalSphereEvent.CrystalSphereMinigame m)
        {
            var v = new SphereView { W = m.GridSize.X, H = m.GridSize.Y };
            v.Known = new MegaCrit.Sts2.Core.Events.Custom.CrystalSphereEvent.CrystalSphereItem?[v.W, v.H]; v.P = new float[v.W, v.H];
            var seenCount = new int[9];
            for (int x = 0; x < v.W; x++)
            for (int y = 0; y < v.H; y++)
                if (!m.cells[x, y].IsHidden && m.cells[x, y].Item is { } it && !v.Hidden.ContainsKey(it))
                {
                    seenCount[SphereCategory(it)]++;
                    int n = 0;
                    for (int i = 0; i < it.Size.X; i++)
                    for (int j = 0; j < it.Size.Y; j++)
                    {
                        int cx = it.Position.X + i, cy = it.Position.Y + j;
                        if (cx < v.W && cy < v.H && m.cells[cx, cy].IsHidden) { v.Known[cx, cy] = it; n++; }
                    }
                    v.Hidden[it] = n;
                }
            bool Free(int x, int y) => x < v.W && y < v.H && m.cells[x, y].IsHidden && v.Known[x, y] == null;
            var miss = new float[v.W, v.H];
            for (int x = 0; x < v.W; x++) for (int y = 0; y < v.H; y++) miss[x, y] = 1;
            foreach (var (cat, w, h, n) in Roster)
            {
                int left = n - seenCount[cat];
                if (left <= 0) continue;
                var cover = new int[v.W, v.H]; int total = 0;
                for (int x = 0; x + w <= v.W; x++)
                for (int y = 0; y + h <= v.H; y++)
                {
                    bool ok = true;
                    for (int i = 0; i < w && ok; i++) for (int j = 0; j < h; j++) if (!Free(x + i, y + j)) { ok = false; break; }
                    if (!ok) continue;
                    total++;
                    for (int i = 0; i < w; i++) for (int j = 0; j < h; j++) cover[x + i, y + j]++;
                }
                for (int k = 0; k < left; k++) v.Unseen.Add((cat, w, h, total));
                if (total == 0) continue;
                for (int x = 0; x < v.W; x++) for (int y = 0; y < v.H; y++)
                    if (cover[x, y] > 0) miss[x, y] *= MathF.Pow(1 - (float)cover[x, y] / total, left);
            }
            for (int x = 0; x < v.W; x++) for (int y = 0; y < v.H; y++) v.P[x, y] = Free(x, y) ? 1 - miss[x, y] : 0;
            return v;
        }

        /// <summary>
        /// 一次占卜（小工具 1 格，大工具 3×3）的候选特征 4–7：挖到已露面的好物品几格、能整个挖开几件已露面的好物品、
        /// 能把已露面的诅咒整个挖开几件（挖开就拿到诅咒）、预计挖到几格还没露面的物品（P 求和）；
        /// 另返回这一下挖到格数最多的已露面好物品（候选的对象 B 引用它的记录）。
        /// </summary>
        public (float a, float b, float c, float d, MegaCrit.Sts2.Core.Events.Custom.CrystalSphereEvent.CrystalSphereItem? best) Dig(MegaCrit.Sts2.Core.Events.Custom.CrystalSphereEvent.CrystalSphereMinigame m, int x0, int y0, bool big)
        {
            var hit = new Dictionary<MegaCrit.Sts2.Core.Events.Custom.CrystalSphereEvent.CrystalSphereItem, int>();
            float good = 0, unseen = 0;
            int r = big ? 1 : 0;
            for (int x = x0 - r; x <= x0 + r; x++)
            for (int y = y0 - r; y <= y0 + r; y++)
            {
                if (x < 0 || y < 0 || x >= W || y >= H || !m.cells[x, y].IsHidden) continue;
                if (Known[x, y] is { } it) { hit[it] = hit.GetValueOrDefault(it) + 1; if (it.IsGood) good++; }
                else unseen += P[x, y];
            }
            float done = 0, curse = 0;
            MegaCrit.Sts2.Core.Events.Custom.CrystalSphereEvent.CrystalSphereItem? best = null;
            foreach (var (it, k) in hit)
            {
                if (k >= Hidden[it]) { if (it.IsGood) done++; else curse++; }
                if (it.IsGood && (best == null || k > hit[best])) best = it;
            }
            return (good, done, curse, unseen, best);
        }
    }

    /// <summary>
    /// 选牌界面上的说明（NCardGridSelectionScreen、NPlayerHand 等显示的提示语、张数与关闭按钮）：一条“选牌说明”记录，
    /// 编号为发起选牌的卡牌/遗物/药水/能力（提示语是它自己的 selectionScreenPrompt 时；否则为 0），附加编号为附魔选牌要附的附魔。
    /// 特征：0–5 通用提示语独热（变换、消耗、删除、附魔、弃牌、升级），6 是发起者自己的提示语，7 最少张数，8 最多张数，9 已选张数，
    /// 10 能否取消，11 可选张数，12–15 从哪里选（手牌、战斗中的牌堆、牌组、新给出的牌）。
    /// 已经选中的牌在界面上高亮，另各出一条“已选的牌”记录（特征同卡牌）。
    /// </summary>
    void AddSelectPrompt(PendingCardChoice pc, IReadOnlyList<int> selected)
    {
        var info = pc.Info;
        Begin();
        int src = 0;
        if (info?.PromptTable == "card_selection")
        {
            int k = Array.IndexOf(GenericPrompts, info.PromptKey);
            if (k >= 0) _f[k] = 1;
        }
        else if (info?.PromptKey is { } key && key.EndsWith(".selectionScreenPrompt") && PromptOwner(info.PromptTable!, key[..^".selectionScreenPrompt".Length]) is { } owner)
        {
            _f[6] = 1; src = Vocab.Id(owner);
        }
        // 版本 14：通用提示语（“选一张牌消耗”等）时编号取选牌命令的来源参数（发起的卡牌、能力、遗物），没有时取正在使用的药水
        if (src == 0) src = Vocab.Id(info?.Source ?? AgentCardSelector.UsingPotion);
        int max = Math.Min(pc.Max, pc.Options.Count);
        _f[7] = Math.Min(pc.Min, max); _f[8] = max; _f[9] = selected.Count; _f[10] = pc.Cancelable ? 1 : 0; _f[11] = pc.Options.Count;
        string m = info?.Method ?? "";
        if (m.StartsWith("FromHand")) _f[12] = 1;
        else if (m == "FromCombatPile") _f[13] = 1;
        else if (m.StartsWith("FromDeck")) _f[14] = 1;
        else if (m.Length > 0) _f[15] = 1;
        Emit(null, TokenKind.SelectPrompt, src, Zone.None, -1, Vocab.Id(info?.Enchantment));
        foreach (int i in selected) AddCard(pc.Options[i], Zone.Offer, kind: TokenKind.SelectedCard);
    }

    static readonly string[] GenericPrompts = { "TO_TRANSFORM", "TO_EXHAUST", "TO_REMOVE", "TO_ENCHANT", "TO_DISCARD", "TO_UPGRADE" };
    static Dictionary<(string, string), AbstractModel>? _promptOwners;

    /// <summary>提示语“表名 + 编号.selectionScreenPrompt”对应的模型（CardModel、RelicModel、PotionModel、PowerModel 的 SelectionScreenPrompt）。</summary>
    static AbstractModel? PromptOwner(string table, string entry)
    {
        _promptOwners ??= ModelDb.AllCards.Select(m => (("cards", m.Id.Entry), (AbstractModel)m))
            .Concat(ModelDb.AllRelics.Select(m => (("relics", m.Id.Entry), (AbstractModel)m)))
            .Concat(ModelDb.AllPotions.Select(m => (("potions", m.Id.Entry), (AbstractModel)m)))
            .Concat(ModelDb.AllPowers.Select(m => (("powers", m.Id.Entry), (AbstractModel)m)))
            .GroupBy(x => x.Item1).ToDictionary(g => g.Key, g => g.First().Item2);
        return _promptOwners.GetValueOrDefault((table, entry));
    }

    /// <summary>物品类别：0 遗物，1 普通药水，2 稀有药水，3 普通卡牌，4 罕见卡牌，5 稀有卡牌，6 诅咒，7 小金币，8 大金币（与 SerializableCrystalSphereItem 的字段对应）。</summary>
    static int SphereCategory(MegaCrit.Sts2.Core.Events.Custom.CrystalSphereEvent.CrystalSphereItem it)
    {
        var z = it.ToSerializable();
        return z.type switch
        {
            MegaCrit.Sts2.Core.Events.Custom.CrystalSphereEvent.CrystalSphereItemType.Relic => 0,
            MegaCrit.Sts2.Core.Events.Custom.CrystalSphereEvent.CrystalSphereItemType.Potion => z.potionRarity == PotionRarity.Rare ? 2 : 1,
            MegaCrit.Sts2.Core.Events.Custom.CrystalSphereEvent.CrystalSphereItemType.CardReward => z.cardRarity switch { CardRarity.Rare => 5, CardRarity.Uncommon => 4, _ => 3 },
            MegaCrit.Sts2.Core.Events.Custom.CrystalSphereEvent.CrystalSphereItemType.Curse => 6,
            _ => z.isBigGold ? 8 : 7,
        };
    }

    /// <summary>候选特征 3：这次占卜会揭开几格（界面上点之前会高亮出来）。小工具 1 格，大工具是 3×3 里还没揭开的格数。</summary>
    static float SphereClears(Choice ch)
    {
        if (ch.F2 == 0 || FakeScreens.CrystalSphere is not { } m) return 1;
        int n = 0, x0 = (int)ch.F0, y0 = (int)ch.F1;
        for (int x = x0 - 1; x <= x0 + 1; x++)
        for (int y = y0 - 1; y <= y0 + 1; y++)
            if (x >= 0 && y >= 0 && x < m.GridSize.X && y < m.GridSize.Y && m.cells[x, y].IsHidden) n++;
        return n;
    }

    /// <summary>
    /// 事件选项：特征 0 是否锁定，1 是否为“继续”类；2–7 选项文字里的数值（事件的 DynamicVars 等，按事件定义的顺序取前 6 个数值，
    /// 如占卜的花费、要掉的血、给的金币、当前伤害），界面文字里显示的就是这些数。
    /// </summary>
    void AddEventOption(EventOption ev)
    {
        Begin(); _f[0] = ev.IsLocked ? 1 : 0; _f[1] = ev.IsProceed ? 1 : 0;
        int k = 2;
        foreach (var v in ev.Description.Variables.Values)
        {
            if (k > 7) break;
            float? x = v switch
            {
                MegaCrit.Sts2.Core.Localization.DynamicVars.DynamicVar dv => (float)dv.BaseValue,
                decimal m => (float)m,
                int i => i,
                bool b => b ? 1 : 0,
                _ => null,
            };
            if (x is { } val) _f[k++] = val;
        }
        // 版本 16（第十二份外部评审，决策记录 205）：选项文字里已经展示、原来按字符串丢掉的信息。
        // “药水的未来？”：12 这瓶药水换成的牌型（1 攻击、2 技能、3 能力），13 稀有度（1 普通、2 罕见、3 稀有）；
        // 遗物交换商：15 换回来的遗物的词表 ID（悬浮提示依次是交出的、换回的遗物；交出的那件照旧是候选引用的对象）；
        // 无尽传送带：14 已经取过几道菜（游戏每第 5 次进固定分支，第十三份外部评审，决策记录 206）
        if (ev.TextKey == "THE_FUTURE_OF_POTIONS.pages.INITIAL.options.POTION") (_f[12], _f[13]) = FuturePotionInfo(ev);
        else if (ev.TextKey.StartsWith("ENDLESS_CONVEYOR."))
        {
            try
            {
                if (RunManager.Instance.EventSynchronizer.GetLocalEvent() is MegaCrit.Sts2.Core.Models.Events.EndlessConveyor ec)
                    _f[14] = (int)ConveyorGrabs!.GetValue(ec)!;
            }
            catch { }
        }
        else if (ev.TextKey.StartsWith("RELIC_TRADER.pages.INITIAL.options."))
        {
            var rel = (ev.HoverTips ?? Array.Empty<MegaCrit.Sts2.Core.HoverTips.IHoverTip>())
                .Select(t => t is MegaCrit.Sts2.Core.HoverTips.HoverTip ht ? ht.CanonicalModel as RelicModel : null).Where(r => r != null).Distinct().ToList();
            if (rel.Count >= 2) _f[15] = Vocab.Id(rel[1]);
        }
        // 版本 19（2026-10-31 实机对照，决策记录 227）：整个事件的 DynamicVars。游戏的选项按钮显示时（NEventOptionButton._Ready）
        // 把它们全部并进每个选项的说明与标题，界面上的数（如沉没宝库的 66、324 金币）多半来自这里；无头引擎没有界面、不并，2–7 照旧只有选项自带的数
        // （实机模组编码时拿掉并进来的，两边一致）。这里直接从事件取，按事件定义的顺序前 8 个数值：前 4 个放 8–11，后 4 个放 16–19
        try
        {
            if (RunManager.Instance.EventSynchronizer.GetLocalEvent() is { } em)
            {
                int j = 0;
                foreach (var dv in em.DynamicVars.Values)
                {
                    if (j >= 8) break;
                    // 格式 29（G62 J12）：审判的参赛编号（Trial.cs:213-215，Rng.Chaotic 抽的 101–999，界面上的装饰数）不进特征，后面的数值依次前移
                    if (V29 && em is MegaCrit.Sts2.Core.Models.Events.Trial && dv.Name == "EntrantNumber") continue;
                    _f[j < 4 ? 8 + j : 12 + j] = (float)dv.BaseValue; j++;
                }
            }
        }
        catch { }
        // v0.113.0 修正（F113）：新事件里给遗物的选项没有用 WithRelic 挂上遗物（附加 ID 原来为 0）：诺奴佩普浴场的热浴给浴巾、私人绿洲给浴袍，落难瓦皮的两个救法给瓦皮
        int optRelic = Vocab.Id(ev.Relic);
        if (F113 && ev.Relic == null)
            optRelic = ev.TextKey switch
            {
                "THERMAE_NONUPEIPE.pages.INITIAL.options.THERMAL_BATHS" => Vocab.Id(ModelDb.Relic<MegaCrit.Sts2.Core.Models.Relics.BathTowel>()),
                "THERMAE_NONUPEIPE.pages.INITIAL.options.PRIVATE_OASIS" => Vocab.Id(ModelDb.Relic<MegaCrit.Sts2.Core.Models.Relics.BathRobes>()),
                "WUPPY_IN_TROUBLE.pages.INITIAL.options.HELP_IMMEDIATELY" or "WUPPY_IN_TROUBLE.pages.INITIAL.options.BE_CAUTIOUS" => Vocab.Id(ModelDb.Relic<MegaCrit.Sts2.Core.Models.Relics.Wuppy>()),
                _ => optRelic,
            };
        // v0.113.0 修正（F113）：几类选项的文本键本来就不在词表里（老问题，selfcheck 的冒烟走不到）：非涅奥古神的遗物选项（<古神>.pages.INITIAL.options.<遗物 ID>，
        // 遗物在附加 ID、数值在 2–7）、建筑师的对话与 PROCEED（胜负已定）、人偶房第二页（文本键是遗物的英文原名，遗物在候选 B）。照旧记为 <unk>，只是不再记进
        // 词表外计数（头部 14 原来每进一次第二幕就加 1，“应为 0”的检查因此失效）；词表里有的键（欧洛巴斯的 OPTION_POOL_3_LOCKED 等）照常
        int optId;
        {
            var evm = F113 ? RunManager.Instance.EventSynchronizer.GetLocalEvent() : null;
            bool quiet = F113 && (ev.TextKey == "PROCEED" || (evm is MegaCrit.Sts2.Core.Models.AncientEventModel && evm is not MegaCrit.Sts2.Core.Models.Events.Neow)
                                  || evm is MegaCrit.Sts2.Core.Models.Events.TheArchitect || evm is MegaCrit.Sts2.Core.Models.Events.DollRoom);
            optId = quiet ? Vocab.IdQuiet(Vocab.EventOption(ev.TextKey)) : Vocab.Id(Vocab.EventOption(ev.TextKey));
        }
        // 格式 29（G34 J4）：选项 22 选它会致死（EventOption.WillKillPlayer，界面把按钮标红的同一判断，EventOption.cs:34,200-220）
        if (V29) { try { _y[0] = ev.WillKillPlayer?.Invoke(RunSession.Me) == true ? 1 : 0; } catch { } }
        Emit(ev, TokenKind.Option, optId, Zone.Offer, -1, optRelic);
    }

    /// <summary>“药水的未来？”的一个选项：这瓶药水要换成的牌型与稀有度（游戏在选项里已经写明；事件的私有表与方法，反射只读）。</summary>
    static (float type, float rarity) FuturePotionInfo(EventOption ev)
    {
        try
        {
            if (RunManager.Instance.EventSynchronizer.GetLocalEvent() is not MegaCrit.Sts2.Core.Models.Events.TheFutureOfPotions fp) return (0, 0);
            int idx = fp.CurrentOptions.ToList().IndexOf(ev);
            var pots = fp.Owner.Potions.ToList();
            if (idx < 0 || idx >= pots.Count) return (0, 0);
            var map = (IDictionary)FuturePotionTypes!.GetValue(fp)!;
            var ct = (MegaCrit.Sts2.Core.Entities.Cards.CardType)map[pots[idx]]!;
            var cr = (MegaCrit.Sts2.Core.Entities.Cards.CardRarity)FuturePotionRarity!.Invoke(fp, new object[] { pots[idx] })!;
            return (ct switch { MegaCrit.Sts2.Core.Entities.Cards.CardType.Attack => 1, MegaCrit.Sts2.Core.Entities.Cards.CardType.Skill => 2, MegaCrit.Sts2.Core.Entities.Cards.CardType.Power => 3, _ => 4 },
                    cr switch { MegaCrit.Sts2.Core.Entities.Cards.CardRarity.Common => 1, MegaCrit.Sts2.Core.Entities.Cards.CardRarity.Uncommon => 2, MegaCrit.Sts2.Core.Entities.Cards.CardRarity.Rare => 3, _ => 4 });
        }
        catch { return (0, 0); }
    }

    // 事件的私有成员（游戏更新改名时 selfcheck 能列出来；原来是用到时才现找）
    static readonly PropertyInfo? ConveyorGrabs = GameCompat.Property(typeof(MegaCrit.Sts2.Core.Models.Events.EndlessConveyor), "NumOfGrabs", BindingFlags.NonPublic | BindingFlags.Instance, "无尽传送带取过几盘");
    static readonly PropertyInfo? FuturePotionTypes = GameCompat.Property(typeof(MegaCrit.Sts2.Core.Models.Events.TheFutureOfPotions), "PotionToCardType", BindingFlags.NonPublic | BindingFlags.Instance, "药水的未来：换成的牌型");
    static readonly MethodInfo? FuturePotionRarity = GameCompat.Method(typeof(MegaCrit.Sts2.Core.Models.Events.TheFutureOfPotions), "GetCardRarity", BindingFlags.NonPublic | BindingFlags.Instance, "药水的未来：稀有度");
    static readonly PropertyInfo? BridgeCurrent = GameCompat.Property(typeof(MegaCrit.Sts2.Core.Models.Events.SlipperyBridge), "RandomCardToLose", BindingFlags.NonPublic | BindingFlags.Instance, "滑脚木桥当前提议的牌");
    static readonly PropertyInfo? BridgeSkipped = GameCompat.Property(typeof(MegaCrit.Sts2.Core.Models.Events.SlipperyBridge), "SkippedRemovals", BindingFlags.NonPublic | BindingFlags.Instance, "滑脚木桥拒绝过的牌");

    void AddOption(object key, string vocabKey, int aux, float v0 = 0, float v1 = 0, Zone zone = Zone.Offer)
    {
        Begin(); _f[0] = v0; _f[1] = v1;
        Emit(key, TokenKind.Option, Vocab.Id(vocabKey), zone, -1, aux);
    }

    /// <summary>
    /// 休息处选项（版本 8 起带数值，见 RunSession.RestHealInfo）：休息为 2 实际能回的血 / 最大生命、3 名义回血量 / 最大生命、
    /// 4 附带的其他效果条数；升级为 2 可升级的牌数 / 牌组张数。其余选项全为 0。
    /// </summary>
    void AddRestOption(RestSiteOption ro)
    {
        Begin();
        var me = RunSession.Me;
        int max = Math.Max(1, me.Creature.MaxHp);
        if (ro is HealRestSiteOption)
        {
            var (actual, nominal, extra) = RunSession.RestHealInfo(me);
            _f[2] = (float)actual / max; _f[3] = (float)nominal / max; _f[4] = extra;
            // 版本 16（第十二份外部评审，决策记录 205）：到下一场战斗第一个行动之前确定会回的血（小血瓶、假血瓶第 1 回合回血；
            // 缩放仪在下一个房间是 Boss 时开打前回血，遗物状态为激活），12 为它占最大生命的比例，13 为这次休息实际让入场生命多出来的比例
            int up = 0;
            foreach (var r in me.Relics)
            {
                try
                {
                    if (r is MegaCrit.Sts2.Core.Models.Relics.BloodVial or MegaCrit.Sts2.Core.Models.Relics.FakeBloodVial) up += r.DynamicVars.Heal.IntValue;
                    else if (r is MegaCrit.Sts2.Core.Models.Relics.Pantograph && r.Status == MegaCrit.Sts2.Core.Entities.Relics.RelicStatus.Active) up += r.DynamicVars.Heal.IntValue;
                    // v0.113.0 修正（F113）：浴巾每场第 1 回合回 Heal（同小血瓶，BathTowel）；浴袍在下一场是 Boss 时（状态激活）开打前回满（BathRobes）
                    else if (F113 && r is MegaCrit.Sts2.Core.Models.Relics.BathTowel) up += r.DynamicVars.Heal.IntValue;
                    else if (F113 && r is MegaCrit.Sts2.Core.Models.Relics.BathRobes && r.Status == MegaCrit.Sts2.Core.Entities.Relics.RelicStatus.Active) up += max;
                }
                catch { }
            }
            int hp = me.Creature.CurrentHp;
            _f[12] = (float)up / max;
            _f[13] = (float)(Math.Min(hp + actual + up, max) - Math.Min(hp + up, max)) / max;
        }
        else if (ro is SmithRestSiteOption)
            _f[2] = (float)me.Deck.UpgradableCardCount / Math.Max(1, me.Deck.Cards.Count);
        // v0.113.0 修正（F113）：“进化”把异鸟宝宝换成小异鸟，牌组里的异鸟扑击全部变成异鸟猛击、保留升级（EvolveRestSiteOption、Byrdine）：2 扑击张数，3 其中升级过的
        else if (F113 && ro is MegaCrit.Sts2.Core.Entities.RestSite.EvolveRestSiteOption)
        { var sw = me.Deck.Cards.Where(c => c is MegaCrit.Sts2.Core.Models.Cards.ByrdSwoop).ToList(); _f[2] = sw.Count; _f[3] = sw.Count(c => c.IsUpgraded); }
        // v0.113.0 修正（F113）：“恢复”把瓦皮回满（RestoreRestSiteOption）：2 缺的生命占它最大生命的比例，3 缺几点
        else if (F113 && ro is MegaCrit.Sts2.Core.Entities.RestSite.RestoreRestSiteOption && me.Relics.OfType<MegaCrit.Sts2.Core.Models.Relics.Wuppy>().FirstOrDefault() is { } wr)
        { int miss = Math.Max(0, wr.MaxHp - wr.CurrentHp); _f[2] = (float)miss / Math.Max(1, wr.MaxHp); _f[3] = miss; }
        Emit(ro, TokenKind.Option, Vocab.Id(Vocab.Rest(ro.OptionId)), Zone.Offer, -1, 0);
    }

    /// <summary>滑脚木桥“再撑一会”下一次可能抽到的牌（版本 18）：照搬 SlipperyBridge.GetNewRandomCard——此前拒绝过的具体牌、
    /// 当前提议牌的同类全部排除，不可删的排除；排完为空时回到全部可删的牌。不在这个事件里为 null。</summary>
    static HashSet<CardModel>? BridgePool(Player me)
    {
        try
        {
            if (RunManager.Instance.EventSynchronizer.GetLocalEvent() is not MegaCrit.Sts2.Core.Models.Events.SlipperyBridge sb) return null;
            var cur = BridgeCurrent!.GetValue(sb) as CardModel;
            var skipped = BridgeSkipped!.GetValue(sb) as HashSet<CardModel>;
            if (cur == null) return null;
            var ex = new HashSet<CardModel>(skipped ?? new HashSet<CardModel>()) { cur };
            var pool = me.Deck.Cards.Where(c => c.GetType() != cur.GetType() && c.IsRemovable && !ex.Contains(c)).ToHashSet();
            return pool.Count > 0 ? pool : me.Deck.Cards.Where(c => c.IsRemovable).ToHashSet();
        }
        catch { return null; }
    }
}
