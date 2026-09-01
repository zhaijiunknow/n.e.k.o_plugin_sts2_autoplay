// Bridge from the live game state to a pure SimState. Mirrors the proven reading logic in
// GameSolverService.BuildSolverState/BuildSolverCard, but emits the pure model. This is the
// authoritative "blueprint" the deterministic resolver (and the search) run on. Runs on the main
// game thread and is fast + side-effect free.
//
// NOTE: Phase 0 covers the core field surface (damage/block/energy/draw/hp_loss/powers),
// players/enemies hp+block+powers, hand and draw pile. Orbs/stars/X-value/relics are bridged in
// later phases. Powers are captured into SimState.Powers so Phase-2 hooks can act on them; the
// Phase-0 drop does not yet apply them (SimHooks is identity).
using System;
using System.Collections.Generic;
using System.Linq;
using MegaCrit.Sts2.Core.CardSelection;
using MegaCrit.Sts2.Core.Combat;
using MegaCrit.Sts2.Core.Entities.Cards;
using MegaCrit.Sts2.Core.Entities.Creatures;
using MegaCrit.Sts2.Core.Entities.Players;
using MegaCrit.Sts2.Core.Localization.DynamicVars;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Models.Powers;
using MegaCrit.Sts2.Core.Models.Relics;
using MegaCrit.Sts2.Core.MonsterMoves;
using MegaCrit.Sts2.Core.MonsterMoves.Intents;
using MegaCrit.Sts2.Core.MonsterMoves.MonsterMoveStateMachine;

namespace NekoComm.Game.Sim
{
    public static class SimBuild
    {
        private static readonly string[] PowerDynamicNames =
        {
            "VulnerablePower", "WeakPower", "StrengthPower", "DexterityPower", "FocusPower",
            "PoisonPower", "IntangiblePower", "BufferPower", "RitualPower", "RegenPower",
            "PlatingPower", "PlatedArmorPower", "ThornsPower", "DoomPower", "DemonFormPower",
            "FeelNoPainPower", "WraithFormPower", "BiasedCognitionPower",
        };

        /// <summary>Snapshot the live combat into a pure SimState.</summary>
        public static SimState FromLive(CombatState combat, Player me)
        {
            var playerCombat = me.PlayerCombatState!;
            var creature = me.Creature;
            var state = new SimState
            {
                Round = combat.RoundNumber,
                Turn = playerCombat.TurnNumber,
                Side = SimSide.Player,
                Ascension = 0,
                ActiveEnergy = playerCombat.Energy,
                MaxEnergy = me.MaxEnergy,
                ActiveStars = 0,
            };

            var p = new SimCombatant(creature.CurrentHp, creature.MaxHp) { Block = creature.Block };
            ReadPlayerPowers(creature, p);
            ReadPlayerOrbs(playerCombat, creature, p);
            state.Players.Add(p);
            ReadRelics(me, state);

            var enemies = combat.Enemies.ToList();
            for (var i = 0; i < enemies.Count; i++)
            {
                var e = enemies[i];
                var se = new SimCombatant(e.CurrentHp, e.MaxHp) { Block = e.Block };
                ReadEnemyPowers(e, se);
                se.Moves.AddRange(ReadEnemyMoves(combat, e));
                state.Enemies.Add(se);
            }

            var hand = playerCombat.Hand.Cards.ToList();
            for (var i = 0; i < hand.Count; i++)
                state.Hand.Add(BuildSimCard(combat, hand[i]));

            var draw = GameStateService.ReadCombatPileCards(playerCombat, "DrawPile", "DrawDeck");
            for (var i = 0; i < draw.Length; i++)
                state.DrawPile.Add(BuildSimCard(combat, draw[i]));

            return state;
        }

        // ---- card surface ----------------------------------------------------

        private static SimCard BuildSimCard(CombatState combat, CardModel card)
        {
            var dv = ReadDynamicValues(card);
            var s = new SimCard
            {
                Id = card.Id.Entry,
                Name = ReadCardDisplayName(card),
                Cost = card.EnergyCost.GetWithModifiers(CostModifiers.All),
                CostsX = card.EnergyCost.CostsX,
                CostsStarX = card.HasStarCostX,
                CardType = ReadCardType(card),
                Target = MapTarget(card.TargetType),
                ExhaustsOnPlay = HasKeyword(card, "EXHAUST"),
                Retains = HasKeyword(card, "RETAIN"),
                Ethereal = HasKeyword(card, "ETHEREAL"),
                Innate = HasKeyword(card, "INNATE"),
            };
            s.Damage = dv.TryGetValue("Damage", out var d) ? d : 0;
            s.Block = dv.TryGetValue("Block", out var b) ? b : 0;
            s.EnergyGain = dv.TryGetValue("Energy", out var e) ? e : 0;
            s.CardsDraw = ReadDrawValue(dv);
            s.HpLoss = dv.TryGetValue("HpLoss", out var h) ? h : 0;
            s.MaxHpGain = dv.TryGetValue("MaxHp", out var mh) ? mh : 0;
            s.Heal = dv.TryGetValue("Heal", out var hl) ? hl : 0;
            s.ExtraDamage = dv.TryGetValue("ExtraDamage", out var ed) ? ed : 0;
            s.StarCost = dv.TryGetValue("Stars", out var st) ? st : 0;
            s.StarsGain = dv.TryGetValue("Stars", out var sg) ? sg : 0;
            s.Repeat = dv.TryGetValue("Repeat", out var rep) ? rep : 1;
            s.Powers.AddRange(ReadPowerVars(card));   // auto-infer power apps from the var TYPE, not a whitelist
            // A Power-type card applies its OWN power as a stack: a generic "Power" DynamicVar gives the
            // amount, and the power id is snake(cardId)+"_power". E.g. FEEL_NO_PAIN -> feel_no_pain_power.
            if (s.CardType == "Power" && dv.TryGetValue("Power", out var powAmt) && powAmt != 0)
                s.Powers.Add((ToRegistryKey(card.Id.Entry) + "_power", powAmt));

            // Per-card effect table (Phase 3): behaviour cards get their script/flags here. A card with
            // no entry keeps the field-driven path. The table is generated + hand-reviewed.
            if (SimCardEffects.TryGet(card.Id.Entry, out var eff))
            {
                foreach (var op in eff.Script) s.Script.Add(op);
                s.ExtraDamagePerExhaust = eff.ExtraDamagePerExhaust;
                s.Retains = eff.Retains;
                s.Ethereal = eff.Ethereal;
                s.Innate = eff.Innate;
                s.ExhaustsOnPlay = eff.ExhaustsOnPlay;
                s.ApproximateEffect = !eff.Complete;   // approximation -> coverage=inferred
            }
            else if (SimOnPlayG.BehaviorSignals.ContainsKey(card.Id.Entry))
            {
                // A behaviour card with no transcribed effect yet: flag it so coverage marks it inferred
                // rather than silently running the (wrong) field path.
                s.BehaviorUnmodeled = true;
            }

            return s;
        }

        // Reads each var's BaseValue (the card's printed/upgraded base), NOT PreviewValue. PreviewValue
        // already folds in Hook.ModifyDamage (strength/weak/vulnerable …); for a faithful model the
        // resolver must re-apply those from *current* powers, so we read the un-modified base.
        // Auto-infer the powers a card applies from its DynamicVars: any var that is a PowerVar<T> is a
        // power application (amount = the power stacks). This replaces the old hardcoded whitelist, so
        // ANY power the game puts on the card is captured, not just the ~18 well-known ones.
        private static List<(string powerId, int amount)> ReadPowerVars(CardModel card)
        {
            var result = new List<(string, int)>();
            try
            {
                var set = card.DynamicVars.Clone(card);
                foreach (var v in set.Values)
                {
                    if (IsPowerVar(v) && !string.IsNullOrEmpty(v.Name))
                    {
                        var amt = (int)v.BaseValue;
                        if (amt != 0) result.Add((ToRegistryKey(v.Name), amt));
                    }
                }
            }
            catch { }
            return result;
        }

        private static bool IsPowerVar(DynamicVar v)
        {
            var t = v.GetType();
            while (t != null && t != typeof(object))
            {
                if (t.IsGenericType && t.GetGenericTypeDefinition() == typeof(PowerVar<>))
                    return true;
                t = t.BaseType;
            }
            return false;
        }

        // Maps a PowerVar's name (e.g. "StrengthPower") to the registry key ("strength_power").
        private static string ToRegistryKey(string name)
            => System.Text.RegularExpressions.Regex.Replace(name, "([a-z0-9])([A-Z])", "$1_$2").ToLowerInvariant();

        private static string ReadCardDisplayName(CardModel card)
        {
            try
            {
                var title = card.GetType().GetProperty("Title")?.GetValue(card);
                if (title != null)
                {
                    var formatted = title.GetType().GetMethod("GetFormattedText")?.Invoke(title, null) as string;
                    if (!string.IsNullOrWhiteSpace(formatted)) return formatted;
                    var text = title.GetType().GetProperty("Text")?.GetValue(title) as string;
                    if (!string.IsNullOrWhiteSpace(text)) return text;
                }
            }
            catch { }
            return card.Id.Entry;
        }

        private static Dictionary<string, int> ReadDynamicValues(CardModel card)
        {
            var set = card.DynamicVars.Clone(card);
            var map = new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);
            foreach (var v in set.Values)
            {
                var name = v.Name;
                if (!string.IsNullOrEmpty(name)) map[name] = (int)v.BaseValue;
            }
            return map;
        }

        private static int ReadDrawValue(Dictionary<string, int> dv)
        {
            foreach (var key in new[] { "Cards", "Draw", "DrawCards" })
                if (dv.TryGetValue(key, out var value)) return value;
            return 0;
        }

        private static string ReadCardType(CardModel card)
        {
            try
            {
                var value = card.GetType().GetProperty("Type")?.GetValue(card);
                return value?.ToString() ?? "Unknown";
            }
            catch { return "Unknown"; }
        }

        private static bool HasKeyword(CardModel card, string keyword)
        {
            try
            {
                var keywords = card.GetType().GetProperty("Keywords")?.GetValue(card) as System.Collections.IEnumerable;
                if (keywords == null) return false;
                foreach (var k in keywords)
                    if (string.Equals(k?.ToString(), keyword, StringComparison.OrdinalIgnoreCase))
                        return true;
            }
            catch { }
            return false;
        }

        private static string NormalizePowerName(string name) => name.ToLowerInvariant();

        private static SimTargetKind MapTarget(TargetType t) => t switch
        {
            TargetType.None => SimTargetKind.None,
            TargetType.Self => SimTargetKind.Self,
            TargetType.AnyEnemy => SimTargetKind.AnyEnemy,
            TargetType.AllEnemies => SimTargetKind.AllEnemies,
            TargetType.RandomEnemy => SimTargetKind.RandomEnemy,
            TargetType.AnyPlayer => SimTargetKind.Self,
            TargetType.AnyAlly => SimTargetKind.AnyAlly,
            TargetType.AllAllies => SimTargetKind.AllAllies,
            _ => SimTargetKind.None,
        };

        // ---- power reading ---------------------------------------------------

        private static void ReadPlayerPowers(Creature c, SimCombatant s)
        {
            s.Powers["strength_power"] = Pow(c, c.GetPowerAmount<StrengthPower>());
            s.Powers["dexterity_power"] = Pow(c, c.GetPowerAmount<DexterityPower>());
            s.Powers["weak_power"] = Pow(c, c.GetPowerAmount<WeakPower>());
            s.Powers["vulnerable_power"] = Pow(c, c.GetPowerAmount<VulnerablePower>());
            s.Powers["frail_power"] = Pow(c, c.GetPowerAmount<FrailPower>());
            s.Powers["poison_power"] = Pow(c, c.GetPowerAmount<PoisonPower>());
            s.Powers["intangible_power"] = Pow(c, c.GetPowerAmount<IntangiblePower>());
            s.Powers["buffer_power"] = Pow(c, c.GetPowerAmount<BufferPower>());
            s.Powers["ritual_power"] = Pow(c, c.GetPowerAmount<RitualPower>());
            s.Powers["regen_power"] = Pow(c, c.GetPowerAmount<RegenPower>());
            s.Powers["plating_power"] = Pow(c, c.GetPowerAmount<PlatingPower>());
            s.Powers["thorns_power"] = Pow(c, c.GetPowerAmount<ThornsPower>());
            s.Powers["doom_power"] = Pow(c, c.GetPowerAmount<DoomPower>());
            s.Powers["demon_form_power"] = Pow(c, c.GetPowerAmount<DemonFormPower>());
        }

        private static void ReadEnemyPowers(Creature c, SimCombatant s)
        {
            s.Powers["strength_power"] = Pow(c, c.GetPowerAmount<StrengthPower>());
            s.Powers["weak_power"] = Pow(c, c.GetPowerAmount<WeakPower>());
            s.Powers["vulnerable_power"] = Pow(c, c.GetPowerAmount<VulnerablePower>());
            s.Powers["poison_power"] = Pow(c, c.GetPowerAmount<PoisonPower>());
        }

        private static int Pow(Creature c, int v) => Math.Max(0, v);

        // Relics: read ids + aggregate combat mods from each relic's declared vars (SimRelic.Aggregate).
        private static void ReadRelics(Player me, SimState state)
        {
            try
            {
                foreach (var relic in me.Relics)
                {
                    var id = relic.Id.Entry;
                    if (string.IsNullOrEmpty(id)) continue;
                    state.Relics.Add(id);
                    var vars = ReadRelicVars(relic);
                    var (e, d, b, s) = SimRelic.Aggregate(vars);
                    // Only the "flat continuous" mods are aggregated; per-relic on-play triggers (the
                    // majority, e.g. AfterCardPlayed) are not modelled and left to the coverage report.
                    state.TurnEnergyBonus += e;
                    state.TurnDrawBonus += d;
                    state.CombatStartBlock += b;
                    state.CombatStartStrength += s;
                }
            }
            catch { }
        }

        private static Dictionary<string, int> ReadRelicVars(RelicModel relic)
        {
            var map = new Dictionary<string, int>(StringComparer.OrdinalIgnoreCase);
            try
            {
                var set = relic.DynamicVars.Clone(relic);
                foreach (var v in set.Values)
                    if (!string.IsNullOrEmpty(v.Name)) map[v.Name] = (int)v.BaseValue;
            }
            catch { }
            return map;
        }

        // Orbs are a general mechanic — read the active player's orb queue + focus + capacity.
        private static void ReadPlayerOrbs(PlayerCombatState playerCombat, Creature creature, SimCombatant p)
        {
            try
            {
                p.Focus = Math.Max(0, creature.GetPowerAmount<FocusPower>());
                var orbQueue = playerCombat.OrbQueue;
                if (orbQueue == null) return;
                p.OrbCapacity = orbQueue.Capacity;
                foreach (var orb in orbQueue.Orbs)
                    p.Orbs.Add(BuildSimOrb(orb));
            }
            catch { }
        }

        private static SimOrb BuildSimOrb(MegaCrit.Sts2.Core.Models.OrbModel orb)
        {
            var id = orb.Id.Entry;
            var sim = SimOrbData.Create(id);
            sim.Passive = (int)orb.PassiveVal;
            sim.Evoke = (int)orb.EvokeVal;
            sim.Value = id.Equals("DARK", StringComparison.OrdinalIgnoreCase) ? 0 : (int)orb.PassiveVal;
            return sim;
        }

        // ---- enemy move table (SimMonster) ----------------------------------

        // Port of the legacy BuildMoveCycle/RollNext walk: follow the game's MoveState FollowUpState
        // chain and convert each step to a SimMonsterMove. Only AttackIntent damage is reliably
        // readable (Defend/Buff/Debuff amounts live in the perform closure, not the intent marker),
        // which matches what the legacy solver was able to read. Non-attack effects are omitted; the
        // move still contributes its attack. Any read failure => empty (enemy idles; honest warning).
        private static List<SimMonsterMove> ReadEnemyMoves(CombatState combat, Creature enemy, int maxMoves = 8)
        {
            var result = new List<SimMonsterMove>();
            try
            {
                var move = enemy.Monster?.NextMove;
                if (move == null) return result;
                var targets = combat.Players?.Select(p => p.Creature).ToArray() ?? Array.Empty<Creature>();
                var seen = new HashSet<string>(StringComparer.Ordinal);
                while (move != null && result.Count < maxMoves && seen.Add(move.Id))
                {
                    var sm = new SimMonsterMove { MoveId = move.Id };
                    if (move.Intents != null)
                    {
                        foreach (var intent in move.Intents)
                        {
                            if (intent is AttackIntent atk)
                            {
                                try
                                {
                                    // Use the BASE damage (DamageCalc), NOT GetSingleDamage — the latter
                                    // already applies strength/weak/vulnerable via Hook.ModifyDamage, which
                                    // would DOUBLE-apply them when the power chain also applies them.
                                    var dmg = (int)(atk.DamageCalc?.Invoke() ?? 0);
                                    sm.Intents.Add(new SimIntent
                                    {
                                        Kind = SimIntentKind.Attack,
                                        Damage = Math.Max(1, dmg),
                                        Times = Math.Max(1, atk.Repeats),
                                    });
                                }
                                catch { }
                            }
                        }
                    }
                    result.Add(sm);
                    move = RollNext(enemy, move);
                }
            }
            catch { }
            return result;
        }

        private static MoveState? RollNext(Creature enemy, MoveState current)
        {
            try
            {
                var machine = enemy.Monster?.MoveStateMachine;
                if (machine == null) return null;
                var id = current.FollowUpState?.Id ?? current.FollowUpStateId;
                if (string.IsNullOrEmpty(id) || !machine.States.TryGetValue(id, out var state)) return null;
                var guard = 0;
                while (guard++ < 32)
                    return state is MoveState ms ? ms : null; // RandomBranch/ConditionalBranch => stop
            }
            catch { }
            return null;
        }
    }
}
