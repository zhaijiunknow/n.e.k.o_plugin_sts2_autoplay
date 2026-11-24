using MegaCrit.Sts2.Core.Combat;
using MegaCrit.Sts2.Core.Commands;
using MegaCrit.Sts2.Core.Entities.Cards;
using MegaCrit.Sts2.Core.Entities.Creatures;
using MegaCrit.Sts2.Core.Entities.FoolArrows;
using MegaCrit.Sts2.Core.Entities.Players;
using MegaCrit.Sts2.Core.Models;

namespace Sts2Headless;

// Fool（游戏 v0.113.0 的第六个角色）专属的观测：格式 24（Obs/Format.cs）。机制依据见适配报告与 fool_analysis（新版反编译源码）：
// - 箭矢：玩家战斗状态里的箭位（PlayerCombatState.Arrows，每支只差状态 Retrieved / Away / Destroyed），射击牌（标签 Shoot）射出最后一支可用的箭，
//   先结算这张牌的伤害再让目标获得扎穿（ImpaledPower：扎着的箭、HitCounter 初值 3）；之后对它每打中一段有力攻击 HitCounter − 1，到 0 拔出最早的一支、
//   重置为 3；目标死亡或逃跑时箭全部回来。没有可用的箭时射击牌照样打出、花能量，但攻击不执行（ArrowCmd.ShootArrowAtTarget）。
// - 旋律：本回合打出过带哇 / 啾 / 叽（CardTag.Wah / Cho / Ki）标签的牌，没有专门字段，按战斗历史现算（MelodyCmd）。
// - 悲伤：大量 Fool 牌往牌堆里塞悲伤（Sadness），苦恼（Anguish）按“本场你生成的悲伤张数”加伤（CardGeneratedEntry）。
public sealed partial class Observation
{
    /// <summary>扎穿每拔出一支箭要打的段数（ImpaledPower 拔出后把 HitCounter 重置为 3）。</summary>
    const int ImpaleHits = 3;

    /// <summary>
    /// 格式 24：玩家的 Fool 战斗计数（战斗计数记录，特征 0 子类型为 4，所属记录为玩家），玩家看得到或数得出来：
    /// 1 箭位总数（含已摧毁），2 可用的箭，3 扎在敌人身上的箭，4 已摧毁的箭，5 本回合射出的箭，6 本场射出的箭，
    /// 7–9 本回合哇、啾、叽是否已激活（0/1），10 旋律种类数（0–3），11 本回合打出的旋律牌张数，12 本场你生成的悲伤张数（苦恼的口径）。
    /// 其他角色因为拿到射击牌而有箭位时同样适用，但只有格式 24（Fool）输出。
    /// </summary>
    void AddFoolStats(ICombatState cs, Player me, int playerTok)
    {
        try
        {
            Begin(); _f[0] = 4;
            var pcs = me.PlayerCombatState!;
            var arrows = pcs.Arrows;
            _f[1] = arrows.Count;
            _f[2] = arrows.Count(a => a.Status == ArrowStatus.Retrieved);
            _f[3] = arrows.Count(a => a.Status == ArrowStatus.Away);
            _f[4] = arrows.Count(a => a.Status == ArrowStatus.Destroyed);
            foreach (var e in CombatManager.Instance.History.Entries)
            {
                switch (e)
                {
                    case MegaCrit.Sts2.Core.Combat.History.Entries.ArrowShotEntry shot when shot.Slot?.owner == me:
                        _f[6]++;
                        if (e.HappenedThisTurn(cs)) _f[5]++;
                        break;
                    case MegaCrit.Sts2.Core.Combat.History.Entries.CardGeneratedEntry gen when gen.Creator == me && gen.Card is MegaCrit.Sts2.Core.Models.Cards.Sadness:
                        _f[12]++; break;
                }
            }
            _f[7] = PlayedWah(me) ? 1 : 0;
            _f[8] = PlayedCho(me) ? 1 : 0;
            _f[9] = PlayedKi(me) ? 1 : 0;
            _f[10] = MegaCrit.Sts2.Core.Commands.MelodyCmd.MelodiesThisTurn(me);
            _f[11] = MegaCrit.Sts2.Core.Commands.MelodyCmd.MelodyCardsPlayedThisTurn(me);
            Emit(null, TokenKind.CombatStat, 0, Zone.Ally, playerTok, 0);
        }
        catch { }
    }

    /// <summary>格式 24：扎穿的能力记录 12 扎着几支箭，13 全部拔出还要打几段有力攻击（当前这支还差 HitCounter 段，之后每支 3 段；扎着的箭先进先出）。</summary>
    static (float arrows, float hitsToFree) ImpaledInfo(MegaCrit.Sts2.Core.Models.Powers.ImpaledPower imp)
    {
        try
        {
            int n = imp.Arrows.Count;
            return (n, n > 0 ? imp.HitCounter + ImpaleHits * (n - 1) : 0);
        }
        catch { return (0, 0); }
    }

    /// <summary>射击牌（标签 Shoot）且打出的人现在没有可用的箭：攻击不会执行。</summary>
    static bool ShootDisabled(CardModel c)
    {
        try { return c.Tags.Contains(CardTag.Shoot) && c.Owner?.PlayerCombatState is { } pcs && pcs.AvailableArrowCount == 0; }
        catch { return false; }
    }

    /// <summary>钉住（Nail）的目标被箭扎穿着（ArrowCmd.CreatureIsImpaledByArrow，与 Nail.OnPlay 同一判断）。</summary>
    static bool NailHits(Creature target)
    {
        try { return MegaCrit.Sts2.Core.Commands.ArrowCmd.CreatureIsImpaledByArrow(target); }
        catch { return true; }
    }

    // ------------------------------------------------------------------ 格式 25（Fool 第二版，游戏更新v0.113.0/观测独立核查.md）

    static bool IsShoot(CardModel c) { try { return c.Tags.Contains(CardTag.Shoot); } catch { return false; } }

    /// <summary>出牌时对自己造成的 HpLoss（倒吊人等：CreatureCmd.Damage 自己，Unblockable | Unpowered | Move），经掉血钩子，截掉小数。同 Ironclad 的 SelfHpLoss。</summary>
    static float CardSelfHpLoss(Player me, CardModel card)
    {
        try
        {
            var cs = me.Creature.CombatState!;
            var props = MegaCrit.Sts2.Core.ValueProps.ValueProp.Unblockable | MegaCrit.Sts2.Core.ValueProps.ValueProp.Unpowered | MegaCrit.Sts2.Core.ValueProps.ValueProp.Move;
            decimal d = card.DynamicVars.HpLoss.BaseValue;
            d = MegaCrit.Sts2.Core.Hooks.Hook.ModifyHpLost(cs.RunState, cs, me.Creature, d, props, me.Creature, card, MegaCrit.Sts2.Core.Hooks.HpLossHookPhase.All, out _);
            return MathF.Floor((float)d);
        }
        catch { return 0; }
    }

    /// <summary>这个生物本回合受到过没被挡住的伤害（怨恨的段数条件，同 Spite.LostHpThisTurn）。</summary>
    static bool LostHpThisTurn(Creature c)
    {
        try { return CombatManager.Instance.History.Entries.OfType<MegaCrit.Sts2.Core.Combat.History.Entries.DamageReceivedEntry>().Any(e => e.HappenedThisTurn(c.CombatState!) && e.Receiver == c && e.Result.UnblockedDamage > 0); }
        catch { return true; }
    }

    /// <summary>出牌时攻击段数经 Hook.ModifyAttackHitCount 修正（拇指夹：多段攻击 +1 段；与 XHits 的 H 同一写法）。</summary>
    static float ModHits(CardModel c, int h)
    {
        try { return c.CombatState is { } cs ? (float)MegaCrit.Sts2.Core.Hooks.Hook.ModifyAttackHitCount(cs, MegaCrit.Sts2.Core.Commands.DamageCmd.Attack(0m).FromCard(c, null), h) : h; }
        catch { return h; }
    }

    /// <summary>v0.113.0 修正（F113）：这张牌每次打出实际获得几次卡面格挡。啾 四肢固定两次（ChoLimbs.OnPlay 循环两次 GainBlock）；
    /// 啾 坚定有可用的箭时两次（ChoFirm.OnPlay：ArrowCmd.PlayerHasAnyArrows 则多一次）；其余一次。拉伸按每次获得格挡各加一次，卡面预览的格挡已含它。</summary>
    static int BlockGains(CardModel c)
    {
        try
        {
            return c switch
            {
                MegaCrit.Sts2.Core.Models.Cards.ChoLimbs => 2,
                MegaCrit.Sts2.Core.Models.Cards.ChoFirm => c.Owner is { } p && MegaCrit.Sts2.Core.Commands.ArrowCmd.PlayerHasAnyArrows(p) ? 2 : 1,
                // 亡者之门（亡灵契约师）：本回合你施加过灾厄时多 Repeat（2）次（DeathsDoor.OnPlay，WasDoomAppliedThisTurn：本回合、施加者为你的 PowerReceivedEntry）
                MegaCrit.Sts2.Core.Models.Cards.DeathsDoor when c.Owner?.Creature is { } oc && oc.CombatState is { } ocs
                    && CombatManager.Instance.History.Entries.OfType<MegaCrit.Sts2.Core.Combat.History.Entries.PowerReceivedEntry>()
                        .Any(e => e.HappenedThisTurn(ocs) && e.Power is MegaCrit.Sts2.Core.Models.Powers.DoomPower && e.Applier == oc) => 1 + c.DynamicVars.Repeat.IntValue,
                _ => 1,
            };
        }
        catch { return 1; }
    }

    /// <summary>v0.113.0 修正（F113）：这张牌每次打出给你生成几张悲伤（万事无忧按张数给格挡；AllIsWellPower.AfterCardGeneratedForCombat，生成者为你）。
    /// 说笑、哀伤打击按 Cards，翻找、刻薄 1 张，活跃气氛按 "Sadness"；悲痛一击是最后一次打出后整张变成悲伤（按张算一次，调用处处理）。</summary>
    static int SadnessMade(CardModel c)
    {
        try
        {
            return c switch
            {
                MegaCrit.Sts2.Core.Models.Cards.Jest or MegaCrit.Sts2.Core.Models.Cards.SorrowfulStrike => c.DynamicVars.Cards.IntValue,
                MegaCrit.Sts2.Core.Models.Cards.Rummage or MegaCrit.Sts2.Core.Models.Cards.Vitriol => 1,
                MegaCrit.Sts2.Core.Models.Cards.BrightenUp => c.DynamicVars["Sadness"].IntValue,
                _ => 0,
            };
        }
        catch { return 0; }
    }

    /// <summary>一段伤害只过伤害上限钩子（ModifyDamageHookType.Cap，散雨射击的溅射这样打），截掉小数。只读。</summary>
    static float CapDmg(ICombatState cs, Creature target, Creature? dealer, decimal amount, MegaCrit.Sts2.Core.ValueProps.ValueProp props, CardModel? card)
    {
        try { return MathF.Floor((float)MegaCrit.Sts2.Core.Hooks.Hook.ModifyDamage(cs.RunState, cs, target, dealer, amount, props, card, null, MegaCrit.Sts2.Core.Hooks.ModifyDamageHookType.Cap, CardPreviewMode.None, out _)); }
        catch { return MathF.Floor((float)amount); }
    }

    /// <summary>
    /// v0.113.0 修正（F113）：出牌候选 4–7 补上 Fool 的附带伤害、斩杀，另返回附带格挡（算进候选 8）。照游戏的结算顺序逐次打出（每次打出先 OnPlay，再 AfterCardPlayed）：
    ///   这张牌自己的伤害：有目标、打全体、只剩一个敌人的打随机，各按界面预览的单次伤害与段数（同 SilentPreview 的口径）。
    ///     射击牌（标签 Shoot）打出多次（叽 诗句等）时，只有前“可用箭数”次射出箭、造成伤害（ArrowCmd.ShootArrowAtTarget 没箭时整个攻击不执行；中途拔回的箭不算，偏保守）。
    ///   射出箭时（扎穿，ArrowCmd.ImpaleTarget：先结算伤害、再扎穿）：
    ///     爆炸箭矢：每次扎穿对所有可攻击的敌人造成层数点 Unpowered 伤害（ExplosiveArrowsPower.AfterArrowImpaled；目标这一箭死了也照样触发）；
    ///     散雨射击：把对目标造成的伤害（含溢出，即对目标的预览伤害）以 Move、只过伤害上限钩子的方式打其余可攻击的敌人（RainShot.OnPlay）。
    ///   大树将倾：这一击打死目标时，对其余敌人各打 OnKillDamage（按对它的界面预览）。审判：目标当前生命 ≤ 阈值时直接斩杀（候选 7 为 1）。
    ///   附带效果（AfterCardPlayed）：暴力和弦：本回合第一张哇（MelodyCmd.IsFirstPlayThisTurn）对所有可攻击的敌人造成层数点 Unpowered 伤害，只在第一次打出时。
    ///   附带格挡：歌之道（本回合第一张啾，层数点）、摇篮曲（这一张打完本回合三种旋律都有、本回合还没触发，层数点）、万事无忧（每生成一张悲伤，层数点）、
    ///     合股线（每拔出一支你的箭 4 点：对被你扎穿的目标打满 HitCounter 段有力攻击拔出一支，目标被打死时扎着的箭全部拔出；召回、连根拔起、拧下拔光），
    ///     都是 Unpowered（不吃敏捷、拉伸），经 Hook.ModifyBlock（同余像）。
    /// 4–7 的口径同 SilentPreview：有目标时 4 单次伤害、5 每次打出的段数、6 对目标的总伤害（自己的加上落在目标上的附带伤害，不扣格挡）、7 能不能打死目标；
    /// 打全体时 6 为各敌人之和、7 为打死几个。附带伤害打死的其他敌人也记进 killed（候选 10 不算它们的攻击）。有附带伤害时按这里的口径重算（不再含格式 21 的紧勒、群蛇形态），
    /// 没有时 4–7 原样返回。第五轮（F113）：重放时瞄准、寂灭只算第一次（PdNoAim）；自动打出的牌（AutoPlays）的伤害、格挡算进来，
    /// 能量与进手记在 _autoGain、离手的牌记在 _autoHand / _autoGone（候选 10、12、13 用）。只读。
    /// </summary>
    (float c4, float c5, float c6, float c7, List<Creature>? killed, float block) FoolPreview(CardModel card, Creature? tgt, Player me,
        float c4, float c5, float c6, float c7, List<Creature>? killed, Dictionary<Creature, float>? hpOut)
    {
        float block = 0;
        try
        {
            var cs = me.Creature.CombatState!;
            var pcs = me.PlayerCombatState!;
            var foes = cs.Creatures.Where(c => !c.IsPlayer && c.Side != me.Creature.Side && c.IsAlive).ToList();
            var hittable = foes.Where(c => Hittable(cs).Contains(c)).ToList();
            var unpowered = MegaCrit.Sts2.Core.ValueProps.ValueProp.Unpowered;
            var move = MegaCrit.Sts2.Core.ValueProps.ValueProp.Move;
            int plays = PlayCount(card, tgt);
            bool shoot = IsShoot(card);
            int avail = pcs.AvailableArrowCount;

            // 附带格挡（与伤害无关的几项）
            bool wah = PlayedWah(me), cho = PlayedCho(me), ki = PlayedKi(me);
            bool tWah = card.Tags.Contains(CardTag.Wah), tCho = card.Tags.Contains(CardTag.Cho), tKi = card.Tags.Contains(CardTag.Ki);
            int songPath = tCho && !cho ? me.Creature.GetPowerAmount<MegaCrit.Sts2.Core.Models.Powers.SongPathPower>() : 0;
            if (songPath > 0) block += AfterimageBlock(cs, me, songPath);
            if (me.Creature.GetPower<MegaCrit.Sts2.Core.Models.Powers.LullabyPower>() is { } lul && !PowerUsed(lul) && (wah || tWah) && (cho || tCho) && (ki || tKi))
                block += AfterimageBlock(cs, me, lul.Amount);
            int well = me.Creature.GetPowerAmount<MegaCrit.Sts2.Core.Models.Powers.AllIsWellPower>();
            if (well > 0)
            {
                int sad = card is MegaCrit.Sts2.Core.Models.Cards.MourningBlow ? 1 : SadnessMade(card) * plays;
                if (sad > 0) block += sad * AfterimageBlock(cs, me, well);
            }

            // 自己的伤害（逐敌人的界面预览）
            List<Creature>? hitList = tgt != null ? new List<Creature> { tgt } : card.TargetType switch
            {
                TargetType.AllEnemies => foes.Where(c => IsHittableM(c)).ToList(),
                TargetType.RandomEnemy when foes.Count == 1 && IsHittableM(foes[0]) => foes,
                _ => null,
            };
            var per = new Dictionary<Creature, (float pd, int hits)>();
            var perX = new Dictionary<Creature, float>();      // 第五轮：重放时的单段伤害（瞄准、寂灭只加在第一次箭攻击上，见 PdNoAim）
            foreach (var f in hitList ?? new List<Creature>())
            {
                var (pd, _, h) = Preview(card, f);
                if (pd < 0) { per.Clear(); perX.Clear(); break; }
                per[f] = (pd, (int)h);
                perX[f] = plays > 1 ? PdNoAim(card, f, pd) : pd;
            }
            Preview(card, null);
            int shots = shoot ? Math.Min(plays, avail) : 0;
            if (shoot && per.Count > 0 && per.Values.All(v => v.hits == 0)) shots = 0;      // 重复射击 X 为 0：整张牌不执行，不射箭
            int explo = me.Creature.GetPowerAmount<MegaCrit.Sts2.Core.Models.Powers.ExplosiveArrowsPower>();
            int chord = tWah && !wah ? me.Creature.GetPowerAmount<MegaCrit.Sts2.Core.Models.Powers.ViolentChordPower>() : 0;
            bool rain = card is MegaCrit.Sts2.Core.Models.Cards.RainShot && tgt != null;
            bool timber = card is MegaCrit.Sts2.Core.Models.Cards.Timber && tgt != null;
            bool judge = card is MegaCrit.Sts2.Core.Models.Cards.Judgement && tgt != null;
            var twine = me.Relics.FirstOrDefault(r => r is MegaCrit.Sts2.Core.Models.Relics.Twine && !r.IsMelted && r.Status != MegaCrit.Sts2.Core.Entities.Relics.RelicStatus.Disabled);

            if (judge)
            {
                // 审判：目标活着且当前生命 ≤ 阈值时直接把生命设为 0（Judgement.OnPlay，CreatureCmd.SetCurrentHp）
                if (tgt!.IsAlive && tgt.CurrentHp <= card.DynamicVars["Threshold"].IntValue)
                {
                    c7 = 1; killed = new() { tgt };
                    if (hpOut != null) hpOut[tgt] = tgt.CurrentHp;
                }
                return (c4, c5, c6, c7, killed, block);
            }

            // 第五轮：这张牌自动打出的牌（啾 副歌、塔罗“愚者”“恶魔”、失落鲁特琴，见 AutoPlays），它们的伤害、格挡、能量与进手算进这一步
            var (apList, apHand, apGone) = AutoPlays(card, me);
            // 格式 29（G02）：Riders29 先记下的整手消耗、浩劫顶牌的能量与进手在这里累加、合并，不再覆盖
            if (V29)
            {
                _autoHand += apHand;
                if (apGone != null) _autoGone = _autoGone == null ? apGone : _autoGone.Concat(apGone.Where(g => !_autoGone.Contains(g))).ToList();
            }
            else { _autoHand = apHand; _autoGone = apGone; }
            var ap = apList.Count > 0 ? AutoPlayEffect(apList, me) : default;
            if (apList.Count > 0) { block += ap.block; _autoGain = V29 ? (_autoGain.energy + ap.energy, _autoGain.cards + ap.cards) : (ap.energy, ap.cards); }
            bool aimReplay = plays > 1 && per.Any(kv => perX[kv.Key] < kv.Value.pd);
            bool dmgRiders = (shots > 0 && (explo > 0 || rain || shots < plays)) || chord > 0 || timber
                || aimReplay || (apList.Count > 0 && (ap.sure.Values.Any(l => l.Count > 0) || ap.exp.Values.Any(v => v > 0)));
            bool tgtDead = tgt != null && c7 > 0;
            if (dmgRiders)
            {
                var seq = foes.ToDictionary(f => f, _ => new List<Hit>());
                var own = foes.ToDictionary(f => f, _ => 0f);
                var extra = foes.ToDictionary(f => f, _ => 0f);
                float ownMax = 0; int ownHits = 0;
                void Add(Creature f, Hit h, bool isOwn)
                {
                    if (!seq.ContainsKey(f)) return;
                    seq[f].Add(h);
                    if (isOwn) { own[f] += h.Dmg; ownMax = Math.Max(ownMax, h.Dmg); } else extra[f] += h.Dmg;
                }
                bool Dead(Creature f) => seq[f].Count > 0 && SeqHpLoss(f, seq[f]) >= f.CurrentHp;
                foreach (var (_, v) in per) ownHits = Math.Max(ownHits, v.hits);
                for (int r = 0; r < plays; r++)
                {
                    bool armed = !shoot || r < shots;
                    if (armed)
                        foreach (var (f, v) in per)
                            for (int i = 0; i < v.hits; i++) Add(f, new Hit(r == 0 ? v.pd : perX[f], true, move, me.Creature, card), true);
                    if (shoot && armed && tgt != null)
                    {
                        if (rain && per.TryGetValue(tgt, out var tv))
                            foreach (var o in hittable)
                                if (!ReferenceEquals(o, tgt)) Add(o, new Hit(CapDmg(cs, o, me.Creature, (decimal)tv.pd, move, card), true, move, me.Creature, card), false);
                        if (explo > 0)
                            foreach (var f in hittable)
                                if (!(ReferenceEquals(f, tgt) && Dead(f))) Add(f, new Hit(HitDmg(cs, f, me.Creature, explo, unpowered, null), true, unpowered, me.Creature, null), false);
                    }
                    if (timber && armed && Dead(tgt!))
                    {
                        foreach (var o in hittable)
                        {
                            if (ReferenceEquals(o, tgt) || Dead(o)) continue;
                            Preview(card, o);
                            if (card.DynamicVars.TryGetValue("OnKillDamage", out var ok)) Add(o, new Hit(MathF.Floor((float)ok.PreviewValue), true, move, me.Creature, card), false);
                        }
                        Preview(card, null);
                    }
                    if (r == 0 && chord > 0)
                        foreach (var f in hittable) Add(f, new Hit(HitDmg(cs, f, me.Creature, chord, unpowered, null), true, unpowered, me.Creature, null), false);
                }
                // 自动打出的牌：确定的逐段记进去，按期望摊的只进 6
                if (apList.Count > 0)
                    foreach (var f in foes)
                    {
                        if (ap.sure.TryGetValue(f, out var hs)) foreach (var h in hs) if (!Dead(f)) Add(f, h, false);
                        if (ap.exp.TryGetValue(f, out var ev)) extra[f] += ev;
                    }
                var dead = foes.Where(Dead).ToList();
                if (hpOut != null) foreach (var f in foes) if (seq[f].Count > 0) hpOut[f] = SeqHpLoss(f, seq[f]);
                if (tgt != null)
                {
                    if (per.TryGetValue(tgt, out var tv2)) c4 = tv2.pd;
                    c5 = ownHits; c6 = own[tgt] + extra[tgt]; c7 = dead.Contains(tgt) ? 1 : 0;
                    tgtDead = c7 > 0;
                }
                else
                {
                    c4 = ownMax > 0 ? ownMax : foes.Select(f => seq[f].Select(h => h.Dmg).DefaultIfEmpty(0).Max()).DefaultIfEmpty(0).Max();
                    c5 = ownHits; c6 = foes.Sum(f => own[f] + extra[f]); c7 = dead.Count;
                }
                killed = dead.Count > 0 ? dead : null;
            }

            // 合股线：这一步拔出几支你的箭（第五轮：改用 ArrowsOut，寂灭标过的第一支扎穿即摧毁、目标死了也不回来）
            if (twine != null && tgt != null)
            {
                var (dh, dd, _) = ArrowsOut(card, tgt, me, tgtDead);
                int d = dh + dd;
                if (d > 0) block += d * AfterimageBlock(cs, me, (int)twine.DynamicVars.Block.BaseValue);
            }
        }
        catch { }
        return (c4, c5, c6, c7, killed, block);
    }

    /// <summary>
    /// v0.113.0 修正（F113）：傻瓜（SimpletonPower）在回合末的自动出牌阶段（AfterAutoPostPlayPhaseEntered，回合结束效果之前）打出几张牌：每层一次，
    /// 从手里不带“不能打出”的牌里取费用最低的一组（X 费排最后），并列时按随机数挑一张、自动打出（不花能量，无目标的按随机目标）。
    /// 这里只在最低费用的那组只有一张、或几张完全相同（同一张牌、同升级、同附魔）时才算是确定的；遇到真随机就停（后面的也不算）。
    /// 第五轮（F113）：相同改用 SameCard（附魔层数、苦痛也要一样）；剩下的次数不少于这一组的张数时整组都会打出（顺序随机，但打出哪些是确定的），也算进来。只读。
    /// </summary>
    static List<CardModel> SimpletonPlays(PlayerCombatState pcs, int times)
    {
        var res = new List<CardModel>();
        try
        {
            var left = pcs.Hand.Cards.ToList();
            while (times > 0)
            {
                var g = left.Where(c => !Kw(c).Contains(CardKeyword.Unplayable))
                    .GroupBy(c => !c.EnergyCost.CostsX ? c.EnergyCost.GetResolved() : 999999999).OrderBy(x => x.Key).FirstOrDefault();
                if (g == null) break;
                var first = g.First();
                bool same = F113
                    ? g.All(c => SameCard(c, first))
                    : !g.Any(c => Vocab.Id(c) != Vocab.Id(first) || c.CurrentUpgradeLevel != first.CurrentUpgradeLevel || Vocab.Id(c.Enchantment) != Vocab.Id(first.Enchantment));
                if (same) { res.Add(first); left.Remove(first); times--; continue; }
                if (F113 && g.Count() <= times)
                {
                    foreach (var c in g.ToList()) { res.Add(c); left.Remove(c); }
                    times -= g.Count();
                    continue;
                }
                break;
            }
        }
        catch { }
        return res;
    }

    /// <summary>v0.113.0 修正（F113）：玩家活着的瓦皮（我方宠物，挂着 WuppysLoyaltyPower：格挡之前替主人挨有力攻击，CreatureCmd.Damage 的 ModifyPreBlockDamageTarget）。</summary>
    static Creature? WuppyOf(ICombatState cs, Player me)
    {
        try { return cs.Creatures.FirstOrDefault(c => !c.IsPlayer && c.IsAlive && c.PetOwner == me && c.HasPower<MegaCrit.Sts2.Core.Models.Powers.WuppysLoyaltyPower>()); }
        catch { return null; }
    }
}
