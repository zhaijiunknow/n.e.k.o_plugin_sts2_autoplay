// Per-card effect table: card_id -> what actually happens when it is played (a script plus any
// special flags). SimBuild consults this first; a card with no entry falls back to the field-driven
// path (its DynamicVars-derived Damage/Block/Draw/Powers/etc.). This is the single source of truth for
// behaviour cards whose effect is not a flat field set (exhaust synergy, X-cost, summon, dynamic
// scaling).
//
// The registry is generated + hand-reviewed: sim/gen_onplay.py scaffolds entries from the game's card
// data/description; the ones needing judgment are transcribed by hand here (and in SimOnPlay.hand.cs).
using System;
using System.Collections.Generic;

namespace NekoComm.Game.Sim
{
    /// <summary>Effect spec for one card: an optional script (SimOp list) plus special flags.</summary>
    public sealed class SimCardEffect
    {
        public readonly List<SimOp> Script = new();
        public bool ExtraDamagePerExhaust;   // extra damage adds per card in the exhaust pile
        public bool Retains;
        public bool Ethereal;
        public bool Innate;
        public bool ExhaustsOnPlay;
        /// <summary>True if the script fully accounts for the card's behaviour (CombatSolver-style
        /// "recipe proven complete"). False = hand-approximated; some effect may be missing → inferred.</summary>
        public bool Complete = true;

        public static SimCardEffect ScriptOnly(params SimOp[] ops)
        {
            var e = new SimCardEffect();
            if (ops != null) foreach (var o in ops) e.Script.Add(o);
            return e;
        }

        public SimCardEffect WithOps(params SimOp[] ops)
        {
            if (ops != null) foreach (var o in ops) Script.Add(o);
            return this;
        }

        // Marks a transcription as an approximation (an effect is not represented) → coverage=inferred.
        public SimCardEffect Approximate()
        {
            Complete = false;
            return this;
        }
    }

    public static class SimCardEffects
    {
        // Card id -> effect. Add behaviour cards here (transcribed from the game's OnPlay). The list
        // grows as gen_onplay.py scans the card set and flags behaviour cards to review.
        private static readonly Dictionary<string, SimCardEffect> Table = new(StringComparer.OrdinalIgnoreCase)
        {
            // "Exhaust all non-Attack cards in your Hand. Gain 5 Block for each card Exhausted."
            ["SECOND_WIND"] = SimCardEffect.ScriptOnly(new SimOp { Kind = SimOpKind.ExhaustNonAttacks, Amount = 5 }),
            // "Deal 9 damage. Deals 3 additional damage for each card in your Exhaust Pile."
            ["ASHEN_STRIKE"] = new SimCardEffect { ExtraDamagePerExhaust = true },
            // "Gain 2 energy. Add a Void into your Discard Pile."
            ["TURBO"] = new SimCardEffect(),
            // "Deal 5 damage twice."
            ["TWIN_STRIKE"] = SimCardEffect.ScriptOnly(new SimOp { Kind = SimOpKind.Damage, Target = SimTargetSel.AnyEnemy, Amount = 5, Times = 2 }),
            // "Deal 3 damage to a random enemy 3 times."
            ["SWORD_BOOMERANG"] = SimCardEffect.ScriptOnly(new SimOp { Kind = SimOpKind.Damage, Target = SimTargetSel.RandomEnemy, Amount = 3, Times = 3 }),
            // "Deal 6 damage. Add a copy of this card to your discard pile."
            ["ANGER"] = SimCardEffect.ScriptOnly(
                new SimOp { Kind = SimOpKind.Damage, Target = SimTargetSel.AnyEnemy, Amount = 6 },
                new SimOp { Kind = SimOpKind.CopyToPile, Pile = SimPile.Discard }),
            // "Deal 9 damage. Add a copy of this card to ALL players' discard piles."
            ["OUTRAGE"] = SimCardEffect.ScriptOnly(
                new SimOp { Kind = SimOpKind.Damage, Target = SimTargetSel.AnyEnemy, Amount = 9 },
                new SimOp { Kind = SimOpKind.CopyToPile, Pile = SimPile.Discard }),
            // "Deal damage equal to your current block."
            ["BODY_SLAM"] = SimCardEffect.ScriptOnly(new SimOp { Kind = SimOpKind.DamageEqualBlock, Target = SimTargetSel.AnyEnemy }),
            // "Deal 10 damage. Double the target's Vulnerable. Exhaust."
            ["MOLTEN_FIST"] = new SimCardEffect
            {
                ExhaustsOnPlay = true,
            }.WithOps(
                new SimOp { Kind = SimOpKind.Damage, Target = SimTargetSel.AnyEnemy, Amount = 10 },
                new SimOp { Kind = SimOpKind.DoublePower, Target = SimTargetSel.AnyEnemy, PowerId = "vulnerable_power" }),
            // "Deal 6 damage. +2 per card named 'Strike' in your deck."
            ["PERFECTED_STRIKE"] = SimCardEffect.ScriptOnly(new SimOp { Kind = SimOpKind.PerXDamage, Target = SimTargetSel.AnyEnemy, Amount = 6, Per = 2, Condition = SimPerCondition.StrikeInDeck }),
            // "Deal 4 damage. +2 for each Vulnerable on the target."
            ["BULLY"] = SimCardEffect.ScriptOnly(new SimOp { Kind = SimOpKind.PerXDamage, Target = SimTargetSel.AnyEnemy, Amount = 4, Per = 2, Condition = SimPerCondition.TargetVulnerable }),
            // "Deal 7 damage. Gain 3 Strength this turn." (Strength persists in the model — approximate.)
            ["SETUP_STRIKE"] = SimCardEffect.ScriptOnly(
                new SimOp { Kind = SimOpKind.Damage, Target = SimTargetSel.AnyEnemy, Amount = 7 },
                new SimOp { Kind = SimOpKind.ApplyPower, Target = SimTargetSel.Self, PowerId = "strength_power", Amount = 3 }),

            // --- Silent ---
            // "Deal 4 to ALL enemies twice."
            ["DAGGER_SPRAY"] = SimCardEffect.ScriptOnly(new SimOp { Kind = SimOpKind.Damage, Target = SimTargetSel.AllEnemies, Amount = 4, Times = 2 }),
            // "Deal 3 to a random enemy 4 times."
            ["RICOCHET"] = SimCardEffect.ScriptOnly(new SimOp { Kind = SimOpKind.Damage, Target = SimTargetSel.RandomEnemy, Amount = 3, Times = 4 }),
            // "Deal 3. Add 2 Shivs to your hand."
            ["LEADING_STRIKE"] = SimCardEffect.ScriptOnly(
                new SimOp { Kind = SimOpKind.Damage, Target = SimTargetSel.AnyEnemy, Amount = 3 },
                new SimOp { Kind = SimOpKind.AddShivs, Amount = 2 }),
            // "Add 3 Shivs to your hand."
            ["BLADE_DANCE"] = SimCardEffect.ScriptOnly(new SimOp { Kind = SimOpKind.AddShivs, Amount = 3 }),
            // "Gain 6 Block. Add 1 Shiv to your hand."
            ["CLOAK_AND_DAGGER"] = SimCardEffect.ScriptOnly(
                new SimOp { Kind = SimOpKind.Block, Target = SimTargetSel.Self, Amount = 6 },
                new SimOp { Kind = SimOpKind.AddShivs, Amount = 1 }),
            // "Gain 8 Block. Discard 1 card."
            ["SURVIVOR"] = SimCardEffect.ScriptOnly(
                new SimOp { Kind = SimOpKind.Block, Target = SimTargetSel.Self, Amount = 8 },
                new SimOp { Kind = SimOpKind.Discard, Amount = 1 }),
            // "Deal 9. Draw 1. Discard 1."
            ["DAGGER_THROW"] = SimCardEffect.ScriptOnly(
                new SimOp { Kind = SimOpKind.Damage, Target = SimTargetSel.AnyEnemy, Amount = 9 },
                new SimOp { Kind = SimOpKind.Draw, Target = SimTargetSel.Self, Amount = 1 },
                new SimOp { Kind = SimOpKind.Discard, Amount = 1 }),
            // "Draw 1. Discard 1."
            ["PREPARED"] = SimCardEffect.ScriptOnly(
                new SimOp { Kind = SimOpKind.Draw, Target = SimTargetSel.Self, Amount = 1 },
                new SimOp { Kind = SimOpKind.Discard, Amount = 1 }),

            // --- "next turn" effects (cross-turn) ---
            // "Deal 12. Draw 2 next turn."  (Regent)
            ["GUIDING_STAR"] = SimCardEffect.ScriptOnly(
                new SimOp { Kind = SimOpKind.Damage, Target = SimTargetSel.AnyEnemy, Amount = 12 },
                new SimOp { Kind = SimOpKind.NextTurnDraw, Amount = 2 }),
            // "Gain 1 star. Draw 1. Draw 1 next turn." (Regent)
            ["GLOW"] = SimCardEffect.ScriptOnly(
                new SimOp { Kind = SimOpKind.GainStars, Amount = 1 },
                new SimOp { Kind = SimOpKind.Draw, Target = SimTargetSel.Self, Amount = 1 },
                new SimOp { Kind = SimOpKind.NextTurnDraw, Amount = 1 }),
            // "Gain 1 star. Gain 3 stars next turn." (Regent)
            ["HIDDEN_CACHE"] = SimCardEffect.ScriptOnly(
                new SimOp { Kind = SimOpKind.GainStars, Amount = 1 },
                new SimOp { Kind = SimOpKind.NextTurnStars, Amount = 3 }),
            // "Gain 11 Block. Gain 5 Block next turn." (Regent)
            ["GLITTERSTREAM"] = SimCardEffect.ScriptOnly(
                new SimOp { Kind = SimOpKind.Block, Target = SimTargetSel.Self, Amount = 11 },
                new SimOp { Kind = SimOpKind.NextTurnBlock, Amount = 5 }),
            // "Gain 4 Block. Gain 4 Block next turn." (Silent)
            ["DODGE_AND_ROLL"] = SimCardEffect.ScriptOnly(
                new SimOp { Kind = SimOpKind.Block, Target = SimTargetSel.Self, Amount = 4 },
                new SimOp { Kind = SimOpKind.NextTurnBlock, Amount = 4 }),
            // "Deal 15. Draw 2 next turn." (Silent)
            ["PREDATOR"] = SimCardEffect.ScriptOnly(
                new SimOp { Kind = SimOpKind.Damage, Target = SimTargetSel.AnyEnemy, Amount = 15 },
                new SimOp { Kind = SimOpKind.NextTurnDraw, Amount = 2 }),

            // --- Ancient / Colourless ---
            // "Gain 2 energy. Draw 2. Lose 2 max HP."
            ["BRIGHTEST_FLAME"] = SimCardEffect.ScriptOnly(
                new SimOp { Kind = SimOpKind.GainEnergy, Amount = 2 },
                new SimOp { Kind = SimOpKind.Draw, Target = SimTargetSel.Self, Amount = 2 },
                new SimOp { Kind = SimOpKind.LoseMaxHp, Amount = 2 }),
            // "Gain 16 Block. Next turn draw 2 + gain 2 energy."
            ["RELAX"] = SimCardEffect.ScriptOnly(
                new SimOp { Kind = SimOpKind.Block, Target = SimTargetSel.Self, Amount = 16 },
                new SimOp { Kind = SimOpKind.NextTurnDraw, Amount = 2 },
                new SimOp { Kind = SimOpKind.NextTurnEnergy, Amount = 2 }),
            // "Deal 33. Stun." (Stun not modelled → approximation.)
            ["WHISTLE"] = SimCardEffect.ScriptOnly(new SimOp { Kind = SimOpKind.Damage, Target = SimTargetSel.AnyEnemy, Amount = 33 }).Approximate(),
            // "Deal 5 twice. (All 'Maul' +2 — increase not modelled → approximation.)"
            ["MAUL"] = SimCardEffect.ScriptOnly(new SimOp { Kind = SimOpKind.Damage, Target = SimTargetSel.AnyEnemy, Amount = 5, Times = 2 }).Approximate(),
        };

        public static bool TryGet(string cardId, out SimCardEffect effect)
            => Table.TryGetValue(cardId, out effect!);

        public static IEnumerable<string> KnownIds => Table.Keys;
    }
}
