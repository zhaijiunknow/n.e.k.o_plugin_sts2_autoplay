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

// 卡牌记录（类型 4）与卡牌的附带记录，界面数值预览、X 费段数、升级预览。（从 Observation.cs 拆出，见 引擎模块与游戏更新适配.md）
public sealed partial class Observation
{
    /// <summary>
    /// 手牌按游戏当前规则计算费用、关键字和能否打出（这些要遍历钩子，较慢）；其他牌堆和非战斗中的牌用卡牌自身的值。
    /// </summary>
    void AddCard(CardModel c, Zone zone, bool inHand = false, float priv0 = 0, TokenKind kind = TokenKind.Card)
    {
        Begin();
        if (inHand) { try { _f[0] = c.EnergyCost.CostsX ? 0 : CostAll(c); } catch { _f[0] = c.EnergyCost.Canonical; } }
        else _f[0] = c.EnergyCost.CostsX ? 0 : c.EnergyCost.GetWithModifiers(CostModifiers.Local);
        _f[1] = c.EnergyCost.CostsX ? 1 : 0;
        _f[2] = c.CurrentStarCost;
        // 格式 20：1 为 2 表示星 X 费（星尘；能量 X 费仍为 1），2 星费：手牌按游戏出牌时的判断（GetStarCostWithModifiers：虚空形态等改星费的钩子，
        // 星 X 费为现在会花掉的星数）；其他牌堆星 X 费为 0（同能量 X 费的 0），其余照旧。没有星费仍为 −1
        if (V20)
        {
            if (c.HasStarCostX) { _f[1] = 2; _f[2] = 0; }
            if (inHand) { try { _f[2] = StarCost(c); } catch { } }
        }
        _f[3] = c.CurrentUpgradeLevel;
        if (inHand) _f[4] = CanPlayM(c) ? 1 : 0;
        // 16、17 的界面预览（手牌）；预览同时更新了各项的 PreviewValue，下面 7–8 的计算型数值要用
        var (pd, pb, _) = inHand ? Preview(c, null) : (-1f, -1f, 1f);
        // 7–8：伤害、格挡以外的前两个数值。版本 12 起按“计算型数值优先”排：Calculated 开头的（按局面算的段数、抽牌数、集中、引导数等，
        // 卡面上显示的就是它）排在前面，手牌取界面预览的值；它的计算常数 CalculationBase、CalculationExtra 不再占位（卡牌 ID 和升级次数就能推出）
        int other = 0;
        foreach (var pass in new[] { true, false })
            foreach (var (name, v) in c.DynamicVars)
            {
                // 格式 22：奥斯提出手的攻击（OstyDamage，亡灵契约师）也是这张牌的伤害
                if (name is "Damage" or "CalculatedDamage" || (V22 && name == "OstyDamage")) { if (pass) _f[5] = (float)v.BaseValue; }
                else if (name is "Block" or "CalculatedBlock") { if (pass) _f[6] = (float)v.BaseValue; }
                else if (name is "CalculationBase" or "CalculationExtra") { }
                else if (name.StartsWith("Calculated") == pass && other < 2) _f[7 + other++] = V29 ? CardVar29(c, v, pass, inHand) : pass && inHand ? (float)v.PreviewValue : (float)v.BaseValue;
            }
        // 格式 29（G55）：奥斯提出手的牌在奥斯提不在时打不出伤害，手牌外的 16 也记 0（手牌的预览已经是 0）
        bool ostyGone29 = false;
        if (V29 && pd < 0 && CombatManager.Instance.IsInProgress)
            try { ostyGone29 = c.Tags.Contains(CardTag.OstyAttack) && c.Owner is { IsOstyMissing: true }; } catch { }
        // 版本 15：疯狂科学按实例的类型给伤害、格挡（不是这一型的为 0），7–8 为附加效果的数值
        var ms = c as MegaCrit.Sts2.Core.Models.Cards.MadScience;
        if (ms != null)
        {
            if (ms.TinkerTimeType != CardType.Attack) _f[5] = 0;
            if (ms.TinkerTimeType != CardType.Skill) _f[6] = 0;
            (_f[7], _f[8]) = TinkerValues(ms);
        }
        var kw = inHand ? Kw(c) : c.GetKeywordsWithSources(KeywordSources.Local);
        // 版本 15 修正：本回合临时的保留、奇巧（独门技术拿进手里的牌、手法等，CardModel.ShouldRetainThisTurn、IsSlyThisTurn，卡面上会显示）也算
        bool retain1 = false, sly1 = false;
        // 提速（Memo.cs）：ShouldRetainThisTurn = 关键字含保留 || HasSingleTurnRetain，所以 retain1 = HasSingleTurnRetain && 关键字不含保留（奇巧同理）；
        // 两个临时标记都没有时结果必为假（原写法里关键字抛异常时也是假），不必再走关键字钩子；关键字只算一次（Kw）。抛异常时同原来一样两个都为假
        if (SingleTurnRetain != null && SingleTurnSly != null)
        {
            bool r1 = SingleTurnRetain(c), s1 = SingleTurnSly(c);
            if (r1 || s1) try { var all = Kw(c); retain1 = r1 && !all.Contains(CardKeyword.Retain); sly1 = s1 && !all.Contains(CardKeyword.Sly); } catch { }
        }
        else try { retain1 = !c.Keywords.Contains(CardKeyword.Retain) && c.ShouldRetainThisTurn; sly1 = !c.Keywords.Contains(CardKeyword.Sly) && c.IsSlyThisTurn; } catch { }
        // v0.113.0：幻影之刃给小刀的保留从“直接加在牌上”（CardCmd.ApplyKeyword，本地关键词）改成战斗内的动态关键词（TryModifyKeywordsInCombat），
        // 手牌外只看本地关键词时抽牌堆、弃牌堆里的小刀就不再显示保留了；照 v0.111.0 的口径补上（猎人模型学的是这个）
        if (!inHand) try { retain1 |= c.Tags.Contains(CardTag.Shiv) && c.Owner?.Creature?.HasPower<MegaCrit.Sts2.Core.Models.Powers.PhantomBladesPower>() == true; } catch { }
        _f[9] = kw.Contains(CardKeyword.Ethereal) ? 1 : 0; _f[10] = kw.Contains(CardKeyword.Retain) || retain1 ? 1 : 0;
        _f[11] = kw.Contains(CardKeyword.Exhaust) ? 1 : 0; _f[12] = kw.Contains(CardKeyword.Unplayable) ? 1 : 0;
        _f[13] = c.Enchantment?.Amount ?? 0; _f[14] = c.Affliction?.Amount ?? 0; _f[15] = kw.Contains(CardKeyword.Innate) ? 1 : 0;
        // 版本 9：16、17 按当前状态算的伤害、格挡（手牌按游戏界面的预览：力量、敏捷、虚弱、脆弱等；其他牌堆同 5、6），18 奇巧，19 永恒
        _f[16] = pd >= 0 ? pd : _f[5]; _f[17] = pb >= 0 ? pb : _f[6];
        if (ms != null) { if (ms.TinkerTimeType != CardType.Attack) _f[16] = 0; if (ms.TinkerTimeType != CardType.Skill) _f[17] = 0; }
        _f[18] = kw.Contains(CardKeyword.Sly) || sly1 ? 1 : 0; _f[19] = kw.Contains(CardKeyword.Eternal) ? 1 : 0;
        // v0.113.0 修正（F113）：手牌以外、战斗中带计算型伤害、格挡的牌，16、17 按现在的局面算（CalculatedVar.Calculate(null) = 基础 + 额外 × 倍数，不过伤害钩子）。
        // 原来取 BaseValue（= CalculationBase），全力射击（10 × 怒放消耗的张数，存在实例的私有字段里，卡牌 ID 推不出）在手牌外恒为 0，苦恼、哇 交响乐只给基础值
        if (F113 && pd < 0 && CombatManager.Instance.IsInProgress)
        {
            try
            {
                if (c.DynamicVars.TryGetValue("CalculatedDamage", out var cdv) && cdv is MegaCrit.Sts2.Core.Localization.DynamicVars.CalculatedVar cdc) _f[16] = (float)cdc.Calculate(null);
                if (c.DynamicVars.TryGetValue("CalculatedBlock", out var cbv) && cbv is MegaCrit.Sts2.Core.Localization.DynamicVars.CalculatedVar cbc) _f[17] = (float)cbc.Calculate(null);
            }
            catch { }
        }
        // 版本 15 修正：手牌以外的牌也算上附魔的加成（动量等把打出后的成长记在附魔自己身上，卡牌的数值不变，只有手牌的界面预览才看得到）
        if (pd < 0 && c.Enchantment is { } enc)
        {
            try
            {
                var mv = MegaCrit.Sts2.Core.ValueProps.ValueProp.Move;
                if (_f[16] > 0) _f[16] = (float)(((decimal)_f[16] + enc.EnchantDamageAdditive((decimal)_f[16], mv)) * enc.EnchantDamageMultiplicative((decimal)_f[16], mv));
                if (_f[17] > 0) _f[17] = (float)(((decimal)_f[17] + enc.EnchantBlockAdditive((decimal)_f[17])) * enc.EnchantBlockMultiplicative((decimal)_f[17]));
            }
            catch { }
        }
        if (ostyGone29) _f[16] = 0;      // 格式 29（G55），放在手牌外计算型伤害与附魔加成之后
        _p[0] = priv0;
        // 格式 27：20 标黄、21 标红，游戏自己给牌算的发光判断（CardModel.ShouldGlowGold / ShouldGlowRed，各卡牌重写的 ShouldGlow*Internal，
        // 没有时取附魔的 ShouldGlow*）。只算战斗中的手牌（游戏只在手牌上显示；界面另加“能打出、出牌阶段”的条件，能否打出已在 4，这里取卡牌本身的判断）
        if (V27 && inHand && CombatManager.Instance.IsInProgress)
        {
            try { _x[0] = c.ShouldGlowGold ? 1 : 0; } catch { }
            try { _x[1] = c.ShouldGlowRed ? 1 : 0; } catch { }
        }
        Emit(kind == TokenKind.Card ? c : null, kind, Vocab.Id(c), zone, -1, Vocab.Id(c.Enchantment));
        // 版本 13：卡牌的苦痛另记一条能力记录（词表 ID 为苦痛，0 层数，1 是减益），所属记录为这张牌：和生物的能力一样加到所属记录上
        int cardTok = NumTokens - 1;
        if (c.Affliction is { } af)
        {
            Begin(); _f[0] = af.Amount; _f[1] = 1;
            Emit(null, TokenKind.Power, Vocab.Id(af), zone, cardTok, 0);
        }
        // 版本 13 补：附魔已失效（迅速、播种、华彩、活力等一次性效果用过了，游戏界面上附魔变灰）时另记一条能力记录
        // （词表 ID 为附魔，0 层数，3 为 1 表示已失效），所属记录为这张牌；还生效的附魔不记（卡牌记录的附加 ID 与第 13 列已经有了）
        if (c.Enchantment is { Status: MegaCrit.Sts2.Core.Entities.Enchantments.EnchantmentStatus.Disabled } en)
        {
            Begin(); _f[0] = en.Amount; _f[3] = 1;
            Emit(null, TokenKind.Power, Vocab.Id(en), zone, cardTok, 0);
        }
        // 版本 15（第七份评审）：费用修正的有效期。卡牌的本地费用修正各有有效期（本回合、打出为止、整场，CardEnergyCost 的 LocalCostModifier），
        // 结束回合、打出之后各去掉一部分；与现在的本地费用不同时各记一条能力记录：CARDMOD:COST_NEXT_TURN（下回合的费用）、CARDMOD:COST_AFTER_PLAY（再次打出时的费用）
        if (CombatManager.Instance.IsInProgress && !c.EnergyCost.CostsX && c.EnergyCost.HasLocalModifiers && LocalMods?.GetValue(c.EnergyCost) is List<MegaCrit.Sts2.Core.Entities.Cards.LocalCostModifier> mods)
        {
            try
            {
                int now = c.EnergyCost.GetWithModifiers(CostModifiers.Local), b = c.EnergyCost.GetWithModifiers(CostModifiers.None);
                int Cost(MegaCrit.Sts2.Core.Entities.Cards.LocalCostModifierExpiration drop)
                {
                    int v = b;
                    foreach (var m in mods) if (!m.Expiration.HasFlag(drop)) v = m.Modify(v);
                    return Math.Max(0, v);
                }
                int next = Cost(MegaCrit.Sts2.Core.Entities.Cards.LocalCostModifierExpiration.EndOfTurn), again = Cost(MegaCrit.Sts2.Core.Entities.Cards.LocalCostModifierExpiration.WhenPlayed);
                if (next != now) { Begin(); _f[0] = next; Emit(null, TokenKind.Power, Vocab.Id("CARDMOD:COST_NEXT_TURN"), zone, cardTok, 0); }
                if (again != now) { Begin(); _f[0] = again; Emit(null, TokenKind.Power, Vocab.Id("CARDMOD:COST_AFTER_PLAY"), zone, cardTok, 0); }
            }
            catch { }
        }
        // 版本 15（第七份评审）：战斗里生成的副本（双持、复制等，没有关联永久牌组 DeckVersion）而牌组里有同一张牌时记一条 CARDMOD:COPY：
        // 巨镰、遗传算法等永久成长的牌只有打出原牌才长到牌组里
        try
        {
            if (zone != Zone.Deck && CombatManager.Instance.IsInProgress && c.DeckVersion == null && c.Owner?.Deck.Cards.Any(d => d.Id == c.Id) == true)
            { Begin(); Emit(null, TokenKind.Power, Vocab.Id("CARDMOD:COPY"), zone, cardTok, 0); }
        }
        catch { }
        // 版本 17（第十五份评审）：无休手斧、流星锤本回合打出过，下回合抽牌前会从弃牌堆等处回到手里（游戏按这张牌在上个玩家回合的出牌记录判断），
        // 记一条 CARDMOD:RETURN_NEXT_TURN，所属记录为这张牌；19 为 1（训练端按它找出这类记录，乘清零的门控）
        try
        {
            if ((c is MegaCrit.Sts2.Core.Models.Cards.ThrummingHatchet or MegaCrit.Sts2.Core.Models.Cards.Bolas || ((V24 || F113) && c is MegaCrit.Sts2.Core.Models.Cards.Bounce)) && CombatManager.Instance.IsInProgress
                && c.Owner?.Creature?.CombatState is { } ccs
                && CombatManager.Instance.History.CardPlaysFinished.Any(e => e.HappenedThisTurn(ccs) && e.CardPlay.Card == c))
            { Begin(); _f[19] = 1; Emit(null, TokenKind.Power, Vocab.Id("CARDMOD:RETURN_NEXT_TURN"), zone, cardTok, 0); }
        }
        catch { }
        // 版本 18（第十六份评审）：滑脚木桥里，牌组中“再撑一会”下一次可能抽到的牌记一条 CARDMOD:BRIDGE_POOL，所属记录为这张牌；
        // 18 为 1（训练端按它找出这类记录，乘清零的门控）
        if (zone == Zone.Deck && _bridgePool != null && _bridgePool.Contains(c))
        { Begin(); _f[18] = 1; Emit(null, TokenKind.Power, Vocab.Id("CARDMOD:BRIDGE_POOL"), zone, cardTok, 0); }
        // 版本 15：重放次数（士兵炖汤、附魔等；卡面上显示“重放 N”）另记一条能力记录（词表 ID 为 CARDMOD:REPLAY，0 为次数），所属记录为这张牌
        int replay = 0;
        try { replay = c.GetEnchantedReplayCount(); } catch { }
        if (replay > 0) { Begin(); _f[0] = replay; Emit(null, TokenKind.Power, Vocab.Id("CARDMOD:REPLAY"), zone, cardTok, 0); }
        // 版本 15：疯狂科学的类型与附加效果各记一条能力记录（词表 ID 为 TINKER:TYPE.类型、TINKER:RIDER.效果，0 为附加效果的第一个数值），所属记录为这张牌
        if (ms != null)
        {
            Begin();
            Emit(null, TokenKind.Power, Vocab.Id("TINKER:TYPE." + ms.TinkerTimeType.ToString().ToUpperInvariant()), zone, cardTok, 0);
            if (ms.TinkerTimeRider != MegaCrit.Sts2.Core.Models.Events.TinkerTime.RiderEffect.None)
            {
                Begin(); _f[0] = TinkerValues(ms).a;
                Emit(null, TokenKind.Power, Vocab.Id("TINKER:RIDER." + ms.TinkerTimeRider.ToString().ToUpperInvariant()), zone, cardTok, 0);
            }
        }
    }

    static readonly System.Reflection.FieldInfo? LocalMods = GameCompat.Field(typeof(MegaCrit.Sts2.Core.Entities.Cards.CardEnergyCost), "_localModifiers", "费用修正的有效期（CARDMOD:COST_*）");

    /// <summary>
    /// 疯狂科学（版本 15）：卡牌类型与附加效果是这张牌实例的属性（TinkerTime 事件里定制，存档字段 TinkerTimeType、TinkerTimeRider），
    /// 同一个卡牌 ID、内容先验分不出。返回附加效果的两个数值（易伤虚弱各几层、几段、绞杀伤害、能量、抽几张、力量敏捷、好奇减费、改良层数）。
    /// </summary>
    static (float a, float b) TinkerValues(MegaCrit.Sts2.Core.Models.Cards.MadScience ms)
    {
        float V(string k) { try { return (float)ms.DynamicVars[k].BaseValue; } catch { return 0; } }
        return ms.TinkerTimeRider switch
        {
            MegaCrit.Sts2.Core.Models.Events.TinkerTime.RiderEffect.Sapping => (V("SappingWeak"), V("SappingVulnerable")),
            MegaCrit.Sts2.Core.Models.Events.TinkerTime.RiderEffect.Violence => (V("ViolenceHits"), 0),
            MegaCrit.Sts2.Core.Models.Events.TinkerTime.RiderEffect.Choking => (V("ChokingDamage"), 0),
            MegaCrit.Sts2.Core.Models.Events.TinkerTime.RiderEffect.Energized => (V("EnergizedEnergy"), 0),
            MegaCrit.Sts2.Core.Models.Events.TinkerTime.RiderEffect.Wisdom => (V("WisdomCards"), 0),
            MegaCrit.Sts2.Core.Models.Events.TinkerTime.RiderEffect.Chaos => (1, 0),
            MegaCrit.Sts2.Core.Models.Events.TinkerTime.RiderEffect.Expertise => (V("ExpertiseStrength"), V("ExpertiseDexterity")),
            MegaCrit.Sts2.Core.Models.Events.TinkerTime.RiderEffect.Curious => (V("CuriousReduction"), 0),
            MegaCrit.Sts2.Core.Models.Events.TinkerTime.RiderEffect.Improvement => (1, 0),
            _ => (0, 0),
        };
    }

    /// <summary>
    /// 游戏界面上的数值预览（CardModel.UpdateDynamicVarPreview，Normal 模式；有目标时按这个目标算，如易伤）：
    /// 返回单次伤害、格挡、段数（Repeat / CalculatedHits，可以为 0；没有这项为 1）；没有对应数值为 −1。只影响显示用的 PreviewValue，不改卡牌。
    /// ok 为假表示中途抛了异常（返回已算到的部分，同原来）。编码里经 Memo.cs 的 Preview 调用（同一次 Encode 里同一牌、同一目标只真算一次）。
    /// </summary>
    static (float dmg, float block, float hits) PreviewCore(CardModel c, Creature? target, out bool ok)
    {
        float dmg = -1, block = -1, hits = 1;
        ok = true;
        try
        {
            c.UpdateDynamicVarPreview(CardPreviewMode.Normal, target, c.DynamicVars);
            foreach (var (name, v) in c.DynamicVars)
            {
                if (name is "Damage" or "CalculatedDamage") dmg = (float)v.PreviewValue;
                else if (V22 && name == "OstyDamage") dmg = (float)v.PreviewValue;      // 格式 22：奥斯提出手（OstyDamageVar 的预览按奥斯提的力量、虚弱等算）
                else if (name is "Block" or "CalculatedBlock") block = (float)v.PreviewValue;
                // v0.113.0 修正（F113）：不再把 "Repeat" 当段数。v0.113.0 里段数全改成 HitCount / CalculatedHits，还带 Repeat 的攻击牌里它都是别的数
                // （冰枪 3 是生成冰霜球的个数、实际打 1 段，原来算成 3 段；折射是玻璃球数，被后面的 HitCount 覆盖；扯碎被 CalculatedHits 覆盖）
                else if (name == "CalculatedHits" || (name == "Repeat" && !F113)) hits = Math.Max(0, (float)v.PreviewValue);     // 版本 13 修正：可以是 0 段（弹幕齐射没有球时打不出伤害）
                // v0.113.0：多段攻击的段数改成新的 HitCountVar（键 "HitCount"）。21 张旧牌改用它：12 张原来是 RepeatVar（"Repeat"，上一行，所有格式都读，
                // 如弹射、七星、黏液弹），9 张原来写死在 OnPlay（双重打击等，格式 20 起 XHits 补成 2）。和 "Repeat" 同样所有格式都读：
                // 不读的话故障机器人（格式 19）的黏液弹从 3 段退成 1 段；代价是故障机器人的喧哗、撕裂、双重打击从原来少算的 1 段变成正确的 2 段（帧有变化）。
                // 折射两个都有（Repeat 是别的数值，两者恰都为 2）
                else if (name == "HitCount") hits = Math.Max(0, (float)v.PreviewValue);
            }
            // 版本 15：疯狂科学只有攻击型造成伤害（暴力附加效果打 3 段）、只有技能型获得格挡（卡面的伤害、格挡数值两种都有）
            if (c is MegaCrit.Sts2.Core.Models.Cards.MadScience ms)
            {
                if (ms.TinkerTimeType != CardType.Attack) dmg = -1;
                // F113：暴力的段数在 v0.113.0 改成 HitCountVar，出牌时过 ModifyAttackHitCount（拇指夹 +1），取预览值
                else if (ms.TinkerTimeRider == MegaCrit.Sts2.Core.Models.Events.TinkerTime.RiderEffect.Violence)
                    hits = F113 && ms.DynamicVars.TryGetValue("ViolenceHits", out var vh) ? Math.Max(0, (float)vh.PreviewValue) : TinkerValues(ms).a;
                if (ms.TinkerTimeType != CardType.Skill) block = -1;
            }
            // 格式 22：奥斯提出手的牌（标签 OstyAttack）奥斯提不在或死了时打不出伤害（Osty.CheckMissingWithAnim 直接跳过），界面上是红的
            if (V22 && dmg >= 0 && c.Tags.Contains(CardTag.OstyAttack) && c.Owner is { IsOstyMissing: true }) dmg = 0;
            // 格式 29（G41）：碎骨在奥斯提不在时整段跳过（BoneShards.OnPlay：Osty.CheckMissingWithAnim），没有格挡
            if (V29 && block >= 0 && c is MegaCrit.Sts2.Core.Models.Cards.BoneShards && c.Owner is { IsOstyMissing: true }) block = 0;
            // 格式 29（G13）：契约终结在消耗堆不足 Cards 张时一点伤害都没有（PactsEnd.CanDealDamage）
            if (V29 && dmg >= 0 && c is MegaCrit.Sts2.Core.Models.Cards.PactsEnd && c.Owner is { } po
                && CardPile.GetCards(po, PileType.Exhaust).Count() < c.DynamicVars.Cards.IntValue) dmg = 0;
            // 格式 24：射击牌没有可用的箭时整张牌不生效（ArrowCmd.ShootArrowAtTarget 不执行攻击，牌照样打出、花能量），卡面预览照样显示伤害
            if ((V24 || F113) && dmg >= 0 && ShootDisabled(c)) dmg = 0;     // F113：别的角色拿到 Fool 的射击牌（多彩哲学家等）同样
            // 格式 24：钉住只对被扎穿的目标造成伤害（Nail.OnPlay 先判 ArrowCmd.CreatureIsImpaledByArrow），有目标时按目标判断
            if ((V24 || F113) && dmg >= 0 && c is MegaCrit.Sts2.Core.Models.Cards.Nail && target != null && !NailHits(target)) dmg = 0;
            // v0.113.0 修正（F113）：拆卸只在目标有易伤时打 HitCount 段（Dismantle.OnPlay；db6c272 读 HitCount 之后恒按 2 段），
            // 怨恨只在本回合掉过血时打 HitCount 段（Spite.OnPlay，LostHpThisTurn），否则 1 段（1 段时拇指夹不加）
            if (F113 && dmg >= 0 && c is MegaCrit.Sts2.Core.Models.Cards.Dismantle && target != null && !target.HasPower<MegaCrit.Sts2.Core.Models.Powers.VulnerablePower>()) hits = Math.Min(hits, 1);
            if (F113 && dmg >= 0 && c is MegaCrit.Sts2.Core.Models.Cards.Spite && c.Owner?.Creature is { } spo && !LostHpThisTurn(spo)) hits = Math.Min(hits, 1);
            if (V20 && dmg >= 0) hits = XHits(c, hits);
            // v0.113.0 修正（F113）：哇 爆破本回合已打出过啾或叽时打两段（WahBlast.OnPlay：WithHitCount(HitsTwice ? 2 : 1)，卡面没有段数数值；出牌时再过 ModifyAttackHitCount）
            if (F113 && dmg >= 0 && c is MegaCrit.Sts2.Core.Models.Cards.WahBlast && c.Owner is { } wbo
                && (PlayedCho(wbo) || PlayedKi(wbo)))
                hits = ModHits(c, 2);
            if (V22 && dmg >= 0) hits = IroncladCounts(c, c.Owner, hits).hits;      // 格式 22：恶魔之焰的段数 = 消耗的张数
            // 格式 20：取整同界面（DynamicVar 显示 (int)PreviewValue）与实际结算（Creature.LoseHpInternal、GainBlockInternal 都截掉小数），虚弱的 13.5 实际是 13
            // v0.113.0 修正第六轮（F113）：格式 19（故障机器人）也取整。原来只有格式 20 起取整，脆弱下防御记成 3.75、实际 3（GainBlockInternal 的 (int)），
            // 候选 8 多记、候选 10 少算掉血；虚弱下伤害同样（LoseHpInternal 的 (int)）
            if (V20 || F113) { if (dmg > 0) dmg = MathF.Floor(dmg); if (block > 0) block = MathF.Floor(block); }
        }
        catch { ok = false; }
        return (dmg, block, hits);
    }

    /// <summary>
    /// 格式 20：X 费攻击的段数等于出牌时花掉的 X（卡面的数值里没有段数，原来按 1 段算）。现在打出会花掉全部能量（星尘为全部星），
    /// 经 Hook.ModifyXValue（化学物 X 等）修正；天穹钻头 X 不小于卡面的能量阈值时翻倍。照各卡 OnPlay 的 WithHitCount 写死名单，名单外的照旧。
    /// v0.111.0 时段数写死在 OnPlay 里、卡面没有段数数值的固定两段攻击（星脉冲等 8 张，WithHitCount(2)）也在这里补成 2；v0.113.0 起它们改用 HitCountVar，
    /// 预览值已过 Hook.ModifyAttackHitCount（新遗物拇指夹：多段攻击 +1 段），这里不再写死（写死会把拇指夹的那一段盖掉）。
    /// X 费攻击的段数同样再过 Hook.ModifyAttackHitCount（与 HitCountVar.GetPreviewValue 同一写法；两版里只有拇指夹实现这个钩子，没有拇指夹时结果不变）。
    /// </summary>
    static float XHits(CardModel c, float hits)
    {
        try
        {
            if (c.Owner?.PlayerCombatState is not { } pcs || c.CombatState is not { } cs) return hits;
            int X(int v) => Math.Max(0, MegaCrit.Sts2.Core.Hooks.Hook.ModifyXValue(cs, c, v));
            int H(int h)
            {
                try { return (int)MegaCrit.Sts2.Core.Hooks.Hook.ModifyAttackHitCount(cs, MegaCrit.Sts2.Core.Commands.DamageCmd.Attack(0m).FromCard(c, null), h); }
                catch { return h; }
            }
            switch (c)
            {
                case MegaCrit.Sts2.Core.Models.Cards.Stardust: return H(X(pcs.Stars));
                case MegaCrit.Sts2.Core.Models.Cards.HeavenlyDrill hd:
                    int x = X(pcs.Energy);
                    return H(x >= hd.DynamicVars.Energy.IntValue ? 2 * x : x);
                case MegaCrit.Sts2.Core.Models.Cards.Volley or MegaCrit.Sts2.Core.Models.Cards.Skewer or MegaCrit.Sts2.Core.Models.Cards.Eradicate or MegaCrit.Sts2.Core.Models.Cards.Whirlwind:
                    return H(X(pcs.Energy));
                // v0.113.0 修正（F113）：重复射击（Fool，X 费）射一支箭打 X 段（RepeatShot.OnPlay：ResolveEnergyXValue，WithHitCount(X)；X 为 0 时整张牌不执行，不射箭）
                case MegaCrit.Sts2.Core.Models.Cards.RepeatShot when F113:
                    return H(X(pcs.Energy));
            }
        }
        catch { }
        return hits;
    }

    /// <summary>
    /// 格式 29：卡牌 7–8 的一个数值。G55：战斗中、手牌外的计算型数值（第一遍取的 Calculated*）按现在的局面算（CalculatedVar.Calculate(null)，
    /// 扯碎的段数 = 1 + 本场没被挡住的次数），原来取 BaseValue（= CalculationBase）；G56：手牌的能力层数（PowerVar，第二遍）取界面预览
    /// （PowerVar.UpdateCardPreview 过 Hook.ModifyPowerAmountGiven：异蛇头骨下致命毒药 6），原来取原值。其余照旧。
    /// </summary>
    static float CardVar29(CardModel c, MegaCrit.Sts2.Core.Localization.DynamicVars.DynamicVar v, bool pass, bool inHand)
    {
        try
        {
            if (pass && inHand) return (float)v.PreviewValue;
            if (pass && v is MegaCrit.Sts2.Core.Localization.DynamicVars.CalculatedVar cv && CombatManager.Instance.IsInProgress && c.CombatState != null)
                return (float)cv.Calculate(null);
            if (!pass && inHand && IsPowerVar(v)) return (float)v.PreviewValue;
        }
        catch { }
        return (float)v.BaseValue;
    }

    static bool IsPowerVar(MegaCrit.Sts2.Core.Localization.DynamicVars.DynamicVar v)
    {
        for (var t = v.GetType(); t != null && t != typeof(object); t = t.BaseType)
            if (t.IsGenericType && t.GetGenericTypeDefinition() == typeof(MegaCrit.Sts2.Core.Localization.DynamicVars.PowerVar<>)) return true;
        return false;
    }

    /// <summary>升级预览（版本 9）：卡牌复制一份再升级（同游戏的升级预览界面，但不登记进对局），失败时为 null。</summary>
    internal static CardModel? UpgradedCopy(CardModel c)
    {
        try
        {
            if (!c.IsUpgradable) return null;
            var copy = (CardModel)c.ClonePreservingMutability();
            copy.UpgradeInternal();
            return copy;
        }
        catch { return null; }
    }
}
