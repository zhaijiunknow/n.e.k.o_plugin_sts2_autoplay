using System.Collections;
using System.Reflection;
using System.Text.RegularExpressions;
using Godot;
using MegaCrit.Sts2.Core.CardSelection;
using MegaCrit.Sts2.Core.Combat;
using MegaCrit.Sts2.Core.Entities.Cards;
using MegaCrit.Sts2.Core.Entities.Creatures;
using MegaCrit.Sts2.Core.Entities.Merchant;
using MegaCrit.Sts2.Core.Entities.Multiplayer;
using MegaCrit.Sts2.Core.Entities.Players;
using MegaCrit.Sts2.Core.Entities.Potions;
using MegaCrit.Sts2.Core.Entities.RestSite;
using MegaCrit.Sts2.Core.Events;
using MegaCrit.Sts2.Core.Context;
using MegaCrit.Sts2.Core.Helpers;
using MegaCrit.Sts2.Core.Map;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.Models.Characters;
using MegaCrit.Sts2.Core.Models.Powers;
using MegaCrit.Sts2.Core.Models.Relics;
using MegaCrit.Sts2.Core.Logging;
using MegaCrit.Sts2.Core.Multiplayer.Game;
using MegaCrit.Sts2.Core.Multiplayer.Game.Lobby;
using MegaCrit.Sts2.Core.MonsterMoves.Intents;
using MegaCrit.Sts2.Core.Nodes;
using MegaCrit.Sts2.Core.Nodes.Cards.Holders;
using MegaCrit.Sts2.Core.Nodes.Combat;
using MegaCrit.Sts2.Core.Nodes.CommonUi;
using MegaCrit.Sts2.Core.Nodes.Debug.Multiplayer;
using MegaCrit.Sts2.Core.Nodes.Ftue;
using MegaCrit.Sts2.Core.Nodes.GodotExtensions;
using MegaCrit.Sts2.Core.Nodes.Rewards;
using MegaCrit.Sts2.Core.Nodes.Rooms;
using MegaCrit.Sts2.Core.Nodes.Screens;
using MegaCrit.Sts2.Core.Nodes.Screens.CardSelection;
using MegaCrit.Sts2.Core.Nodes.Screens.CharacterSelect;
using MegaCrit.Sts2.Core.Nodes.Screens.GameOverScreen;
using MegaCrit.Sts2.Core.Nodes.Screens.MainMenu;
using MegaCrit.Sts2.Core.Nodes.Screens.Map;
using MegaCrit.Sts2.Core.Nodes.Screens.ScreenContext;
using MegaCrit.Sts2.Core.Nodes.Screens.Shops;
using MegaCrit.Sts2.Core.Nodes.Screens.Timeline;
using MegaCrit.Sts2.Core.Nodes.Screens.Timeline.UnlockScreens;
using MegaCrit.Sts2.Core.Nodes.Screens.TreasureRoomRelic;
using MegaCrit.Sts2.Core.Rooms;
using MegaCrit.Sts2.Core.Rewards;
using MegaCrit.Sts2.Core.Runs;
using MegaCrit.Sts2.Core.Timeline;
using MegaCrit.Sts2.addons.mega_text;
namespace NekoComm.Game;

// AgentView implementation; shares the service API and state across partial files.
internal static partial class GameStateService
{
    private static object BuildAgentViewPayload(
        string screen,
        SessionPayload session,
        string runId,
        int? turn,
        string[] availableActions,
        CombatState? combatState,
        RunState? runState,
        CombatPayload? combat,
        RunPayload? run,
        MapPayload? map,
        SelectionPayload? selection,
        CharacterSelectPayload? characterSelect,
        TimelinePayload? timeline,
        ChestPayload? chest,
        EventPayload? eventPayload,
        ShopPayload? shop,
        RestPayload? rest,
        RewardPayload? reward,
        BundlePayload[]? bundles,
        ModalPayload? modal,
        GameOverPayload? gameOver)
    {
        var glossaryTerms = new HashSet<string>(StringComparer.Ordinal);

        return new
        {
            version = AgentViewVersion,
            screen,
            run_id = runId,
            session,
            turn,
            actions = availableActions,
            available_actions = availableActions,
            combat = BuildAgentCombatPayload(combatState, combat, glossaryTerms),
            run = BuildAgentRunPayload(combatState, runState, run, glossaryTerms),
            map = BuildAgentMapPayload(map),
            selection = BuildAgentSelectionPayload(selection, glossaryTerms),
            character_select = BuildAgentCharacterSelectPayload(characterSelect),
            timeline = BuildAgentTimelinePayload(timeline),
            chest = BuildAgentChestPayload(chest),
            @event = BuildAgentEventPayload(eventPayload),
            shop = BuildAgentShopPayload(shop, glossaryTerms),
            rest = BuildAgentRestPayload(rest),
            reward = BuildAgentRewardPayload(reward, glossaryTerms),
            bundles = BuildAgentBundlePayload(bundles, glossaryTerms),
            modal = BuildAgentModalPayload(modal),
            game_over = BuildAgentGameOverPayload(gameOver),
            glossary = BuildAgentGlossary(glossaryTerms)
        };
    }

    private static object? BuildAgentCombatPayload(
        CombatState? combatState,
        CombatPayload? combat,
        HashSet<string> glossaryTerms)
    {
        if (combat == null)
        {
            return null;
        }

        var liveHand = GetLocalPlayer(combatState)?.PlayerCombatState?.Hand.Cards.ToList()
            ?? new List<CardModel>();
        var playerCombatState = GetLocalPlayer(combatState)?.PlayerCombatState;

        return new
        {
            player = new
            {
                hp = $"{combat.player.current_hp}/{combat.player.max_hp}",
                block = combat.player.block,
                energy = combat.player.energy,
                stars = combat.player.stars,
                focus = combat.player.focus,
                orbs = combat.player.orbs.Select(orb => FormatOrbLine(orb)).ToArray(),
                cards_played_this_turn = combat.player.cards_played_this_turn,
                attacks_played_this_turn = combat.player.attacks_played_this_turn,
                skills_played_this_turn = combat.player.skills_played_this_turn
            },
            end_turn_will_kill_player = combat.end_turn_will_kill_player,
            lethal_risks = combat.lethal_risks.Select(risk => new
            {
                risk_id = risk.risk_id,
                source = risk.source,
                will_kill_player = risk.will_kill_player,
                reason = risk.reason,
                incoming_damage = risk.incoming_damage,
                damage_after_block = risk.damage_after_block,
                player_hp = risk.player_hp,
                player_block = risk.player_block,
                power_id = risk.power_id,
                power_amount = risk.power_amount
            }).ToArray(),
            hand = combat.hand.Select(card =>
                BuildAgentHandCardPayload(
                    card,
                    card.index >= 0 && card.index < liveHand.Count ? liveHand[card.index] : null,
                    glossaryTerms)).ToArray(),
            draw = BuildAgentCardStacks(ReadCombatPileCards(playerCombatState, "DrawPile", "DrawDeck"), glossaryTerms),
            discard = BuildAgentCardStacks(ReadCombatPileCards(playerCombatState, "DiscardPile"), glossaryTerms),
            exhaust = BuildAgentCardStacks(ReadCombatPileCards(playerCombatState, "ExhaustPile"), glossaryTerms),
            draw_cards = BuildStructuredPileCards(ReadCombatPileCards(playerCombatState, "DrawPile", "DrawDeck")),
            discard_cards = BuildStructuredPileCards(ReadCombatPileCards(playerCombatState, "DiscardPile")),
            exhaust_cards = BuildStructuredPileCards(ReadCombatPileCards(playerCombatState, "ExhaustPile")),
            enemies = combat.enemies.Select(enemy => new
            {
                i = enemy.index,
                enemy_id = enemy.enemy_id,
                name = enemy.name,
                hp = $"{enemy.current_hp}/{enemy.max_hp}",
                block = enemy.block,
                intent = enemy.intent,
                move_id = enemy.move_id,
                alive = enemy.is_alive,
                hittable = enemy.is_hittable
            }).ToArray()
        };
    }

    private static object? BuildAgentRunPayload(
        CombatState? combatState,
        RunState? runState,
        RunPayload? run,
        HashSet<string> glossaryTerms)
    {
        if (run == null)
        {
            return null;
        }

        var player = GetLocalPlayer(runState);
        var deckCards = player?.Deck.Cards.ToArray() ?? Array.Empty<CardModel>();
        var combatPlayer = GetLocalPlayer(combatState)?.PlayerCombatState;

        foreach (var effect in run.ascension_effects)
        {
            CollectGlossaryTerms(glossaryTerms, effect.name);
            CollectGlossaryTerms(glossaryTerms, effect.description);
        }

        return new
        {
            character = run.character_name,
            ascension = run.ascension,
            ascension_effects = run.ascension_effects,
            floor = run.floor,
            act_id = run.act_id,
            boss_id = run.boss_id,
            hp = $"{run.current_hp}/{run.max_hp}",
            gold = run.gold,
            max_energy = run.max_energy,
            base_orb_slots = run.base_orb_slots,
            deck = deckCards.Length > 0
                ? BuildAgentCardStacks(deckCards, glossaryTerms)
                : BuildAgentCardStacks(run.deck, glossaryTerms),
            relics = run.relics
                .Select(relic => relic.is_melted ? $"{relic.name} (熔毁)" : relic.name)
                .ToArray(),
            potions = run.potions.Select(potion => new
            {
                i = potion.index,
                potion_id = potion.potion_id,
                line = FormatPotionLine(potion),
                usable = potion.can_use,
                discard = potion.can_discard,
                target = NormalizeTargetHint(potion.target_type),
                targets = potion.valid_target_indices
            }).ToArray(),
            piles = new
            {
                draw = BuildAgentCardStacks(ReadCombatPileCards(combatPlayer, "DrawPile", "DrawDeck"), glossaryTerms),
                discard = BuildAgentCardStacks(ReadCombatPileCards(combatPlayer, "DiscardPile"), glossaryTerms),
                exhaust = BuildAgentCardStacks(ReadCombatPileCards(combatPlayer, "ExhaustPile"), glossaryTerms),
                draw_cards = BuildStructuredPileCards(ReadCombatPileCards(combatPlayer, "DrawPile", "DrawDeck")),
                discard_cards = BuildStructuredPileCards(ReadCombatPileCards(combatPlayer, "DiscardPile")),
                exhaust_cards = BuildStructuredPileCards(ReadCombatPileCards(combatPlayer, "ExhaustPile"))
            }
        };
    }

    private static object? BuildAgentSelectionPayload(SelectionPayload? selection, HashSet<string> glossaryTerms)
    {
        if (selection == null)
        {
            return null;
        }

        CollectGlossaryTerms(glossaryTerms, selection.prompt);

        return new
        {
            kind = selection.kind,
            prompt = selection.prompt,
            min = selection.min_select,
            max = selection.max_select,
            selected = selection.selected_count,
            confirm = selection.can_confirm,
            cards = selection.cards.Select(card => BuildAgentChoiceCardPayload(card.index, card.name, card.upgraded, card.energy_cost, card.star_cost, card.costs_x, card.star_costs_x, GetPreferredCardRulesText(card.rules_text, card.resolved_rules_text), glossaryTerms)).ToArray()
        };
    }

    private static object? BuildAgentRewardPayload(RewardPayload? reward, HashSet<string> glossaryTerms)
    {
        if (reward == null)
        {
            return null;
        }

        foreach (var option in reward.rewards)
        {
            CollectGlossaryTerms(glossaryTerms, option.description);
        }

        return new
        {
            pending_card_choice = reward.pending_card_choice,
            can_proceed = reward.can_proceed,
            rewards = reward.rewards.Select(option => new
            {
                i = option.index,
                line = $"{option.reward_type}: {option.description}",
                claimable = option.claimable
            }).ToArray(),
            cards = reward.card_options.Select(card => BuildAgentChoiceCardPayload(card.index, card.name, card.upgraded, null, null, false, false, GetPreferredCardRulesText(card.rules_text, card.resolved_rules_text), glossaryTerms)).ToArray(),
            alternatives = reward.alternatives.Select(option => new
            {
                i = option.index,
                line = option.label
            }).ToArray()
        };
    }

    private static object? BuildAgentBundlePayload(BundlePayload[]? bundles, HashSet<string> glossaryTerms)
    {
        if (bundles == null || bundles.Length == 0)
        {
            return null;
        }

        return bundles.Select(bundle => new
        {
            i = bundle.index,
            cards = bundle.cards.Select(card =>
                BuildAgentChoiceCardPayload(
                    card.index, card.name, card.upgraded,
                    card.energy_cost, null, false, false,
                    GetPreferredCardRulesText(card.rules_text, card.resolved_rules_text),
                    glossaryTerms)).ToArray()
        }).ToArray();
    }

    private static object? BuildAgentEventPayload(EventPayload? eventPayload)
    {
        if (eventPayload == null)
        {
            return null;
        }

        return new
        {
            id = eventPayload.event_id,
            title = eventPayload.title,
            finished = eventPayload.is_finished,
            options = eventPayload.options.Select(option => new
            {
                i = option.index,
                line = FormatEventOptionLine(option),
                locked = option.is_locked,
                proceed = option.is_proceed
            }).ToArray()
        };
    }

    private static object? BuildAgentShopPayload(ShopPayload? shop, HashSet<string> glossaryTerms)
    {
        if (shop == null)
        {
            return null;
        }

        return new
        {
            open = shop.is_open,
            can_open = shop.can_open,
            can_close = shop.can_close,
            cards = shop.cards.Select(card =>
                BuildAgentPricedCardPayload(
                    card.index,
                    card.name,
                    card.upgraded,
                    card.energy_cost,
                    card.star_cost,
                    card.costs_x,
                    card.star_costs_x,
                    GetPreferredCardRulesText(card.rules_text, card.resolved_rules_text),
                    card.price,
                    card.enough_gold,
                    glossaryTerms)).ToArray(),
            relics = shop.relics.Select(relic => new
            {
                i = relic.index,
                line = $"{relic.name} [{relic.rarity}] | {relic.price}g",
                affordable = relic.enough_gold,
                stocked = relic.is_stocked
            }).ToArray(),
            potions = shop.potions.Select(potion => new
            {
                i = potion.index,
                line = $"{potion.name ?? "空"}{(string.IsNullOrWhiteSpace(potion.usage) ? string.Empty : $"：{potion.usage}")} | {potion.price}g",
                affordable = potion.enough_gold,
                stocked = potion.is_stocked
            }).ToArray(),
            remove = shop.card_removal == null
                ? null
                : new
                {
                    price = shop.card_removal.price,
                    affordable = shop.card_removal.enough_gold,
                    available = shop.card_removal.available,
                    used = shop.card_removal.used
                }
        };
    }

    private static object? BuildAgentRestPayload(RestPayload? rest)
    {
        if (rest == null)
        {
            return null;
        }

        return new
        {
            options = rest.options.Select(option => new
            {
                i = option.index,
                line = (string.IsNullOrWhiteSpace(option.description)
                    ? option.title
                    : $"{option.title}: {option.description}") +
                    (option.requires_target
                        ? $" [target {option.target_index_space}: {string.Join(",", option.valid_target_indices)}]"
                        : string.Empty),
                requires_target = option.requires_target,
                target_index_space = option.target_index_space,
                valid_target_indices = option.valid_target_indices,
                enabled = option.is_enabled
            }).ToArray()
        };
    }

    private static object? BuildAgentMapPayload(MapPayload? map)
    {
        if (map == null)
        {
            return null;
        }

        return new
        {
            current = map.current_node == null ? null : $"{map.current_node.row},{map.current_node.col}",
            local_vote = map.local_vote == null ? null : $"{map.local_vote.row},{map.local_vote.col}",
            votes = map.player_votes
                .Where(vote => vote.coord != null)
                .Select(vote => new
                {
                    player_id = vote.player_id,
                    local = vote.is_local,
                    coord = $"{vote.coord!.row},{vote.coord.col}"
                }).ToArray(),
            options = map.available_nodes.Select(node => new
            {
                i = node.index,
                line = $"{node.node_type} ({node.row},{node.col})" +
                    (node.has_local_vote
                        ? " [local vote]"
                        : node.vote_count > 0
                            ? $" [votes:{node.vote_count}]"
                            : string.Empty)
            }).ToArray()
        };
    }

    private static object? BuildAgentCharacterSelectPayload(CharacterSelectPayload? characterSelect)
    {
        if (characterSelect == null)
        {
            return null;
        }

        return new
        {
            selected = characterSelect.selected_character_id,
            embark = characterSelect.can_embark,
            ascension = characterSelect.ascension,
            characters = characterSelect.characters.Select(character => new
            {
                i = character.index,
                line = character.is_random ? $"{character.name} (随机)" : character.name,
                locked = character.is_locked,
                selected = character.is_selected
            }).ToArray()
        };
    }

    private static object? BuildAgentTimelinePayload(TimelinePayload? timeline)
    {
        if (timeline == null)
        {
            return null;
        }

        return new
        {
            back = timeline.back_enabled,
            confirm = timeline.can_confirm_overlay,
            slots = timeline.slots.Select(slot => new
            {
                i = slot.index,
                line = $"{slot.title} [{slot.state}]",
                actionable = slot.is_actionable
            }).ToArray()
        };
    }

    private static object? BuildAgentChestPayload(ChestPayload? chest)
    {
        if (chest == null)
        {
            return null;
        }

        return new
        {
            opened = chest.is_opened,
            claimed = chest.has_relic_been_claimed,
            relics = chest.relic_options.Select(relic => new
            {
                i = relic.index,
                line = $"{relic.name} [{relic.rarity}]"
            }).ToArray()
        };
    }

    private static object? BuildAgentModalPayload(ModalPayload? modal)
    {
        if (modal == null)
        {
            return null;
        }

        return new
        {
            type = modal.type_name,
            confirm = modal.can_confirm,
            dismiss = modal.can_dismiss,
            confirm_label = modal.confirm_label,
            dismiss_label = modal.dismiss_label
        };
    }

    private static object? BuildAgentGameOverPayload(GameOverPayload? gameOver)
    {
        if (gameOver == null)
        {
            return null;
        }

        return new
        {
            victory = gameOver.is_victory,
            floor = gameOver.floor,
            character = gameOver.character_id,
            can_continue = gameOver.can_continue,
            can_return = gameOver.can_return_to_main_menu
        };
    }

    private static object BuildAgentHandCardPayload(
        CombatHandCardPayload card,
        CardModel? liveCard,
        HashSet<string> glossaryTerms)
    {
        var displayRulesText = GetPreferredCardRulesText(card.rules_text, card.resolved_rules_text);
        var mods = GetCardModifierTags(liveCard);
        var keywords = GetGlossaryMatches(displayRulesText, mods);
        CollectGlossaryTerms(glossaryTerms, displayRulesText, mods);

        return new
        {
            i = card.index,
            line = FormatCardLine(card.name, card.upgraded, 1, card.energy_cost, card.star_cost, card.costs_x, card.star_costs_x, displayRulesText),
            playable = card.playable,
            target = card.requires_target ? NormalizeTargetHint(card.target_index_space ?? card.target_type) : null,
            targets = card.requires_target ? card.valid_target_indices : Array.Empty<int>(),
            why = card.playable ? null : card.unplayable_reason,
            keywords,
            mods
        };
    }

    private static object BuildAgentChoiceCardPayload(
        int index,
        string name,
        bool upgraded,
        int? energyCost,
        int? starCost,
        bool costsX,
        bool starCostsX,
        string rulesText,
        HashSet<string> glossaryTerms)
    {
        var keywords = GetGlossaryMatches(rulesText);
        CollectGlossaryTerms(glossaryTerms, rulesText);

        return new
        {
            i = index,
            line = FormatCardLine(name, upgraded, 1, energyCost, starCost, costsX, starCostsX, rulesText),
            keywords,
            mods = Array.Empty<string>()
        };
    }

    private static object BuildAgentPricedCardPayload(
        int index,
        string name,
        bool upgraded,
        int energyCost,
        int starCost,
        bool costsX,
        bool starCostsX,
        string rulesText,
        int price,
        bool enoughGold,
        HashSet<string> glossaryTerms)
    {
        var keywords = GetGlossaryMatches(rulesText);
        CollectGlossaryTerms(glossaryTerms, rulesText);

        return new
        {
            i = index,
            line = $"{FormatCardLine(name, upgraded, 1, energyCost, starCost, costsX, starCostsX, rulesText)} | {price}g",
            affordable = enoughGold,
            keywords,
            mods = Array.Empty<string>()
        };
    }

    private static object[] BuildAgentCardStacks(IEnumerable<CardModel> cards, HashSet<string> glossaryTerms)
    {
        var descriptors = cards
            .Select(card => BuildAgentCardDescriptor(card, glossaryTerms))
            .ToArray();

        return BuildAgentCardStacks(descriptors);
    }

    private static object[] BuildAgentCardStacks(IEnumerable<DeckCardPayload> cards, HashSet<string> glossaryTerms)
    {
        var descriptors = cards
            .Select(card => BuildAgentCardDescriptor(card, glossaryTerms))
            .ToArray();

        return BuildAgentCardStacks(descriptors);
    }

    private static object[] BuildAgentCardStacks(IEnumerable<AgentCardDescriptor> descriptors)
    {
        return descriptors
            .GroupBy(descriptor => descriptor.GroupKey, StringComparer.Ordinal)
            .Select(group =>
            {
                var first = group.First();
                var line = FormatCardLine(first.name, first.upgraded, group.Count(), first.energy_cost, first.star_cost, first.costs_x, first.star_costs_x, first.rules_text);

                return new
                {
                    line,
                    keywords = first.keywords,
                    mods = first.mods
                };
            })
            .OrderBy(item => item.line, StringComparer.Ordinal)
            .Cast<object>()
            .ToArray();
    }

    private static AgentCardDescriptor BuildAgentCardDescriptor(CardModel card, HashSet<string> glossaryTerms)
    {
        var rulesText = GetResolvedCardRulesText(card);
        var mods = GetCardModifierTags(card);
        var keywords = GetGlossaryMatches(rulesText, mods);
        CollectGlossaryTerms(glossaryTerms, rulesText, mods);

        return new AgentCardDescriptor(
            card.Title,
            card.IsUpgraded,
            card.EnergyCost.GetWithModifiers(CostModifiers.All),
            Math.Max(0, card.GetStarCostWithModifiers()),
            card.EnergyCost.CostsX,
            card.HasStarCostX,
            rulesText,
            keywords,
            mods);
    }

    private static AgentCardDescriptor BuildAgentCardDescriptor(DeckCardPayload card, HashSet<string> glossaryTerms)
    {
        var rulesText = GetPreferredCardRulesText(card.rules_text, card.resolved_rules_text);
        var keywords = GetGlossaryMatches(rulesText);
        CollectGlossaryTerms(glossaryTerms, rulesText);

        return new AgentCardDescriptor(
            card.name,
            card.upgraded,
            card.energy_cost,
            card.star_cost,
            card.costs_x,
            card.star_costs_x,
            rulesText,
            keywords,
            Array.Empty<string>());
    }

    private static string FormatCardLine(
        string name,
        bool upgraded,
        int count,
        int? energyCost,
        int? starCost,
        bool costsX,
        bool starCostsX,
        string rulesText)
    {
        var title = upgraded && !name.EndsWith("+", StringComparison.Ordinal) ? $"{name}+" : name;
        if (count > 1)
        {
            title = $"{title}*{count}";
        }

        var cost = FormatCardCost(energyCost, starCost, costsX, starCostsX);
        var prefix = string.IsNullOrWhiteSpace(cost) ? title : $"{title} [{cost}]";
        return string.IsNullOrWhiteSpace(rulesText)
            ? prefix
            : $"{prefix}：{rulesText}";
    }

    private static string FormatCardCost(int? energyCost, int? starCost, bool costsX, bool starCostsX)
    {
        var parts = new List<string>();
        if (costsX)
        {
            parts.Add("X费");
        }
        else if (energyCost.HasValue)
        {
            parts.Add($"{Math.Max(0, energyCost.Value)}费");
        }

        if (starCostsX)
        {
            parts.Add("X星");
        }
        else if (starCost.HasValue && starCost.Value > 0)
        {
            parts.Add($"{starCost.Value}星");
        }

        return string.Join("/", parts);
    }

    private static string FormatOrbLine(CombatOrbPayload orb)
    {
        return $"{orb.name} 被动{orb.passive_value}/激发{orb.evoke_value}";
    }

    private static string FormatPotionLine(RunPotionPayload potion)
    {
        if (!potion.occupied)
        {
            return $"{potion.index}: 空";
        }

        var usage = string.IsNullOrWhiteSpace(potion.usage) ? string.Empty : $"：{potion.usage}";
        return $"{potion.index}: {potion.name}{usage}";
    }

    private static string FormatEventOptionLine(EventOptionPayload option)
    {
        var segments = new List<string>();
        if (!string.IsNullOrWhiteSpace(option.title))
        {
            segments.Add(option.title);
        }

        if (!string.IsNullOrWhiteSpace(option.description))
        {
            segments.Add(option.description);
        }

        if (segments.Count == 0 && !string.IsNullOrWhiteSpace(option.text_key))
        {
            segments.Add(option.text_key);
        }

        return string.Join(" | ", segments);
    }

    private static object[] BuildStructuredPileCards(CardModel[] cards)
    {
        return cards.Select(card => new
        {
            card_id = card.Id.Entry,
            upgraded = card.IsUpgraded,
            card_type = card.Type.ToString()
        }).ToArray();
    }

    internal static CardModel[] ReadCombatPileCards(object? playerCombatState, params string[] memberNames)
    {
        if (playerCombatState == null)
        {
            return Array.Empty<CardModel>();
        }

        foreach (var memberName in memberNames)
        {
            var memberValue = TryGetMemberValue(playerCombatState, memberName);
            var cards = ExtractCards(memberValue);
            if (cards.Length > 0 || memberValue != null)
            {
                return cards;
            }
        }

        return Array.Empty<CardModel>();
    }

    internal static CardModel[] ExtractCards(object? value)
    {
        return ExtractCards(value, new HashSet<object>(ReferenceEqualityComparer.Instance));
    }

    private static CardModel[] ExtractCards(object? value, HashSet<object> visited)
    {
        if (value == null)
        {
            return Array.Empty<CardModel>();
        }

        if (!visited.Add(value))
        {
            return Array.Empty<CardModel>();
        }

        if (value is IEnumerable enumerable and not string)
        {
            var cards = new List<CardModel>();
            foreach (var item in enumerable)
            {
                if (item is CardModel card)
                {
                    cards.Add(card);
                }
            }

            if (cards.Count > 0)
            {
                return cards.ToArray();
            }
        }

        foreach (var memberName in new[] { "Cards", "CardModels", "Entries", "List" })
        {
            var nested = TryGetMemberValue(value, memberName);
            if (nested == null)
            {
                continue;
            }

            var cards = ExtractCards(nested, visited);
            if (cards.Length > 0)
            {
                return cards;
            }
        }

        return Array.Empty<CardModel>();
    }

    internal static object? TryGetMemberValue(object instance, string memberName)
    {
        const BindingFlags flags = BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic;

        try
        {
            var property = instance.GetType().GetProperty(memberName, flags);
            if (property != null)
            {
                return property.GetValue(instance);
            }

            var field = instance.GetType().GetField(memberName, flags);
            if (field != null)
            {
                return field.GetValue(instance);
            }
        }
        catch
        {
        }

        return null;
    }

    private static string[] GetCardModifierTags(CardModel? card)
    {
        if (card == null)
        {
            return Array.Empty<string>();
        }

        var values = new HashSet<string>(StringComparer.Ordinal);
        foreach (var memberName in new[]
        {
            "Enchantments",
            "Enchants",
            "Modifiers",
            "ModifierIds",
            "Affixes",
            "Augments",
            "Keywords"
        })
        {
            var memberValue = TryGetMemberValue(card, memberName);
            foreach (var token in ExtractModifierTokens(memberValue))
            {
                if (string.IsNullOrWhiteSpace(token))
                {
                    continue;
                }

                values.Add(NormalizeCardRulesText(token));
            }
        }

        return values.OrderBy(value => value, StringComparer.Ordinal).ToArray();
    }

    private static IEnumerable<string> ExtractModifierTokens(object? value)
    {
        if (value == null)
        {
            yield break;
        }

        if (value is string text)
        {
            if (!string.IsNullOrWhiteSpace(text))
            {
                yield return text;
            }

            yield break;
        }

        if (value is IEnumerable enumerable)
        {
            foreach (var item in enumerable)
            {
                foreach (var token in ExtractModifierTokens(item))
                {
                    yield return token;
                }
            }

            yield break;
        }

        foreach (var memberName in new[] { "Title", "Name", "Keyword", "Text", "Description", "Label" })
        {
            var memberValue = TryGetMemberValue(value, memberName);
            if (TryCoerceText(memberValue) is { Length: > 0 } memberText)
            {
                yield return memberText;
            }
        }

        var idValue = TryGetMemberValue(value, "Id");
        if (idValue != null)
        {
            var entryValue = TryGetMemberValue(idValue, "Entry");
            if (entryValue is string entryText && !string.IsNullOrWhiteSpace(entryText))
            {
                yield return entryText;
            }
        }
    }

    private static string[] GetGlossaryMatches(string text, params string[][] modifierGroups)
    {
        var values = new HashSet<string>(StringComparer.Ordinal);

        foreach (var (keyword, _) in AgentKeywordDefinitions)
        {
            if (!string.IsNullOrWhiteSpace(text) && text.Contains(keyword, StringComparison.Ordinal))
            {
                values.Add(keyword);
            }

            foreach (var modifierGroup in modifierGroups)
            {
                if (modifierGroup.Any(modifier => modifier.Contains(keyword, StringComparison.Ordinal)))
                {
                    values.Add(keyword);
                }
            }
        }

        return values.OrderBy(value => value, StringComparer.Ordinal).ToArray();
    }

    private static void CollectGlossaryTerms(HashSet<string> glossaryTerms, string? text, params string[][] modifierGroups)
    {
        if (glossaryTerms.Count >= AgentKeywordDefinitions.Length)
        {
            return;
        }

        foreach (var keyword in GetGlossaryMatches(text ?? string.Empty, modifierGroups))
        {
            glossaryTerms.Add(keyword);
        }
    }

    private static Dictionary<string, string> BuildAgentGlossary(HashSet<string> glossaryTerms)
    {
        var glossary = new Dictionary<string, string>(StringComparer.Ordinal);
        foreach (var (keyword, definition) in AgentKeywordDefinitions)
        {
            if (glossaryTerms.Contains(keyword))
            {
                glossary[keyword] = definition;
            }
        }

        return glossary;
    }

    private static string? NormalizeTargetHint(string? value)
    {
        if (string.IsNullOrWhiteSpace(value))
        {
            return null;
        }

        var normalized = value.Trim().ToLowerInvariant();
        if (normalized.Contains("enemy", StringComparison.Ordinal))
        {
            return "enemy";
        }

        if (normalized.Contains("player", StringComparison.Ordinal) || normalized.Contains("self", StringComparison.Ordinal))
        {
            return "player";
        }

        return normalized;
    }

    private static readonly (string keyword, string definition)[] AgentKeywordDefinitions =
    {
        ("力量", "每层力量通常使攻击额外造成 1 点伤害。"),
        ("敏捷", "每层敏捷通常使获得的格挡额外增加 1。"),
        ("易伤", "易伤单位会承受更多攻击伤害。"),
        ("虚弱", "虚弱单位造成的攻击伤害会降低。"),
        ("脆弱", "脆弱单位获得的格挡会减少。"),
        ("格挡", "格挡会优先抵消即将受到的伤害。"),
        ("消耗", "消耗牌打出后会移出本场战斗。"),
        ("保留", "保留牌在回合结束时不会被弃掉。"),
        ("中毒", "中毒会在回合结束时造成等量生命损失，然后层数减少。"),
        ("眩晕", "眩晕通常是无法主动打出的状态牌。"),
        ("灼伤", "灼伤通常会在手中或结算时带来额外伤害。"),
        ("虚空", "虚空通常会在抽到时消耗能量或妨碍出牌。"),
        ("力量流失", "力量流失会临时降低力量。"),
        ("集中", "集中通常会强化充能球的被动与激发效果。"),
        ("球位", "球位决定你能同时容纳多少个充能球。"),
        ("附魔", "附魔是卡牌附着的额外词条或效果层。"),
        ("灌注", "灌注表示卡牌带有额外附着效果。"),
        ("临时", "临时牌通常会在回合结束或打出后离开牌组流转。")
    };

    // Steam transport exposes the raw lobby id (decimal SteamID string, what +connect_lobby needs); ENet and
    // single-player return null. Null-guarded so a transient unconnected service can't throw the state build.
}
