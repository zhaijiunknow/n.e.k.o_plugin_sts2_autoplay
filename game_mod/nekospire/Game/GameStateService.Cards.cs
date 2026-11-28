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

// Cards implementation; shares the service API and state across partial files.
internal static partial class GameStateService
{
    private static string SafeReadString(Func<string?> getter, string fallback = "")
    {
        try
        {
            var value = getter();
            return value == null ? fallback : value;
        }
        catch
        {
            return fallback;
        }
    }

    private static bool SafeReadBool(Func<bool> getter, bool fallback = false)
    {
        try
        {
            return getter();
        }
        catch
        {
            return fallback;
        }
    }

    private static int SafeReadInt(Func<int> getter, int fallback = 0)
    {
        try
        {
            return getter();
        }
        catch
        {
            return fallback;
        }
    }

    /// <summary>
    /// 读一个 int 属性（反射返回的是装箱 object，直接给 SafeReadInt 会类型不匹配）。
    /// `gridAxis` 用于 `Vector2I`（GridSize）：0 取 X，1 取 Y，其它值原样转 int。
    /// </summary>
    private static int GetReflectedInt(object target, string propertyName, int gridAxis = -1)
    {
        var value = GetReflectedProperty(target, propertyName);
        if (value == null)
        {
            return 0;
        }

        try
        {
            if (value is Vector2I v)
            {
                return gridAxis switch
                {
                    0 => v.X,
                    1 => v.Y,
                    _ => 0
                };
            }

            return Convert.ToInt32(value);
        }
        catch
        {
            return 0;
        }
    }


    internal static object? GetReflectedField(object target, string fieldName)
    {
        try
        {
            const BindingFlags flags = BindingFlags.Instance | BindingFlags.NonPublic | BindingFlags.Public;
            for (var t = target.GetType(); t != null; t = t.BaseType)
            {
                var f = t.GetField(fieldName, flags);
                if (f != null)
                {
                    return f.GetValue(target);
                }
            }

            return null;
        }
        catch
        {
            return null;
        }
    }

    private static string GetCardRulesText(CardModel? card)
    {
        if (card == null)
        {
            return string.Empty;
        }

        try
        {
            var rawDescription = card.Description?.GetRawText();
            if (!string.IsNullOrWhiteSpace(rawDescription))
            {
                return NormalizeCardRulesText(rawDescription);
            }
        }
        catch
        {
        }

        foreach (var memberName in new[]
        {
            "Description",
            "RulesText",
            "Body",
            "Text",
            "RawText",
            "DescriptionText"
        })
        {
            var text = TryReadCardTextMember(card, memberName);
            if (!string.IsNullOrWhiteSpace(text))
            {
                return NormalizeCardRulesText(text);
            }
        }

        return string.Empty;
    }

    private static string GetResolvedCardRulesText(CardModel? card)
    {
        if (card == null)
        {
            return string.Empty;
        }

        try
        {
            card.UpdateDynamicVarPreview(CardPreviewMode.Normal, card.CurrentTarget, card.DynamicVars);
            var pileType = card.Pile?.Type ?? PileType.None;
            var resolved = card.GetDescriptionForPile(pileType, card.CurrentTarget);
            if (!string.IsNullOrWhiteSpace(resolved))
            {
                return NormalizeCardRulesText(resolved);
            }
        }
        catch
        {
        }

        return GetCardRulesText(card);
    }

    private static CardDynamicValuePayload[] BuildCardDynamicValuePayloads(CardModel? card)
    {
        if (card == null)
        {
            return Array.Empty<CardDynamicValuePayload>();
        }

        try
        {
            var previewSet = card.DynamicVars.Clone(card);
            card.UpdateDynamicVarPreview(CardPreviewMode.Normal, card.CurrentTarget, previewSet);

            return previewSet.Values
                .Select(dynamicVar => new CardDynamicValuePayload
                {
                    name = dynamicVar.Name,
                    base_value = (int)dynamicVar.BaseValue,
                    current_value = (int)dynamicVar.PreviewValue,
                    enchanted_value = (int)dynamicVar.EnchantedValue,
                    is_modified = (int)dynamicVar.PreviewValue != (int)dynamicVar.BaseValue
                        || (int)dynamicVar.EnchantedValue != (int)dynamicVar.BaseValue,
                    was_just_upgraded = dynamicVar.WasJustUpgraded
                })
                .OrderBy(payload => payload.name, StringComparer.Ordinal)
                .ToArray();
        }
        catch
        {
            return Array.Empty<CardDynamicValuePayload>();
        }
    }

    private static string GetPreferredCardRulesText(string rulesText, string? resolvedRulesText)
    {
        return string.IsNullOrWhiteSpace(resolvedRulesText) ? rulesText : resolvedRulesText;
    }

    private static AscensionEffectPayload[] BuildAscensionEffectPayloads(int ascensionLevel, GameMode gameMode)
    {
        if (ascensionLevel <= 0)
        {
            return Array.Empty<AscensionEffectPayload>();
        }

        return Enumerable.Range(1, ascensionLevel)
            .Select(level => new AscensionEffectPayload
            {
                id = $"LEVEL_{level:D2}",
                name = AscensionHelper.GetTitle(level, gameMode).GetFormattedText(),
                description = AscensionHelper.GetDescription(level, gameMode).GetFormattedText()
            })
            .ToArray();
    }

    private static string TryReadCardTextMember(object instance, string memberName)
    {
        const BindingFlags flags = BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic;

        try
        {
            var property = instance.GetType().GetProperty(memberName, flags);
            if (property != null)
            {
                return TryCoerceText(property.GetValue(instance));
            }

            var field = instance.GetType().GetField(memberName, flags);
            if (field != null)
            {
                return TryCoerceText(field.GetValue(instance));
            }
        }
        catch
        {
        }

        return string.Empty;
    }

    private static string TryCoerceText(object? value)
    {
        if (value == null)
        {
            return string.Empty;
        }

        if (value is string text)
        {
            return text;
        }

        const BindingFlags flags = BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic;
        var valueType = value.GetType();

        try
        {
            var getRawText = valueType.GetMethod("GetRawText", flags, null, Type.EmptyTypes, null);
            if (getRawText != null && getRawText.ReturnType == typeof(string))
            {
                return getRawText.Invoke(value, null) as string ?? string.Empty;
            }
        }
        catch
        {
        }

        try
        {
            var textProperty = valueType.GetProperty("Text", flags);
            if (textProperty != null && textProperty.PropertyType == typeof(string))
            {
                return textProperty.GetValue(value) as string ?? string.Empty;
            }
        }
        catch
        {
        }

        return value.ToString() ?? string.Empty;
    }

    private static string NormalizeCardRulesText(string value)
    {
        if (string.IsNullOrWhiteSpace(value))
        {
            return string.Empty;
        }

        var normalized = Regex.Replace(value, @"\[(?:/?[^\]]+)\]", string.Empty);
        normalized = Regex.Replace(normalized, @"\s+", " ");
        return normalized.Trim();
    }

    private static RewardOptionPayload BuildRewardOptionPayload(NRewardButton button, int index)
    {
        var reward = button.Reward;

        return new RewardOptionPayload
        {
            index = index,
            reward_type = GetRewardTypeName(reward),
            description = reward?.Description.GetFormattedText() ?? string.Empty,
            claimable = button.IsEnabled
        };
    }

    private static RewardCardOptionPayload BuildRewardCardOptionPayload(NCardHolder holder, int index)
    {
        var card = holder.CardModel;
        return BuildBundleCardPayload(card, index);
    }

    private static RewardCardOptionPayload BuildBundleCardPayload(CardModel? card, int index)
    {
        var resolvedRulesText = GetResolvedCardRulesText(card);
        var dynamicValues = BuildCardDynamicValuePayloads(card);

        return new RewardCardOptionPayload
        {
            index = index,
            card_id = card?.Id.Entry ?? string.Empty,
            name = card?.Title ?? string.Empty,
            upgraded = card?.IsUpgraded ?? false,
            card_type = card?.Type.ToString() ?? string.Empty,
            rarity = card?.Rarity.ToString() ?? string.Empty,
            energy_cost = card?.EnergyCost.GetWithModifiers(CostModifiers.All) ?? 0,
            rules_text = GetCardRulesText(card),
            resolved_rules_text = resolvedRulesText,
            dynamic_values = dynamicValues
        };
    }

    private static RewardAlternativePayload BuildRewardAlternativePayload(NCardRewardAlternativeButton button, int index)
    {
        return new RewardAlternativePayload
        {
            index = index,
            label = button.GetNodeOrNull<MegaLabel>("Label")?.Text ?? button.Name
        };
    }

    private static RunRelicPayload BuildRunRelicPayload(RelicModel relic, int index)
    {
        return new RunRelicPayload
        {
            index = index,
            relic_id = relic.Id.Entry,
            name = relic.Title.GetFormattedText(),
            description = GetDynamicFormattedTextProperty(relic, "DynamicDescription", "Description"),
            stack = GetReflectedNullableIntProperty(relic, "Amount"),
            is_melted = relic.IsMelted
        };
    }

    private static RunPotionPayload BuildRunPotionPayload(
        IScreenContext? currentScreen,
        CombatState? combatState,
        Player player,
        PotionModel? potion,
        int index)
    {
        var requiresTarget = potion != null && PotionRequiresTarget(combatState, potion);
        var targetIndexSpace = potion != null ? GetPotionTargetIndexSpace(combatState, potion) : null;
        var validTargetIndices = potion != null ? GetPotionTargetIndices(combatState, potion) : Array.Empty<int>();

        return new RunPotionPayload
        {
            index = index,
            potion_id = potion?.Id.Entry,
            name = potion?.Title.GetFormattedText(),
            description = potion != null ? GetDynamicFormattedTextProperty(potion, "DynamicDescription", "Description") : null,
            rarity = potion != null ? GetReflectedStringProperty(potion, "Rarity") : null,
            occupied = potion != null,
            usage = potion?.Usage.ToString(),
            target_type = potion?.TargetType.ToString(),
            is_queued = potion?.IsQueued ?? false,
            requires_target = requiresTarget,
            target_index_space = targetIndexSpace,
            valid_target_indices = validTargetIndices,
            can_use = IsPotionUsable(currentScreen, combatState, player, potion),
            can_discard = CanDiscardPotionsInCurrentScreen(currentScreen) && IsPotionDiscardable(player, potion)
        };
    }

    internal static object? GetReflectedProperty(object target, string propertyName)
    {
        try
        {
            return target.GetType().GetProperty(propertyName)?.GetValue(target);
        }
        catch
        {
            return null;
        }
    }

    private static string? GetReflectedStringProperty(object target, string propertyName)
    {
        var value = GetReflectedProperty(target, propertyName);
        return value?.ToString();
    }

    private static string? GetReflectedFormattedTextProperty(object target, string propertyName)
    {
        var value = GetReflectedProperty(target, propertyName);
        if (value == null)
        {
            return null;
        }

        try
        {
            var getRawText = value.GetType().GetMethod("GetRawText", Type.EmptyTypes);
            if (getRawText != null)
            {
                return getRawText.Invoke(value, null)?.ToString();
            }

            return value.GetType().GetMethod("GetFormattedText")?.Invoke(value, null)?.ToString();
        }
        catch
        {
            return value.ToString();
        }
    }

    private static string? GetDynamicFormattedTextProperty(object target, params string[] propertyNames)
    {
        foreach (var propertyName in propertyNames)
        {
            var value = GetReflectedFormattedTextProperty(target, propertyName);
            if (!string.IsNullOrWhiteSpace(value))
            {
                return value;
            }
        }

        return null;
    }

    private static int? GetReflectedNullableIntProperty(object target, string propertyName)
    {
        try
        {
            var value = GetReflectedProperty(target, propertyName);
            return value == null ? null : Convert.ToInt32(value);
        }
        catch
        {
            return null;
        }
    }

    private static bool GetReflectedBoolProperty(object target, string propertyName)
    {
        try
        {
            var value = GetReflectedProperty(target, propertyName);
            return value != null && Convert.ToBoolean(value);
        }
        catch
        {
            return false;
        }
    }

    private static bool GetEventOptionWillKillPlayer(object eventModel, object option)
    {
        try
        {
            var owner = GetReflectedProperty(eventModel, "Owner");
            var willKillPlayer = GetReflectedProperty(option, "WillKillPlayer") as Delegate;
            if (owner == null || willKillPlayer == null)
            {
                return false;
            }

            return willKillPlayer.DynamicInvoke(owner) as bool? ?? false;
        }
        catch
        {
            return false;
        }
    }

    private static ShopCardPayload BuildShopCardPayload(MerchantCardEntry entry, int index, string category)
    {
        var card = entry.CreationResult?.Card;
        var resolvedRulesText = GetResolvedCardRulesText(card);
        var dynamicValues = BuildCardDynamicValuePayloads(card);
        return new ShopCardPayload
        {
            index = index,
            category = category,
            card_id = card?.Id.Entry ?? string.Empty,
            name = card?.Title ?? string.Empty,
            upgraded = card?.IsUpgraded ?? false,
            card_type = card?.Type.ToString() ?? string.Empty,
            rarity = card?.Rarity.ToString() ?? string.Empty,
            costs_x = card?.EnergyCost.CostsX ?? false,
            star_costs_x = card?.HasStarCostX ?? false,
            energy_cost = card?.EnergyCost.GetWithModifiers(CostModifiers.All) ?? 0,
            star_cost = card != null ? Math.Max(0, card.GetStarCostWithModifiers()) : 0,
            rules_text = GetCardRulesText(card),
            resolved_rules_text = resolvedRulesText,
            dynamic_values = dynamicValues,
            price = entry.IsStocked ? entry.Cost : 0,
            on_sale = entry.IsOnSale,
            is_stocked = entry.IsStocked,
            enough_gold = entry.IsStocked && entry.EnoughGold
        };
    }

    private static ShopRelicPayload BuildShopRelicPayload(MerchantRelicEntry entry, int index)
    {
        var relic = entry.Model;
        return new ShopRelicPayload
        {
            index = index,
            relic_id = relic?.Id.Entry ?? string.Empty,
            name = relic?.Title.GetFormattedText() ?? string.Empty,
            rarity = relic?.Rarity.ToString() ?? string.Empty,
            price = entry.IsStocked ? entry.Cost : 0,
            is_stocked = entry.IsStocked,
            enough_gold = entry.IsStocked && entry.EnoughGold
        };
    }

    private static ShopPotionPayload BuildShopPotionPayload(MerchantPotionEntry entry, int index, Player? player)
    {
        var potion = entry.Model;
        return new ShopPotionPayload
        {
            index = index,
            potion_id = potion?.Id.Entry,
            name = potion?.Title.GetFormattedText(),
            rarity = potion?.Rarity.ToString(),
            usage = potion?.Usage.ToString(),
            price = entry.IsStocked ? entry.Cost : 0,
            is_stocked = entry.IsStocked,
            enough_gold = CanPurchaseShopPotion(player, entry)
        };
    }

    private static bool CanPurchaseShopPotion(Player? player, MerchantPotionEntry entry)
    {
        return entry.IsStocked &&
            entry.EnoughGold &&
            player?.PotionSlots.Any(slot => slot == null) == true;
    }

    private static ShopCardRemovalPayload? BuildShopCardRemovalPayload(MerchantCardRemovalEntry? entry)
    {
        if (entry == null)
        {
            return null;
        }

        return new ShopCardRemovalPayload
        {
            price = entry.IsStocked ? entry.Cost : 0,
            available = entry.IsStocked,
            used = entry.Used,
            enough_gold = entry.IsStocked && entry.EnoughGold
        };
    }

    private static DeckCardPayload BuildDeckCardPayload(CardModel card, int index)
    {
        var resolvedRulesText = GetResolvedCardRulesText(card);
        var dynamicValues = BuildCardDynamicValuePayloads(card);
        return new DeckCardPayload
        {
            index = index,
            card_id = card.Id.Entry,
            name = card.Title,
            upgraded = card.IsUpgraded,
            card_type = card.Type.ToString(),
            rarity = card.Rarity.ToString(),
            costs_x = card.EnergyCost.CostsX,
            star_costs_x = card.HasStarCostX,
            energy_cost = card.EnergyCost.GetWithModifiers(CostModifiers.All),
            star_cost = Math.Max(0, card.GetStarCostWithModifiers()),
            rules_text = GetCardRulesText(card),
            resolved_rules_text = resolvedRulesText,
            dynamic_values = dynamicValues
        };
    }

    private static SelectionCardPayload BuildSelectionCardPayload(CardModel card, int index)
    {
        var resolvedRulesText = GetResolvedCardRulesText(card);
        var dynamicValues = BuildCardDynamicValuePayloads(card);
        return new SelectionCardPayload
        {
            index = index,
            card_id = card.Id.Entry,
            name = card.Title,
            upgraded = card.IsUpgraded,
            card_type = card.Type.ToString(),
            rarity = card.Rarity.ToString(),
            costs_x = card.EnergyCost.CostsX,
            star_costs_x = card.HasStarCostX,
            energy_cost = card.EnergyCost.GetWithModifiers(CostModifiers.All),
            star_cost = Math.Max(0, card.GetStarCostWithModifiers()),
            rules_text = GetCardRulesText(card),
            resolved_rules_text = resolvedRulesText,
            dynamic_values = dynamicValues
        };
    }

    internal static bool IsProceedButtonUsable(NProceedButton? button)
    {
        return button != null &&
            GodotObject.IsInstanceValid(button) &&
            button.IsEnabled &&
            button.IsVisibleInTree();
    }

    private static string GetRewardTypeName(Reward? reward)
    {
        return reward switch
        {
            CardReward => "Card",
            GoldReward => "Gold",
            PotionReward => "Potion",
            RelicReward => "Relic",
            CardRemovalReward => "RemoveCard",
            SpecialCardReward => "SpecialCard",
            LinkedRewardSet => "LinkedRewardSet",
            null => "Unknown",
            _ => reward.GetType().Name
        };
    }

}
