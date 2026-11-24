using MegaCrit.Sts2.Core.Combat;
using MegaCrit.Sts2.Core.Entities.Cards;
using MegaCrit.Sts2.Core.Entities.Creatures;
using MegaCrit.Sts2.Core.Entities.Players;
using MegaCrit.Sts2.Core.Models;

namespace Sts2Headless;

// 格式 22：出牌附带效果的候选 4–8 与候选 10 的输入（铁甲与亡灵契约师观测调研.md 第三节）。
public sealed partial class Observation
{
    /// <summary>
    /// 格式 22：在格式 21 的结果（SilentPreview 之后的 4–7、击杀、格挡）上，补上铁甲战士、亡灵契约师常见的出牌附带效果：
    ///   格挡（算进候选 8）：狂怒（打出攻击牌，层数点 Unpowered 格挡）、无惧疼痛（这张牌打出后被消耗）、骨笛（奥斯提出手的牌，每次打出 2 点）。
    ///   伤害（算进 4–7，并从候选 10 里去掉打死的敌人）：卡戎之灰（这张牌被消耗时打全体）、势不可当（Juggernaut：每次获得格挡打随机一个敌人，
    ///   只剩一个可攻击的敌人时是确定的，多个时只按期望摊进 6、不算击杀）、狱火（Inferno：自己回合里掉血就打全体，配合自伤牌）、
    ///   末日降临（给全体上灾厄后当场处决生命 ≤ 灾厄的敌人）。
    /// 另返回：这张牌的自伤（SelfHpLoss × 打出次数，候选 10 加上它）、奥斯提的生命变化（OstyChange，候选 10 的回合结束预演用）。
    /// 有伤害类附带效果时按格式 21 的口径重算 4–7（这张牌自己的伤害按界面预览，有目标的攻击按目标口径，其余按“打全体”口径），
    /// 紧勒、群蛇形态照格式 21 一起算进去；刀刃陷阱、毒性爆发（静默猎手的牌，自己的伤害另有算法）只加格挡、不重算。
    /// 不算的：打出过程中消耗别的牌（要看选了哪些）引起的无惧疼痛、卡戎之灰；狂怒、骨笛等附带格挡再引起的势不可当算在内，其余连锁不算。
    /// </summary>
    (float c4, float c5, float c6, float c7, List<Creature>? killed, float block, float selfLoss, float ostyEx) Riders22(CardModel card, Creature? tgt, Player me,
        float c4, float c5, float c6, float c7, List<Creature>? killed, float block)
    {
        if (V29) return Riders29(card, tgt, me, c4, c5, c6, c7, killed, block);      // 格式 29 的新口径（Riders29.cs），本函数原样留给格式 22、28
        try
        {
            var cs = me.Creature.CombatState!;
            var foes = cs.Creatures.Where(c => !c.IsPlayer && c.Side != me.Creature.Side && c.IsAlive).ToList();
            var hittable = foes.Where(c => Hittable(cs).Contains(c)).ToList();
            var unpowered = MegaCrit.Sts2.Core.ValueProps.ValueProp.Unpowered;
            var move = MegaCrit.Sts2.Core.ValueProps.ValueProp.Move;
            int plays = PlayCount(card, tgt);
            float selfPer = SelfHpLoss(cs, me, card), self = selfPer * plays;
            float ostyEx = OstyChange(cs, me, card, plays);
            bool exhausts = ExhaustsOnPlay(card, me);
            int exhaustN = (exhausts ? 1 : 0) + ExhaustOthers(card, me);      // 这张牌打出后消耗的张数（自己 + 别的牌）
            int ownGains = IroncladCounts(card, me, 1).gains;                  // 每次打出获得几次格挡（重振精神按消耗张数、邪眼两次）
            int rage = me.Creature.GetPowerAmount<MegaCrit.Sts2.Core.Models.Powers.RagePower>();
            int fnp = me.Creature.GetPowerAmount<MegaCrit.Sts2.Core.Models.Powers.FeelNoPainPower>();
            int jugg = me.Creature.GetPowerAmount<MegaCrit.Sts2.Core.Models.Powers.JuggernautPower>();
            int inferno = me.Creature.GetPowerAmount<MegaCrit.Sts2.Core.Models.Powers.InfernoPower>();
            int image = me.Creature.GetPowerAmount<MegaCrit.Sts2.Core.Models.Powers.AfterimagePower>();
            var charon = me.Relics.FirstOrDefault(r => r is MegaCrit.Sts2.Core.Models.Relics.CharonsAshes && RelicLive(r));
            var flute = me.Relics.FirstOrDefault(r => r is MegaCrit.Sts2.Core.Models.Relics.BoneFlute && RelicLive(r));
            bool ostyAtk = card.Tags.Contains(CardTag.OstyAttack) && me.IsOstyAlive;

            // 获得格挡的每一次（势不可当按次数触发）：这张牌自己的格挡、余像（格式 21 已算进 block）、狂怒、无惧疼痛、骨笛
            var gains = new List<float>();
            var (_, ownBlock, _) = Preview(card, tgt);
            Preview(card, null);
            float add = 0;
            for (int r = 0; r < plays; r++)
            {
                if (ownBlock > 0) for (int g = 0; g < ownGains; g++) { gains.Add(ownBlock); if (g > 0) add += ownBlock; }     // 候选 8 已经算了一次，多出来的次数补上
                if (image > 0) gains.Add(AfterimageBlock(cs, me, image));
            }
            void Gain(float b, int times) { for (int i = 0; i < times; i++) { gains.Add(b); add += b; } }
            if (rage > 0 && card.Type == CardType.Attack) Gain(AfterimageBlock(cs, me, rage), plays);
            if (fnp > 0 && exhaustN > 0) Gain(AfterimageBlock(cs, me, fnp), exhaustN);
            if (flute != null && ostyAtk && flute.DynamicVars.TryGetValue("Block", out var fb)) Gain(AfterimageBlock(cs, me, fb.IntValue), plays);

            int juggHits = jugg > 0 ? gains.Count(b => b > 0) : 0;
            bool charonHit = charon != null && exhaustN > 0 && charon.DynamicVars.TryGetValue("Damage", out _);
            int infernoHits = inferno > 0 && selfPer > 0 ? plays : 0;
            bool doom = card is MegaCrit.Sts2.Core.Models.Cards.EndOfDays;
            // 亡灵精通：奥斯提每掉 1 点血，对全体可攻击的敌人造成层数点伤害（不吃格挡）；奥斯提被牺牲（牺牲、碎骨）时掉的是它的全部生命
            int necro = me.Creature.GetPowerAmount<MegaCrit.Sts2.Core.Models.Powers.NecroMasteryPower>();
            float necroLoss = necro > 0 && OstyKillCards.Contains(card.GetType()) && me.IsOstyAlive ? me.Osty!.CurrentHp : 0;
            bool silentOwn = card is MegaCrit.Sts2.Core.Models.Cards.KnifeTrap or MegaCrit.Sts2.Core.Models.Cards.Outbreak;
            if (silentOwn || (juggHits == 0 && !charonHit && infernoHits == 0 && !doom && necroLoss <= 0))
                return (c4, c5, c6, c7, killed, block + add, self, ostyEx);

            // 重算 4–7：每个敌人的一串伤害（这张牌自己的 + 附带的），按游戏的结算顺序逐次打出
            var seq = foes.ToDictionary(f => f, _ => new List<Hit>());
            var own = foes.ToDictionary(f => f, _ => 0f);
            var extra = foes.ToDictionary(f => f, _ => 0f);
            float ownMax = 0, ownHits = 0;
            var strangle = foes.Select(f => (f, amt: f.Powers.OfType<MegaCrit.Sts2.Core.Models.Powers.StranglePower>().Where(p => p.Applier?.Player == me).Sum(p => p.Amount)))
                .Where(x => x.amt > 0).ToList();
            int serpent = me.Creature.GetPowerAmount<MegaCrit.Sts2.Core.Models.Powers.SerpentFormPower>();
            var poisonProps = MegaCrit.Sts2.Core.ValueProps.ValueProp.Unblockable | unpowered;
            void Det(Creature f, float d) { seq[f].Add(new Hit(d, true, unpowered, me.Creature, null)); extra[f] += d; }
            void RandomHit(float amount) // 打随机一个可攻击的敌人：只剩一个时确定，多个时按期望
            {
                if (hittable.Count == 1) Det(hittable[0], HitDmg(cs, hittable[0], me.Creature, (decimal)amount, unpowered, null));
                else foreach (var f in hittable) extra[f] += HitDmg(cs, f, me.Creature, (decimal)amount, unpowered, null) / hittable.Count;
            }
            void Riders21()
            {
                if (serpent > 0) RandomHit(serpent);
                foreach (var (f, amt) in strangle)
                {
                    var h = new Hit(HitDmg(cs, f, null, amt, poisonProps, null), false, poisonProps, null, null);
                    seq[f].Add(h); extra[f] += h.Dmg;
                }
            }
            List<Creature>? hitList = tgt != null ? new List<Creature> { tgt } : card.TargetType switch
            {
                TargetType.AllEnemies => foes.Where(c => IsHittableM(c)).ToList(),
                TargetType.RandomEnemy when foes.Count == 1 && IsHittableM(foes[0]) => foes,
                // F113：v0.113.0 的铭记死亡打当前生命最低的可攻击敌人（同 Candidates、SilentPreview）
                TargetType.RandomEnemy when F113 && card is MegaCrit.Sts2.Core.Models.Cards.MementoMori && MementoTarget(me) is { } mt => new List<Creature> { mt },
                _ => null,
            };
            bool all = tgt == null;
            var per = new Dictionary<Creature, (float pd, int hits)>();
            foreach (var f in hitList ?? new List<Creature>())
            {
                var (pd, _, hits) = Preview(card, f);
                if (pd < 0) { per.Clear(); break; }
                per[f] = (pd, (int)hits);
            }
            Preview(card, null);
            if (per.Count == 0) all = true;
            foreach (var (_, v) in per) { ownHits = Math.Max(ownHits, v.hits); ownMax = Math.Max(ownMax, v.pd); }
            // 自伤先于这张牌的攻击（御血术等先掉血再出手），狱火随自伤触发
            for (int r = 0; r < plays; r++)
            {
                if (infernoHits > 0) foreach (var f in hittable) Det(f, HitDmg(cs, f, me.Creature, inferno, unpowered, null));
                foreach (var (f, v) in per)
                    for (int i = 0; i < v.hits; i++) { var h = new Hit(v.pd, true, move, me.Creature, card); seq[f].Add(h); own[f] += h.Dmg; }
                Riders21();
            }
            for (int i = 0; i < juggHits; i++) RandomHit(jugg);
            if (necroLoss > 0) foreach (var f in hittable) { var h = new Hit(HitDmg(cs, f, me.Creature, (decimal)(necroLoss * necro), poisonProps, null), false, poisonProps, me.Creature, null); seq[f].Add(h); extra[f] += h.Dmg; }
            if (charonHit) { var dv = charon!.DynamicVars["Damage"]; for (int x = 0; x < exhaustN; x++) foreach (var f in hittable) Det(f, HitDmg(cs, f, me.Creature, dv.BaseValue, unpowered, null)); }

            var dead = foes.Where(f => seq[f].Count > 0 && SeqHpLoss(f, seq[f]) >= f.CurrentHp).ToList();
            // 末日降临：给每个可攻击的敌人加灾厄（加能力钩子），再处决生命 ≤ 灾厄的（DoomPower.DoomKill）
            if (doom && card.DynamicVars.TryGetValue("DoomPower", out var doomVar))
            {
                var canon = ModelDb.Power<MegaCrit.Sts2.Core.Models.Powers.DoomPower>();
                foreach (var f in hittable)
                {
                    if (dead.Contains(f) || !f.CanReceivePowers) continue;
                    var pw = (PowerModel?)f.GetPower<MegaCrit.Sts2.Core.Models.Powers.DoomPower>() ?? canon;
                    decimal given = MegaCrit.Sts2.Core.Hooks.Hook.ModifyPowerAmountGiven(cs, pw, me.Creature, doomVar.BaseValue * plays, f, card, out _);
                    int now = f.GetPowerAmount<MegaCrit.Sts2.Core.Models.Powers.DoomPower>() + (int)MegaCrit.Sts2.Core.Hooks.Hook.ModifyPowerAmountReceived(cs, pw, f, given, me.Creature, out _);
                    if (f.CurrentHp <= now) dead.Add(f);
                }
            }
            if (!all)
            {
                c4 = ownMax; c5 = ownHits; c6 = own[tgt!] + extra[tgt!]; c7 = dead.Contains(tgt!) ? 1 : 0;
            }
            else
            {
                bool hasOwn = own.Values.Any(v => v > 0) || ownHits > 0;
                c4 = hasOwn ? ownMax : foes.Select(f => seq[f].Select(h => h.Dmg).DefaultIfEmpty(0).Max()).DefaultIfEmpty(0).Max();
                c5 = hasOwn ? ownHits : plays;
                c6 = foes.Sum(f => own[f] + extra[f]); c7 = dead.Count;
            }
            return (c4, c5, c6, c7, dead.Count > 0 ? dead : null, block + add, self, ostyEx);
        }
        catch { return (c4, c5, c6, c7, killed, block, 0, 0); }
    }
}
