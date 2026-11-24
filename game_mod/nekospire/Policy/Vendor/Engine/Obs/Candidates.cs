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

// 候选的整数与 12 个特征：出牌伤害预览、打全体与附带伤害、占卜、购买、候选级的“活过本回合”（从 Observation.cs 拆出，见 引擎模块与游戏更新适配.md）
public sealed partial class Observation
{
    /// <summary>候选（动作类型、对象 A/B、词表 ID 与 12 个特征）；引用到尚未编码的对象时补一条“供选择”区域的记录。</summary>
    void AddChoices(Decision d, Player me, bool inCombat, bool labels)
    {
        // 版本 16（第十二份外部评审，决策记录 205）：商店里在卖会员卡、自己还没有时，购买候选另给买了会员卡之后的价格
        bool memberOffer = d.Kind == DecisionKind.Shop && !me.Relics.Any(r => r is MegaCrit.Sts2.Core.Models.Relics.MembershipCard)
            && d.Choices.Any(c => c.Kind == ActionKind.Buy && c.A is MegaCrit.Sts2.Core.Models.Relics.MembershipCard);
        // 格式 29（G02、G45）：选牌界面里已经选了、还没结算的牌（选项里不在候选中的），凑满张数的那一下一并结算（Cand29.cs 的 SelectFx29）
        _selPending = V29 && d.Kind == DecisionKind.CardSelect && _sel != null ? _sel.Options.Where(o => !d.Choices.Any(c => ReferenceEquals(c.A, o))).ToList() : null;
        // 候选；引用到尚未编码的对象时，补一条“供选择”区域的记录
        foreach (var ch in d.Choices)
        {
            // 升级预览的那份复制品单独记在“升级预览”区域（版本 9）
            if (ch.Kind == ActionKind.SelectCard && ch.B is CardModel up && !_index.ContainsKey(up)) AddCard(up, Zone.UpgradePreview);
            int a = Ref(ch.A), b = Ref(ch.B);
            // 前往地图节点：对象 B 为这个节点的路线摘要
            if (b < 0 && ch.Kind == ActionKind.MapMove && ch.A is MapPoint mp && _route.TryGetValue(mp, out int ri)) b = ri;
            // 版本 15：占卜的候选，对象 B 为这一下挖到格数最多的已露面好物品（它的第一条格子记录）
            (float a, float b, float c, float d, MegaCrit.Sts2.Core.Events.Custom.CrystalSphereEvent.CrystalSphereItem? best) dig = default;
            if (ch.Kind == ActionKind.MinigameCell && _sphere != null && FakeScreens.CrystalSphere is { } sm)
            {
                dig = _sphere.Dig(sm, (int)ch.F0, (int)ch.F1, ch.F2 != 0);
                if (b < 0 && dig.best != null && _sphere.Tok.TryGetValue(dig.best, out int st)) b = st;
            }
            CI.Add((int)ch.Kind); CI.Add(a); CI.Add(b); CI.Add(Vocab.Id(ch.Key)); CI.Add(0);
            _autoGain = default; _autoHand = 0; _autoGone = null; _orbAfter = null;      // 第五轮：自动打出的结果只在这个候选内有效（FoolPreview 填）
            Array.Clear(_cy); _thornsGain = 0;      // 格式 29 的候选新列 14–19（这个候选内各处按需填）；这一步新加的荆棘（包 4 写、包 3 读）
            CF.Add(ch.F0); CF.Add(ch.F1); CF.Add(ch.F2); CF.Add(ch.Kind == ActionKind.MinigameCell ? SphereClears(ch) : 0);
            // 版本 9：出牌且有目标生物时，4 对这个目标的单次伤害（界面预览），5 段数，6 总伤害，7 能否打死（总伤害 ≥ 生命 + 格挡）
            float c4 = 0, c5 = 0, c6 = 0, c7 = 0;
            List<Creature>? killed = null;      // 这一步打得死的敌人（候选 10 不算它们的攻击）
            // 格式 23：这一步对每个敌人造成的掉血（判断加毒、打伤之后毒不毒得死用）；毒性爆发结算之后每个敌人剩下的毒
            var hl = (V23 || F113) && inCombat ? new Dictionary<Creature, float>() : null;      // F113 第四轮：所有格式都记（敌人的阈值眩晕用）
            var pout = V23 && inCombat ? new Dictionary<Creature, int>() : null;
            if (ch.Kind == ActionKind.PlayCard && ch.A is CardModel pcard && ch.B is Creature tgt)
            {
                var (pd, _, hits) = Preview(pcard, tgt);
                // 版本 15 修正：6 总伤害含重放（士兵炖汤等，游戏按 Hook.ModifyCardPlayCount 打几次）；7 按实际掉血判断（逐段过格挡与游戏的掉血修正，硬化外壳的本回合额度封顶）
                int plays = PlayCount(pcard, tgt);
                if (pd >= 0)
                {
                    c4 = pd; c5 = hits; c6 = pd * hits * plays;
                    float lost = FoeHpLoss(tgt, pcard, pd, (int)hits * plays, tgt.Block);
                    c7 = lost >= tgt.CurrentHp ? 1 : 0;
                    if (hl != null) hl[tgt] = lost;
                }
                Preview(pcard, null);      // 恢复不带目标的预览，免得影响别处
                if (c7 > 0) killed = new() { tgt };
            }
            // 格式 20：不选目标的攻击也给 4–7。打全体（TargetType.AllEnemies，带寻锋的君王之剑也是）逐个可打的敌人按界面预览算：
            // 4 单次伤害（各敌人中最大），5 段数，6 各敌人总伤害之和（同单体口径，不扣格挡），7 打得死几个（单体时 0 或 1，含义一致）。
            // 打随机敌人（AttackCommand 每段从活着的敌人里随机挑）只在只剩一个活着的敌人时按单体算，多个时仍为 0
            else if (V20 && inCombat && ch.Kind == ActionKind.PlayCard && ch.A is CardModel acard && ch.B == null)
            {
                var foes = me.Creature.CombatState!.Creatures.Where(c => !c.IsPlayer && c.Side != me.Creature.Side && c.IsAlive).ToList();
                var hitList = acard.TargetType switch
                {
                    TargetType.AllEnemies => foes.Where(c => IsHittableM(c)).ToList(),
                    TargetType.RandomEnemy when foes.Count == 1 && IsHittableM(foes[0]) => foes,
                    // v0.113.0 的铭记死亡标着打随机敌人，实际打当前生命最低的可攻击敌人（MementoMori.OnPlay：HittableEnemies.MinBy(CurrentHp)），目标确定
                    TargetType.RandomEnemy when acard is MegaCrit.Sts2.Core.Models.Cards.MementoMori && MementoTarget(me) is { } mt => new List<Creature> { mt },
                    _ => null,
                };
                // 格式 29（G20）：打随机敌人、可攻击的敌人 ≥ 2 个时按期望给 4–6，7 为 0（见 Cand29.cs 的 RandomTarget29）
                if (V29 && RandomTarget29(acard, me, out var r4, out var r5, out var r6)) { c4 = r4; c5 = r5; c6 = r6; }
                else if (hitList != null)
                {
                    int plays = PlayCount(acard, null);
                    foreach (var f in hitList)
                    {
                        var (pd, _, hits) = Preview(acard, f);
                        if (pd < 0) break;      // 没有伤害数值（共鸣这类打全体的技能）
                        c4 = Math.Max(c4, pd); c5 = Math.Max(c5, hits); c6 += pd * hits * plays;
                        float lost = FoeHpLoss(f, acard, pd, (int)hits * plays, f.Block);
                        if (lost >= f.CurrentHp) (killed ??= new()).Add(f);
                        if (hl != null) hl[f] = lost;
                    }
                    c7 = killed?.Count ?? 0;
                    Preview(acard, null);
                }
            }
            // 格式 21（静默猎手）：刀刃陷阱、毒性爆发的 4–7，出牌附带的紧勒、群蛇形态算进 4–7，余像的格挡算进 8（见 SilentPreview）
            float riderBlock = 0;
            if (V21 && inCombat && ch.Kind == ActionKind.PlayCard && ch.A is CardModel scard)
                (c4, c5, c6, c7, killed, riderBlock) = SilentPreview(scard, ch.B as Creature, me, c4, c5, c6, c7, killed, hl, pout);
            // 格式 22：狂怒、无惧疼痛、骨笛的格挡，卡戎之灰、势不可当、狱火的伤害，末日降临的处决；出牌的自伤、奥斯提的生命变化给候选 10 用（见 Riders22）
            float selfLoss = 0, ostyEx = 0;
            if (V22 && inCombat && ch.Kind == ActionKind.PlayCard && ch.A is CardModel icard)
                (c4, c5, c6, c7, killed, riderBlock, selfLoss, ostyEx) = Riders22(icard, ch.B as Creature, me, c4, c5, c6, c7, killed, riderBlock);
            // v0.113.0 修正（F113，格式 ≥20）：爆炸箭矢、散雨溅射、暴力和弦、大树将倾、审判的 4–7，歌之道、摇篮曲、万事无忧、合股线的格挡算进 8（见 FoolPreview）
            if (F113 && V20 && inCombat && ch.Kind == ActionKind.PlayCard && ch.A is CardModel fcard)
            {
                float fb;
                (c4, c5, c6, c7, killed, fb) = FoolPreview(fcard, ch.B as Creature, me, c4, c5, c6, c7, killed, hl);
                riderBlock += fb;
            }
            // v0.113.0 修正第四轮（F113）：出牌时遗物附带的格挡进 8（装饰扇、音叉、永冻、威吓头盔），附带伤害（锁镰、开信刀）打死的敌人进候选 10 的不算名单（见 RelicRiders）
            if (F113 && inCombat && ch.Kind == ActionKind.PlayCard && ch.A is CardModel rcard)
            {
                var (rb, rk) = RelicRiders(rcard, ch.B as Creature, me, hl, killed);
                riderBlock += rb;
                if (rk != null) { killed ??= new(); foreach (var f in rk) if (!killed.Contains(f)) killed.Add(f); }
            }
            // 格式 29（G10、G78）：用药水的 4–7（带伤害的药水照出牌口径）、格挡、当场挨的伤害、打死的敌人、发光水的消耗连锁，第 1 步就算好（见 Cand29.cs 的 PotionEffects29）
            Pot29? pot29 = null;
            if (V29 && inCombat && ch.Kind == ActionKind.UsePotion && ch.A is PotionModel p29)
            {
                pot29 = PotionEffects29(p29, ch.B as Creature, me, hl);
                (c4, c5, c6, c7) = (pot29.C4, pot29.C5, pot29.C6, pot29.C7);
                killed = pot29.Killed;
            }
            // 版本 15：占卜的候选 4–7 见 SphereView.Dig
            if (ch.Kind == ActionKind.MinigameCell) (c4, c5, c6, c7) = (dig.a, dig.b, dig.c, dig.d);
            // 版本 16：购买候选 4 买了会员卡之后的价格（会员卡把价格乘 0.5，游戏取整数），5 这件就是会员卡，6 店里在卖会员卡、自己还没有；
            // 不在卖或已经有了时 4 为原价、6 为 0。购买候选的对象 A 是商品本身（卡牌、遗物、药水；删牌为条目），会员卡对删牌同样打折
            if (ch.Kind == ActionKind.Buy)
            {
                bool isCard = ch.A is MegaCrit.Sts2.Core.Models.Relics.MembershipCard;
                c4 = memberOffer && !isCard ? (float)Math.Floor(ch.F0 * 0.5f) : ch.F0; c5 = isCard ? 1 : 0; c6 = memberOffer ? 1 : 0;
            }
            CF.Add(c4); CF.Add(c5); CF.Add(c6); CF.Add(c7);
            // 版本 19（第十七份外部评审，决策记录 227）：候选级的“执行这一步之后还能不能活过本回合”。战斗中：
            // 8 这一步当场得到的格挡（出牌：卡面格挡预览 × 打出次数，含敏捷、脆弱；格挡药水的数值；挤掉最前面的冰霜球时它的激发格挡），
            // 9 这一步让回合结束时冰霜球被动格挡多出多少（生成冰霜球加、挤掉冰霜球减；按卡牌类别查生成哪些球，见 OrbChannels），
            // 10 做完这一步就结束回合预计掉多少血（回合结束预演加上 8、9 的格挡；这一步能打死目标时不算它的攻击，格式 20 打全体时打死几个就去掉几个），11 当前生命减 10。
            // 结束回合候选的 10 为现在的预计掉血；其余战斗候选 8、9 为 0。抽牌、随机效果、减攻击的负面状态不算；战斗外全为 0
            float c8 = 0, c9 = 0, c10 = 0, c11 = 0, g12v = 0, g13v = 0;
            // 格式 29：战斗内 8–13 与新列 14、15、17 改走 CombatCand29（Cand29.cs，包 3）；战斗外的同名计数（新列 14–17，G33）
            if (V29 && inCombat) (c8, c9, c10, c11, g12v, g13v) = CombatCand29(ch, me, killed, hl, pout, riderBlock, selfLoss, ostyEx, pot29);
            else if (V29) DeckCounts29(ch, me);
            else if (inCombat && _lossAfter != null)
            {
                List<Creature>? skip = null;
                try
                {
                    if (ch.Kind == ActionKind.PlayCard && ch.A is CardModel pc2)
                    {
                        var tgt2 = ch.B as Creature;
                        var (_, pb, _) = Preview(pc2, tgt2);
                        // F113：啾 四肢、啾 坚定、亡者之门一次打出获得多次格挡；第五轮：拉伸只给第一次打出（CardBlockTotal）
                        if (pb > 0) c8 = F113 ? CardBlockTotal(pc2, pb, PlayCount(pc2, tgt2)) : pb * PlayCount(pc2, tgt2);
                        Preview(pc2, null);
                        // 第七轮（F113）：激发（双重释放等、球位满了挤掉的）、暴风雨、集中变化也算，激发打死的敌人进不算名单（见 OrbFx）
                        if (F113)
                        {
                            var (imm, pas, ok) = OrbFx(pc2, tgt2, me, hl, killed);
                            c8 += imm; c9 = pas;
                            if (ok != null) { killed ??= new(); foreach (var f in ok) if (!killed.Contains(f)) killed.Add(f); }
                        }
                        else
                        {
                            var (imm, pas) = OrbDelta(pc2, me);
                            c8 += imm; c9 = pas;
                        }
                        skip = killed;
                    }
                    else if (ch.Kind == ActionKind.UsePotion && ch.A is PotionModel pot2)
                    {
                        if (pot2.DynamicVars.TryGetValue("Block", out var pbv)) c8 = (float)pbv.BaseValue;
                        // v0.113.0 修正第四轮（F113）：火焰药水、爆裂安瓿打死的敌人不算它的攻击，强化剂的格挡进 8（见 PotionEffects）
                        if (F113) { var (pblk, pk) = PotionEffects(pot2, ch.B as Creature, me, hl); c8 += pblk; if (pk != null) { killed = pk; skip = pk; } }
                    }
                }
                catch { }
                if (riderBlock > 0) c8 += riderBlock;
                // 格式 22：出牌会让永世沙漏塞一张凋萎进手（战斗计数 19），它在回合结束时打玩家，算进这一步的预演（格式 23 也算）
                float handEx = V22T && ch.Kind == ActionKind.PlayCard ? _nextPlaySelfDmg : 0;
                // 格式 23：这一步给敌人上的虚弱、减力量，让敌人涨的伤害（激怒、感染），加毒、打伤之后毒得死的敌人（见 Fx23）
                Dictionary<Creature, FoeMod>? mod = null;
                // 第七轮（F113）：所有格式都算（格式 19、20、22 原来没有）
                if ((V23 || F113) && ch.Kind is ActionKind.PlayCard or ActionKind.UsePotion)
                {
                    var (m23, pskip) = Fx23(ch, me, hl!, pout ?? new(), killed);
                    mod = m23;
                    if (pskip != null) skip = skip == null ? pskip : skip.Concat(pskip).ToList();
                }
                // 第七轮（F113）：做完这一步之后，回合末确定的被动伤害（玻璃球、只剩一个可攻击敌人时的闪电球，冰雹风暴、炸弹等，见 EndTurnPreview 的 sure）
                // 打得死的敌人不会出手，进不算名单。充能球那几段按这一步之后的球（_orbAfter：激发、生成、集中变化之后）与这一步之后还活着的敌人重算
                // （打死一个之后只剩一个，闪电的被动就打得准）；这一步掉过血的敌人按格挡已打光算
                if (F113 && _sureHits != null && ch.Kind is ActionKind.PlayCard or ActionKind.UsePotion)
                {
                    var csx = me.Creature.CombatState!;
                    var live = Hittable(csx).Where(f => f.IsAlive && !(killed?.Contains(f) ?? false)).ToList();
                    var orbsNow = _orbAfter ?? me.PlayerCombatState!.OrbQueue.Orbs.Select(o => (k: o switch
                    {
                        MegaCrit.Sts2.Core.Models.Orbs.LightningOrb => 'L', MegaCrit.Sts2.Core.Models.Orbs.GlassOrb => 'G', _ => '?',
                    }, pv: (float)o.PassiveVal, trig: PassiveTriggers(csx, o))).ToList();
                    foreach (var f in live)
                    {
                        if ((skip?.Contains(f) ?? false) || !_sureHits.TryGetValue(f, out var sh)) continue;
                        var hits = new List<float>();
                        foreach (var o in orbsNow)
                            for (int t = 0; t < o.trig; t++)
                            {
                                if (o.k == 'L' && live.Count == 1) hits.Add(o.pv);
                                else if (o.k == 'G' && o.pv - t > 0) hits.Add(o.pv - t);
                            }
                        hits.AddRange(sh.hits.Skip(sh.orbN));
                        if (hits.Count == 0) continue;
                        float lost = hl?.GetValueOrDefault(f) ?? 0, blk2 = lost > 0 ? 0 : f.Block, more = 0;
                        foreach (var v in hits)
                        {
                            float sd = v, b2 = Math.Min(blk2, sd); blk2 -= b2; sd -= b2;
                            if (sd <= 0) continue;
                            try { sd = (float)MegaCrit.Sts2.Core.Hooks.Hook.ModifyHpLost(RunSession.State, csx, f, (decimal)sd, MegaCrit.Sts2.Core.ValueProps.ValueProp.Unpowered, me.Creature, null, MegaCrit.Sts2.Core.Hooks.HpLossHookPhase.All, out _); } catch { }
                            more += MathF.Floor(Math.Max(0, sd));
                        }
                        if (Math.Min(lost + more, ShellLeft(f)) >= f.CurrentHp) (skip = skip == null ? new List<Creature>() : skip.ToList()).Add(f);
                    }
                }
                // v0.113.0 修正第四轮（F113，见 Riders113）：敌人对这一步的反应。荆棘反伤、球首的通电进当场挨的伤害（handEx）；
                // 振翅、钻地、犁击、尖啸、贪食让敌人眩晕、这回合不出手（进不算名单）；蟹怒让另一只钳子加力量（格式 23、25 的 mod）。
                // CandFx：这一步改变的回合末条件（奥利哈钢、涟漪盆、斗篷扣、悔恨）与给玩家的覆甲、无实体、缓冲
                CandFx fx = default;
                if (F113)
                {
                    List<Creature>? stun = null;
                    if (ch.Kind == ActionKind.PlayCard && ch.A is CardModel rc2)
                    {
                        var (th, st1) = AttackReactions(rc2, ch.B as Creature, me);
                        handEx += th + GalvanizedSelfDamage(rc2, me);
                        stun = st1;
                    }
                    var (st2, crab) = ThresholdReactions(me.Creature.CombatState!, me, hl, skip);
                    if (st2 != null) stun = stun == null ? st2 : stun.Concat(st2).ToList();
                    if (stun != null) skip = skip == null ? stun : skip.Concat(stun).ToList();
                    if (crab != null && V23)
                    {
                        mod ??= new();
                        foreach (var (f, add) in crab) mod[f] = mod.TryGetValue(f, out var m0) ? m0 with { Add = m0.Add + add } : new FoeMod(add, false);
                    }
                    var (plat, intg, bufN) = DefensiveGains(ch, me);
                    // 打出影融：回合末到账的格挡 × 2^层数（ShadowmeldPower，玩家回合结束之后才撤）
                    float boost = ch.Kind == ActionKind.PlayCard && ch.A is MegaCrit.Sts2.Core.Models.Cards.Shadowmeld smc
                        ? (float)Math.Pow(2, (double)smc.DynamicVars["Power"].BaseValue * PlayCount(smc, null)) - 1 : 0;
                    // 第五轮：自动打出、恶魔消耗整手等额外离手的牌（_autoHand / _autoGone，FoolPreview 填）
                    fx = new CandFx(c8, ch.Kind == ActionKind.PlayCard && ch.A is CardModel ac2 && ac2.Type == CardType.Attack,
                                    ch.Kind == ActionKind.PlayCard ? -1 + _autoHand : 0, plat, intg, bufN, boost, _autoGone);
                }
                // v0.113.0 修正（F113）：打出倒吊人（塔罗）、喝龙涎香会挂上额外回合（ExtraTurnPower），这回合结束后敌人不行动：候选 10 不算任何敌人的攻击
                // （手牌、能力的回合结束自伤照算）；倒吊人自己失去的生命另加（Unblockable | Unpowered | Move，经掉血钩子）
                bool extraNext = F113 && ((ch.Kind == ActionKind.PlayCard && ch.A is MegaCrit.Sts2.Core.Models.Cards.TheHangedMan)
                                          || (ch.Kind == ActionKind.UsePotion && ch.A is MegaCrit.Sts2.Core.Models.Potions.Ambergris));
                if (extraNext) skip = me.Creature.CombatState!.Creatures.Where(c => !c.IsPlayer && c.Side != me.Creature.Side && c.IsAlive).ToList();
                // v0.113.0 修正（F113）：打出的是回合末在手里才生效的牌（召唤之引、毒素），打出后它离手，它那一段回合末自伤不再算
                var gone = F113 && ch.Kind == ActionKind.PlayCard && ch.A is CardModel gc && gc.HasTurnEndInHandEffect ? gc : null;
                // F113 第四轮：现在有佩尔之眼的额外回合时，打出一张牌就没有了（PaelsEye：本回合一张非自动打出的牌都没打才给），出牌候选改用 _lossAfterNoExtra
                var lossFn = _lossAfterFlip != null && SurroundTurns(ch, killed) ? _lossAfterFlip
                    : F113 && ch.Kind == ActionKind.PlayCard && _lossAfterNoExtra != null ? _lossAfterNoExtra : _lossAfter;
                c10 = lossFn(c8 + c9, skip, ostyEx, handEx, mod, gone, fx);
                // 格式 22：这一步自己掉的血（放血、御血术等）也算在“做完这一步就结束回合”的掉血里
                if (V22) c10 += selfLoss;
                if (extraNext && ch.A is MegaCrit.Sts2.Core.Models.Cards.TheHangedMan hm && !(V22 && selfLoss > 0)) c10 += CardSelfHpLoss(me, hm);
                // 格式 20：这回合结束会被贪食者的流沙吞掉时记为当前全部生命；这一下打死贪食者、或打出仓皇逃脱（流沙计数 +1）的除外
                // （格式 23：这一步之后毒得死贪食者的也除外，毒在流沙之前结算）
                if (_devourer != null && !(skip?.Any(x => ReferenceEquals(x, _devourer)) ?? false)
                    && !(ch.Kind == ActionKind.PlayCard && ch.A is MegaCrit.Sts2.Core.Models.Cards.FranticEscape))
                    c10 = me.Creature.CurrentHp;
                c11 = me.Creature.CurrentHp - c10;
            }
            CF.Add(c8); CF.Add(c9); CF.Add(c10); CF.Add(c11);
            // 格式 26：12 这一步当场得到的能量，13 这一步之后进手的牌数（见 CandGains）
            if (V26)
            {
                var (g12, g13) = V29 ? (g12v, g13v) : inCombat ? CandGains(ch, me, killed, ch.Kind == ActionKind.PlayCard ? -1 + _autoHand : 0) : (0f, 0f);
                CF.Add(g12); CF.Add(g13);
            }
            if (V29) CF.AddRange(_cy);      // 格式 29：候选特征 14–19
            if (labels) Labels.Add(ch.Label);
        }
    }
}
