using Godot;
using System.Reflection;
using MegaCrit.Sts2.Core.Combat;
using MegaCrit.Sts2.Core.Entities.Cards;
using NekoComm.Game.Sim;
using MegaCrit.Sts2.Core.Entities.Creatures;
using MegaCrit.Sts2.Core.Entities.Merchant;
using MegaCrit.Sts2.Core.Entities.Multiplayer;
using MegaCrit.Sts2.Core.Multiplayer;
using MegaCrit.Sts2.Core.Entities.Players;
using MegaCrit.Sts2.Core.Entities.Potions;
using MegaCrit.Sts2.Core.Entities.RestSite;
using MegaCrit.Sts2.Core.Context;
using MegaCrit.Sts2.Core.DevConsole;
using MegaCrit.Sts2.Core.GameActions;
using MegaCrit.Sts2.Core.Helpers;
using MegaCrit.Sts2.Core.Nodes;
using MegaCrit.Sts2.Core.Nodes.Cards.Holders;
using MegaCrit.Sts2.Core.Nodes.Combat;
using MegaCrit.Sts2.Core.Nodes.CommonUi;
using MegaCrit.Sts2.Core.Nodes.Debug;
using MegaCrit.Sts2.Core.Nodes.Debug.Multiplayer;
using MegaCrit.Sts2.Core.Nodes.GodotExtensions;
using MegaCrit.Sts2.Core.Nodes.Rewards;
using MegaCrit.Sts2.Core.Nodes.Screens;
using MegaCrit.Sts2.Core.Nodes.Screens.CardSelection;
using MegaCrit.Sts2.Core.Nodes.Screens.CharacterSelect;
using MegaCrit.Sts2.Core.Nodes.Screens.GameOverScreen;
using MegaCrit.Sts2.Core.Nodes.Screens.MainMenu;
using MegaCrit.Sts2.Core.Nodes.Screens.Map;
using MegaCrit.Sts2.Core.Nodes.Screens.Overlays;
using MegaCrit.Sts2.Core.Nodes.Screens.PauseMenu;
using MegaCrit.Sts2.Core.Nodes.Screens.ScreenContext;
using MegaCrit.Sts2.Core.Nodes.Screens.Shops;
using MegaCrit.Sts2.Core.Nodes.Screens.Timeline;
using MegaCrit.Sts2.Core.Nodes.Screens.Timeline.UnlockScreens;
using MegaCrit.Sts2.Core.Nodes.Screens.TreasureRoomRelic;
using MegaCrit.Sts2.Core.Nodes.Events.Custom.CrystalSphere;
using MegaCrit.Sts2.Core.Nodes.TopBar;
using MegaCrit.Sts2.Core.Nodes.Rooms;
using MegaCrit.Sts2.Core.Runs;
using MegaCrit.Sts2.Core.Models;
using MegaCrit.Sts2.Core.GameActions.Multiplayer;
using MegaCrit.Sts2.Core.Map;
using MegaCrit.Sts2.Core.Logging;
using MegaCrit.Sts2.Core.Multiplayer.Connection;
using MegaCrit.Sts2.Core.Multiplayer.Game;
using MegaCrit.Sts2.Core.Multiplayer.Game.Lobby;
using MegaCrit.Sts2.Core.Rooms;
using MegaCrit.Sts2.Core.Rewards;
using MegaCrit.Sts2.Core.Timeline;
using NekoComm.Server;
namespace NekoComm.Game;

// Shop implementation; shares the service API and state across partial files.
internal static partial class GameActionService
{
    private static async Task<ActionResponsePayload> ExecuteOpenShopInventoryAsync()
    {
        var currentScreen = ActiveScreenContext.Instance.GetCurrentScreen();
        var screen = GameStateService.ResolveScreen(currentScreen);

        if (!GameStateService.CanOpenShopInventory(currentScreen) || currentScreen is not NMerchantRoom merchantRoom)
        {
            throw new ApiException(409, "invalid_action", "Action is not available in the current state.", new
            {
                action = "open_shop_inventory",
                screen
            });
        }

        merchantRoom.OpenInventory();
        var stable = await WaitForShopInventoryOpenAsync(TimeSpan.FromSeconds(10));

        return new ActionResponsePayload
        {
            action = "open_shop_inventory",
            status = stable ? "completed" : "pending",
            stable = stable,
            message = stable ? "Action completed." : "Action queued but state is still transitioning.",
            state = GameStateService.BuildStatePayload()
        };
    }

    private static async Task<ActionResponsePayload> ExecuteCloseShopInventoryAsync()
    {
        var currentScreen = ActiveScreenContext.Instance.GetCurrentScreen();
        var screen = GameStateService.ResolveScreen(currentScreen);

        if (!GameStateService.CanCloseShopInventory(currentScreen) || currentScreen is not NMerchantInventory inventoryScreen)
        {
            throw new ApiException(409, "invalid_action", "Action is not available in the current state.", new
            {
                action = "close_shop_inventory",
                screen
            });
        }

        var backButton = inventoryScreen.GetNodeOrNull<NButton>("%BackButton")
            ?? throw new ApiException(503, "state_unavailable", "Shop back button not found.", new
            {
                action = "close_shop_inventory",
                screen
            }, retryable: true);

        backButton.ForceClick();
        var stable = await WaitForShopInventoryCloseAsync(TimeSpan.FromSeconds(10));

        return new ActionResponsePayload
        {
            action = "close_shop_inventory",
            status = stable ? "completed" : "pending",
            stable = stable,
            message = stable ? "Action completed." : "Action queued but state is still transitioning.",
            state = GameStateService.BuildStatePayload()
        };
    }

    // Game 0.113 replaced MerchantEntry.OnTryPurchaseWrapper with the same path the shop UI takes
    // (NMerchantSlot.OnSelected): MerchantSynchronizer.PurchaseLocalEntry runs the purchase locally and
    // propagates it to co-op peers so every client applies it deterministically. The unsynchronized
    // call is only for an entry that is not registered in a synchronized inventory (no Id).
    private static Task<bool> PurchaseMerchantEntryAsync(MerchantEntry entry, MerchantInventory inventory)
    {
        var synchronizer = RunManager.Instance.MerchantSynchronizer;
        return synchronizer != null && entry.Id.HasValue
            ? synchronizer.PurchaseLocalEntry(entry)
            : entry.OnTryPurchaseUnsynchronized(inventory);
    }

    private static async Task<ActionResponsePayload> ExecuteBuyCardAsync(ActionRequest request)
    {
        var currentScreen = ActiveScreenContext.Instance.GetCurrentScreen();
        var screen = GameStateService.ResolveScreen(currentScreen);

        if (!GameStateService.CanBuyShopCard(currentScreen))
        {
            throw new ApiException(409, "invalid_action", "Action is not available in the current state.", new
            {
                action = "buy_card",
                screen
            });
        }

        if (request.option_index == null)
        {
            throw new ApiException(400, "invalid_request", "buy_card requires option_index.", new
            {
                action = "buy_card"
            });
        }

        var inventory = GameStateService.GetMerchantInventory(currentScreen)
            ?? throw new ApiException(503, "state_unavailable", "Shop inventory is unavailable.", new
            {
                action = "buy_card",
                screen
            }, retryable: true);

        var cards = GameStateService.GetMerchantCardEntries(currentScreen).ToList();
        if (request.option_index < 0 || request.option_index >= cards.Count)
        {
            throw new ApiException(409, "invalid_target", "option_index is out of range.", new
            {
                action = "buy_card",
                option_index = request.option_index,
                option_count = cards.Count
            });
        }

        var entry = cards[request.option_index.Value];
        if (!entry.IsStocked)
        {
            throw new ApiException(409, "invalid_target", "The selected card is out of stock.", new
            {
                action = "buy_card",
                option_index = request.option_index
            });
        }

        var previousGold = inventory.Player.Gold;
        var previousCardId = entry.CreationResult?.Card.Id.Entry;
        var success = await PurchaseMerchantEntryAsync(entry, inventory);
        if (!success)
        {
            throw new ApiException(409, "invalid_action", "Card purchase failed in the current state.", new
            {
                action = "buy_card",
                option_index = request.option_index
            });
        }

        var stable = await WaitForMerchantCardPurchaseAsync(inventory.Player, entry, previousGold, previousCardId, TimeSpan.FromSeconds(10));
        return new ActionResponsePayload
        {
            action = "buy_card",
            status = stable ? "completed" : "pending",
            stable = stable,
            message = stable ? "Action completed." : "Action queued but state is still transitioning.",
            state = GameStateService.BuildStatePayload()
        };
    }

    private static async Task<ActionResponsePayload> ExecuteBuyRelicAsync(ActionRequest request)
    {
        var currentScreen = ActiveScreenContext.Instance.GetCurrentScreen();
        var screen = GameStateService.ResolveScreen(currentScreen);

        if (!GameStateService.CanBuyShopRelic(currentScreen))
        {
            throw new ApiException(409, "invalid_action", "Action is not available in the current state.", new
            {
                action = "buy_relic",
                screen
            });
        }

        if (request.option_index == null)
        {
            throw new ApiException(400, "invalid_request", "buy_relic requires option_index.", new
            {
                action = "buy_relic"
            });
        }

        var inventory = GameStateService.GetMerchantInventory(currentScreen)
            ?? throw new ApiException(503, "state_unavailable", "Shop inventory is unavailable.", new
            {
                action = "buy_relic",
                screen
            }, retryable: true);

        var relics = GameStateService.GetMerchantRelicEntries(currentScreen).ToList();
        if (request.option_index < 0 || request.option_index >= relics.Count)
        {
            throw new ApiException(409, "invalid_target", "option_index is out of range.", new
            {
                action = "buy_relic",
                option_index = request.option_index,
                option_count = relics.Count
            });
        }

        var entry = relics[request.option_index.Value];
        if (!entry.IsStocked)
        {
            throw new ApiException(409, "invalid_target", "The selected relic is out of stock.", new
            {
                action = "buy_relic",
                option_index = request.option_index
            });
        }

        var previousGold = inventory.Player.Gold;
        var previousRelicId = entry.Model?.Id.Entry;
        var success = await PurchaseMerchantEntryAsync(entry, inventory);
        if (!success)
        {
            throw new ApiException(409, "invalid_action", "Relic purchase failed in the current state.", new
            {
                action = "buy_relic",
                option_index = request.option_index
            });
        }

        var stable = await WaitForMerchantRelicPurchaseAsync(inventory.Player, entry, previousGold, previousRelicId, TimeSpan.FromSeconds(10));
        return new ActionResponsePayload
        {
            action = "buy_relic",
            status = stable ? "completed" : "pending",
            stable = stable,
            message = stable ? "Action completed." : "Action queued but state is still transitioning.",
            state = GameStateService.BuildStatePayload()
        };
    }

    private static async Task<ActionResponsePayload> ExecuteBuyPotionAsync(ActionRequest request)
    {
        var currentScreen = ActiveScreenContext.Instance.GetCurrentScreen();
        var screen = GameStateService.ResolveScreen(currentScreen);

        if (!GameStateService.CanBuyShopPotion(currentScreen))
        {
            throw new ApiException(409, "invalid_action", "Action is not available in the current state.", new
            {
                action = "buy_potion",
                screen
            });
        }

        if (request.option_index == null)
        {
            throw new ApiException(400, "invalid_request", "buy_potion requires option_index.", new
            {
                action = "buy_potion"
            });
        }

        var inventory = GameStateService.GetMerchantInventory(currentScreen)
            ?? throw new ApiException(503, "state_unavailable", "Shop inventory is unavailable.", new
            {
                action = "buy_potion",
                screen
            }, retryable: true);

        var potions = GameStateService.GetMerchantPotionEntries(currentScreen).ToList();
        if (request.option_index < 0 || request.option_index >= potions.Count)
        {
            throw new ApiException(409, "invalid_target", "option_index is out of range.", new
            {
                action = "buy_potion",
                option_index = request.option_index,
                option_count = potions.Count
            });
        }

        var entry = potions[request.option_index.Value];
        if (!entry.IsStocked)
        {
            throw new ApiException(409, "invalid_target", "The selected potion is out of stock.", new
            {
                action = "buy_potion",
                option_index = request.option_index
            });
        }

        var previousGold = inventory.Player.Gold;
        var previousPotionId = entry.Model?.Id.Entry;
        var success = await PurchaseMerchantEntryAsync(entry, inventory);
        if (!success)
        {
            throw new ApiException(409, "invalid_action", "Potion purchase failed in the current state.", new
            {
                action = "buy_potion",
                option_index = request.option_index
            });
        }

        var stable = await WaitForMerchantPotionPurchaseAsync(inventory.Player, entry, previousGold, previousPotionId, TimeSpan.FromSeconds(10));
        return new ActionResponsePayload
        {
            action = "buy_potion",
            status = stable ? "completed" : "pending",
            stable = stable,
            message = stable ? "Action completed." : "Action queued but state is still transitioning.",
            state = GameStateService.BuildStatePayload()
        };
    }

    private static async Task<ActionResponsePayload> ExecuteRemoveCardAtShopAsync()
    {
        var currentScreen = ActiveScreenContext.Instance.GetCurrentScreen();
        var screen = GameStateService.ResolveScreen(currentScreen);

        if (!GameStateService.CanRemoveCardAtShop(currentScreen))
        {
            throw new ApiException(409, "invalid_action", "Action is not available in the current state.", new
            {
                action = "remove_card_at_shop",
                screen
            });
        }

        var inventory = GameStateService.GetMerchantInventory(currentScreen)
            ?? throw new ApiException(503, "state_unavailable", "Shop inventory is unavailable.", new
            {
                action = "remove_card_at_shop",
                screen
            }, retryable: true);

        var entry = GameStateService.GetMerchantCardRemovalEntry(currentScreen)
            ?? throw new ApiException(503, "state_unavailable", "Shop card removal service is unavailable.", new
            {
                action = "remove_card_at_shop",
                screen
            }, retryable: true);

        // Fire-and-forget: merchant card removal opens deck selection and blocks
        // until the player confirms a card. Do not await the full task here.
        ObserveBackgroundResult(PurchaseMerchantEntryAsync(entry, inventory), "remove_card_at_shop");
        var stable = await WaitForShopCardRemovalTransitionAsync(TimeSpan.FromSeconds(10));

        return new ActionResponsePayload
        {
            action = "remove_card_at_shop",
            status = stable ? "completed" : "pending",
            stable = stable,
            message = stable ? "Action completed." : "Action queued but state is still transitioning.",
            state = GameStateService.BuildStatePayload()
        };
    }

    private static async Task<bool> WaitForShopInventoryOpenAsync(TimeSpan timeout)
    {
        var deadline = DateTime.UtcNow + timeout;
        while (DateTime.UtcNow < deadline)
        {
            await WaitForNextFrameAsync();

            var currentScreen = ActiveScreenContext.Instance.GetCurrentScreen();
            if (currentScreen is NMerchantInventory inventory && inventory.IsOpen)
            {
                return true;
            }
        }

        return ActiveScreenContext.Instance.GetCurrentScreen() is NMerchantInventory openInventory && openInventory.IsOpen;
    }

    private static async Task<bool> WaitForShopInventoryCloseAsync(TimeSpan timeout)
    {
        var deadline = DateTime.UtcNow + timeout;
        while (DateTime.UtcNow < deadline)
        {
            await WaitForNextFrameAsync();

            var currentScreen = ActiveScreenContext.Instance.GetCurrentScreen();
            if (currentScreen is not NMerchantInventory)
            {
                return true;
            }
        }

        return ActiveScreenContext.Instance.GetCurrentScreen() is not NMerchantInventory;
    }

    private static async Task<bool> WaitForMerchantCardPurchaseAsync(
        Player player,
        MerchantCardEntry entry,
        int previousGold,
        string? previousCardId,
        TimeSpan timeout)
    {
        var deadline = DateTime.UtcNow + timeout;
        while (DateTime.UtcNow < deadline)
        {
            await WaitForNextFrameAsync();

            var currentGold = player.Gold;
            var currentCardId = entry.CreationResult?.Card.Id.Entry;
            if (currentGold != previousGold || currentCardId != previousCardId || !entry.IsStocked)
            {
                return true;
            }
        }

        return false;
    }

    private static async Task<bool> WaitForMerchantRelicPurchaseAsync(
        Player player,
        MerchantRelicEntry entry,
        int previousGold,
        string? previousRelicId,
        TimeSpan timeout)
    {
        var deadline = DateTime.UtcNow + timeout;
        while (DateTime.UtcNow < deadline)
        {
            await WaitForNextFrameAsync();

            var currentGold = player.Gold;
            var currentRelicId = entry.Model?.Id.Entry;
            if (currentGold != previousGold || currentRelicId != previousRelicId || !entry.IsStocked)
            {
                return true;
            }
        }

        return player.Gold != previousGold || entry.Model?.Id.Entry != previousRelicId || !entry.IsStocked;
    }

    private static async Task<bool> WaitForMerchantPotionPurchaseAsync(
        Player player,
        MerchantPotionEntry entry,
        int previousGold,
        string? previousPotionId,
        TimeSpan timeout)
    {
        var deadline = DateTime.UtcNow + timeout;
        while (DateTime.UtcNow < deadline)
        {
            await WaitForNextFrameAsync();

            var currentGold = player.Gold;
            var currentPotionId = entry.Model?.Id.Entry;
            if (currentGold != previousGold || currentPotionId != previousPotionId || !entry.IsStocked)
            {
                return true;
            }
        }

        return player.Gold != previousGold || entry.Model?.Id.Entry != previousPotionId || !entry.IsStocked;
    }

    private static async Task<bool> WaitForShopCardRemovalTransitionAsync(TimeSpan timeout)
    {
        var deadline = DateTime.UtcNow + timeout;
        while (DateTime.UtcNow < deadline)
        {
            await WaitForNextFrameAsync();

            var currentScreen = ActiveScreenContext.Instance.GetCurrentScreen();
            if (currentScreen is NCardGridSelectionScreen || currentScreen is not NMerchantInventory)
            {
                return true;
            }
        }

        var finalScreen = ActiveScreenContext.Instance.GetCurrentScreen();
        return finalScreen is NCardGridSelectionScreen || finalScreen is not NMerchantInventory;
    }

    /// <summary>
    /// 解析药水的目标；返回 null 表示"这瓶药不需要生物目标"。
    ///
    /// ★ 修复 D9（2026-10-19）：原实现把 <c>TargetType.AllEnemies</c> 等**非单目标**类型
    /// 落入 <c>_ =&gt; potion.Owner.Creature</c> 兜底，于是把「玩家自己」当成了目标。
    /// 游戏侧 <c>UsePotionAction.ExecuteAction</c> 对非单目标会走
    /// <c>GetCreatureAsync(TargetId, ...)</c>，随后用 <c>PotionModel.IsValidTarget(creature)</c>
    /// 校验 —— 对 <c>AllEnemies</c> 而言「玩家」是非法目标 ⇒ <c>Cancel()</c>，
    /// 药水永不消耗；mod 的 <c>WaitForPotionUseTransitionAsync</c> 等满 10 秒后只回
    /// <c>status="pending"</c>，插件误判为已执行 ⇒ 求解器反复给出同一条 <c>use_potion</c>
    /// 建议 ⇒ 无限循环（实测单局 7347 次、5 小时 2 分）。
    ///
    /// ★ 判据与游戏保持一致（见 <c>PotionModel.IsValidTarget</c>）：
    ///   <c>target == null</c> 时，只有 <c>TargetedNoCreature</c> 与非单目标类型合法。
    ///   故**非单目标一律传 null**，而不是把自己塞进去。
    /// ★ 游戏自身的 <c>PotionModel.EnqueueManualUse</c> 亦如此：先看
    ///   <c>IsValidTarget(Owner.Creature)</c> 过不过，过才回填自己。
    /// </summary>
}
