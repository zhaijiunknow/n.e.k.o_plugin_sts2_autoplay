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

// Events implementation; shares the service API and state across partial files.
internal static partial class GameActionService
{
    private static async Task<ActionResponsePayload> ExecuteChooseEventOptionAsync(ActionRequest request)
    {
        var currentScreen = ActiveScreenContext.Instance.GetCurrentScreen();
        var screen = GameStateService.ResolveScreen(currentScreen);

        if (!GameStateService.CanChooseEventOption(currentScreen))
        {
            throw new ApiException(409, "invalid_action", "Action is not available in the current state.", new
            {
                action = "choose_event_option",
                screen
            });
        }

        if (request.option_index == null)
        {
            throw new ApiException(400, "invalid_request", "choose_event_option requires option_index.", new
            {
                action = "choose_event_option"
            });
        }

        var eventModel = RunManager.Instance.EventSynchronizer.GetLocalEvent()
            ?? throw new ApiException(503, "state_unavailable", "Event state is unavailable.", new
            {
                action = "choose_event_option",
                screen
            }, retryable: true);

        if (eventModel.IsFinished)
        {
            // Finished events only have the synthetic proceed option at index 0
            if (request.option_index != 0)
            {
                throw new ApiException(409, "invalid_target", "Event is finished. Only option_index 0 (proceed) is valid.", new
                {
                    action = "choose_event_option",
                    option_index = request.option_index,
                    is_finished = true
                });
            }

            await NEventRoom.Proceed();
            var stable = await WaitForEventScreenTransitionAsync(TimeSpan.FromSeconds(10));

            return new ActionResponsePayload
            {
                action = "choose_event_option",
                status = stable ? "completed" : "pending",
                stable = stable,
                message = stable ? "Event proceeded." : "Proceed queued but state is still transitioning.",
                state = GameStateService.BuildStatePayload()
            };
        }

        // Non-finished event: choose an option
        var options = eventModel.CurrentOptions;
        if (request.option_index < 0 || request.option_index >= options.Count)
        {
            throw new ApiException(409, "invalid_target", "option_index is out of range.", new
            {
                action = "choose_event_option",
                option_index = request.option_index,
                option_count = options.Count
            });
        }

        if (options[request.option_index.Value].IsLocked)
        {
            throw new ApiException(409, "invalid_target", "The selected event option is locked.", new
            {
                action = "choose_event_option",
                option_index = request.option_index
            });
        }

        RunManager.Instance.EventSynchronizer.ChooseLocalOption(request.option_index.Value);
        var stableOption = await WaitForEventOptionTransitionAsync(
            eventModel.Id?.Entry,
            BuildEventOptionSignature(eventModel),
            options.Count,
            TimeSpan.FromSeconds(10));

        return new ActionResponsePayload
        {
            action = "choose_event_option",
            status = stableOption ? "completed" : "pending",
            stable = stableOption,
            message = stableOption ? "Action completed." : "Action queued but state is still transitioning.",
            state = GameStateService.BuildStatePayload()
        };
    }

    /// <summary>
    /// 水晶球"占卜"子屏（`NCrystalSphereScreen`）的**揭格子**动作。
    ///
    /// 为什么需要它：这一屏既不是 `NEventRoom`（所以 `choose_event_option` 不管用），
    /// 进小游戏后 `EventSynchronizer.GetLocalEvent()` 也不再有可选项 —— 屏上真正的可操作对象
    /// 是 `CrystalSphereMinigame.cells[,]`（11x11）。屏名被 `ResolveScreenFromStaticRoom()`
    /// 兜底成 "EVENT"，于是「名字说有事件、动作却一个都没有」，整局永久卡死。
    ///
    /// ★ 实现刻意**对齐游戏自己的 `CrystalSphereScreenHandler`**（已反编译其 IL，
    ///   见 .dsh-artifacts/_t33_PROBE-SUMMARY.md）：它也是拿 `%Cells` 下的
    ///   `NCrystalSphereCell` 节点、发 `SignalName.Released`，而不是像素/坐标点击。
    ///   这里复用同一条具名节点路径，所以不会随版本漂移。
    ///
    /// 参数：复用 `option_index` 作为**格子序号** `y * grid_width + x`（不新增请求字段，
    /// 避免动到插件与 mod 之间的请求协议）。不传则让游戏自己挑一格（同官方 handler 的随机行为）。
    /// </summary>
    private static async Task<ActionResponsePayload> ExecuteDivineCrystalSphereAsync(ActionRequest request)
    {
        var currentScreen = ActiveScreenContext.Instance.GetCurrentScreen();
        var screen = GameStateService.ResolveScreen(currentScreen);

        if (currentScreen is not Node root || !currentScreen.GetType().Name.Contains("CrystalSphere"))
        {
            throw new ApiException(409, "invalid_action", "Action is not available in the current state.", new
            {
                action = "divine_crystal_sphere",
                screen
            });
        }

        // 官方 handler 用的是 `%Cells` 具名节点容器。
        var cellsContainer = root.GetNodeOrNull<Control>("%Cells");
        if (cellsContainer == null)
        {
            throw new ApiException(503, "state_unavailable", "Crystal sphere cells container (%Cells) not found.", new
            {
                action = "divine_crystal_sphere",
                screen
            }, retryable: true);
        }

        var clickable = new List<NCrystalSphereCell>();
        CollectClickableCells(cellsContainer, clickable);
        if (clickable.Count == 0)
        {
            throw new ApiException(409, "invalid_target", "No clickable crystal sphere cells remain.", new
            {
                action = "divine_crystal_sphere",
                screen
            });
        }

        NCrystalSphereCell target;
        if (request.option_index is int idx)
        {
            if (idx < 0 || idx >= clickable.Count)
            {
                throw new ApiException(409, "invalid_target", "cell index is out of range.", new
                {
                    action = "divine_crystal_sphere",
                    cell_index = idx,
                    cell_count = clickable.Count
                });
            }

            target = clickable[idx];
        }
        else
        {
            target = clickable[0];
        }

        // ★★ 序号 ↔ 坐标的**权威映射**就在此处。
        //   `CollectClickableCells` 是按**场景树节点顺序**递归的，
        //   而 `BuildCrystalSpherePayload.candidates` 是按 `cells[,]` 的**行优先**顺序 ——
        //   两者**顺序未必一致**（前者取决于 .tscn 里节点的排列）。
        //   ⇒ 插件若拿 candidates[i] 当 option_index=i 的坐标去推理"揭了哪块"，可能错位。
        //   ⇒ 这里把**真正被点的那一格**的坐标回给调用方，让日志/复盘有权威依据，
        //     而不是让插件靠顺序去猜（CANON §23：「枚举要给出有哪些可能」，
        //     但"序号代表哪一格"必须由**持有序号解释权的一方**说明）。

        // ★★★ 必须调 `ForceClick()`，**不是**单独发 `Released` 信号。
        //
        //   【为什么】实测（2026-10-20 07:03，run C6CM8CZRR9KQ / act1 / floor23）：
        //   只发 `EmitSignal(NClickableControl.SignalName.Released)` 时，
        //   序号 0..96 / 102 全部返回 HTTP 200 且 **cells_hidden 恒为 97、divinations_left 恒为 3**，
        //   连发 5 次 + 等 10 秒仍无变化 ⇒ 信号发出去了但**没人接**。
        //
        //   【真因】`NClickableControl.ForceClick()` 的反编译 IL 是：
        //       callvirt NClickableControl.OnRelease
        //       ldsfld   SignalName.Released
        //       call     GodotObject.EmitSignal
        //   ⇒ 官方点击其实是【两步】：先 `OnRelease()`（把按钮内部状态从"按下"翻到"弹起"），
        //     **再**发 `Released` 信号。我只做了第二步 ⇒ 按钮状态机没走完 ⇒ 信号无效。
        //
        //   【权威依据】游戏自己的 `AutoSlay.Helpers.UiHelper.Click(NClickableControl, Int32)`
        //   的 `<Click>d__0.MoveNext` IL 只有两句实质内容：
        //       callvirt NClickableControl.ForceClick
        //       call     Task.Delay
        //   ⇒ **官方自动化就是 ForceClick + 等**。本方法现在与它同款。
        //     （我前一轮写"与官方同款：发 Released 信号"，那是**读错了 IL** ——
        //       官方 handler 里那行 `EmitSignal(Node.SignalName.Released)` 存在，
        //       但真正生效的路径是 UiHelper.Click → ForceClick。此处已更正。）
        target.ForceClick();

        // ★★ 序号 ↔ 坐标的**权威映射**必须在日志里能读到。
        //   `CollectClickableCells` 按**场景树节点顺序**递归，
        //   而 `BuildCrystalSpherePayload.candidates` 按 `cells[,]` 的**行优先**顺序 ——
        //   两者顺序**未必一致** ⇒ 插件不能想当然认为 candidates[i] 就是 option_index=i 的坐标。
        //   这里把**真正被点的那一格**打印出来，让日志/复盘有权威依据（而不是靠顺序去猜）。
        var clickedX = GetNodeCellX(target);
        var clickedY = GetNodeCellY(target);
        try
        {
            Godot.GD.Print($"[nekospire] divine_crystal_sphere idx={request.option_index} " +
                           $"count={clickable.Count} clicked=({clickedX},{clickedY})");
        }
        catch
        {
            // 日志失败绝不影响动作本身
        }

        var stable = await WaitForCrystalSphereProgressAsync(target, TimeSpan.FromSeconds(8));

        return new ActionResponsePayload
        {
            action = "divine_crystal_sphere",
            status = stable ? "completed" : "pending",
            stable = stable,
            message = stable
                ? $"Revealed cell ({clickedX},{clickedY})."
                : $"Click sent to cell ({clickedX},{clickedY}) but no visible progress yet.",
            state = GameStateService.BuildStatePayload()
        };
    }

    /// <summary>读 `NCrystalSphereCell.Entity.X`（场景节点 → 游戏实体）。失败返回 -1。</summary>
    private static int GetNodeCellX(NCrystalSphereCell cell)
        => GetReflectedIntOf(GetReflectedPropertyOf(cell, "Entity"), "X", -1);

    /// <summary>读 `NCrystalSphereCell.Entity.Y`。失败返回 -1。</summary>
    private static int GetNodeCellY(NCrystalSphereCell cell)
        => GetReflectedIntOf(GetReflectedPropertyOf(cell, "Entity"), "Y", -1);

    /// <summary>
    /// 等到"这一揭真的生效了"或超时。
    ///
    /// ★ 原来只看 `previousCell.GetParent().GetChildCount()` —— **它永远不变**：
    ///   格子在游戏过程中不会增删（官方 handler 是靠"可点格数变化"判断进度，
    ///   因为官方**逐个剔除**已揭的格子；我们不动节点，所以那条路对我不适用）。
    ///   实测后果：即使真的揭开了也会被判 `pending`（假阴性）。
    ///
    /// ⇒ 改为观察**真正的进度量**：
    ///   · 该屏的 `_entity.DivinationCount`（每揭一次 -1，`:CellClicked` 里递减）；
    ///   · 该格的 `IsHidden` 由 true 变 false；
    ///   · 该格离开了树（被清掉）或屏被关掉。
    ///   任一发生即视为"生效"。
    /// </summary>
    private static async Task<bool> WaitForCrystalSphereProgressAsync(Node clickedCell, TimeSpan timeout)
    {
        var deadline = DateTime.UtcNow + timeout;
        var cellEntityBefore = GetReflectedPropertyOf(clickedCell, "Entity");
        var divBefore = GetReflectedIntOf(cellEntityBefore, "DivinationCount", -1);
        var hiddenBefore = SafeReadBoolOf(cellEntityBefore, "IsHidden");

        while (DateTime.UtcNow < deadline)
        {
            await WaitForNextFrameAsync();

            // ① 屏/格子没了 ⇒ 一定发生了变化
            if (!GodotObject.IsInstanceValid(clickedCell))
            {
                return true;
            }

            // ② 该格已被揭开
            if (hiddenBefore && !SafeReadBoolOf(cellEntityBefore, "IsHidden"))
            {
                return true;
            }

            // ③ 占卜次数变了（`CellClicked` 里 `DivinationCount--`）
            var divNow = GetReflectedIntOf(cellEntityBefore, "DivinationCount", divBefore);
            if (divBefore >= 0 && divNow != divBefore)
            {
                return true;
            }
        }

        return false;
    }

    /// <summary>反射取属性/字段（**复用 `GameStateService` 的实现**，不另起一套）。失败返回 null，不抛。</summary>
    private static object? GetReflectedPropertyOf(object? target, string name)
    {
        if (target == null)
        {
            return null;
        }

        try
        {
            return GameStateService.GetReflectedProperty(target, name)
                   ?? GameStateService.GetReflectedField(target, name);
        }
        catch
        {
            return null;
        }
    }

    /// <summary>反射取 int（失败返回 fallback）。用于跨程序集读 Godot 节点上的游戏对象。</summary>
    private static int GetReflectedIntOf(object? target, string name, int fallback)
    {
        var v = GetReflectedPropertyOf(target, name);
        return v is int i ? i : fallback;
    }

    /// <summary>反射取 bool（失败返回 false）。</summary>
    private static bool SafeReadBoolOf(object? target, string name)
        => GetReflectedPropertyOf(target, name) is bool b && b;

    /// <summary>递归收集 `%Cells` 下可点的格子（沿用官方 handler 的 UiHelper.FindAll 语义）。</summary>
    private static void CollectClickableCells(Node node, List<NCrystalSphereCell> found)
    {
        if (node is NCrystalSphereCell cell && cell.IsEnabled)
        {
            found.Add(cell);
        }

        foreach (var child in node.GetChildren())
        {
            if (child is Node n)
            {
                CollectClickableCells(n, found);
            }
        }
    }

    /// <summary>
    /// Waits for screen to leave NEventRoom (used after proceed).
    /// </summary>
    private static async Task<bool> WaitForEventScreenTransitionAsync(TimeSpan timeout)
    {
        var deadline = DateTime.UtcNow + timeout;
        while (DateTime.UtcNow < deadline)
        {
            await WaitForNextFrameAsync();

            var currentScreen = ActiveScreenContext.Instance.GetCurrentScreen();
            if (currentScreen is not NEventRoom)
            {
                return true;
            }
        }

        return false;
    }

    /// <summary>
    /// Waits for event state to change after choosing an option.
    /// Detects: screen change, IsFinished change, or options count change.
    /// </summary>
    private static async Task<bool> WaitForEventOptionTransitionAsync(
        string? previousEventId,
        string previousOptionSignature,
        int previousOptionCount,
        TimeSpan timeout)
    {
        var deadline = DateTime.UtcNow + timeout;
        while (DateTime.UtcNow < deadline)
        {
            await WaitForNextFrameAsync();

            var currentScreen = ActiveScreenContext.Instance.GetCurrentScreen();

            // Screen changed entirely (e.g. combat started from event)
            if (currentScreen is not NEventRoom)
            {
                return true;
            }

            var currentEventModel = RunManager.Instance.EventSynchronizer.GetLocalEvent();
            if (currentEventModel == null)
            {
                continue;
            }

            if (currentEventModel.Id?.Entry != previousEventId)
            {
                return true;
            }

            if (currentEventModel.IsFinished)
            {
                return true;
            }

            if (currentEventModel.CurrentOptions.Count != previousOptionCount)
            {
                return true;
            }

            if (BuildEventOptionSignature(currentEventModel) != previousOptionSignature)
            {
                return true;
            }
        }

        return false;
    }

    private static async Task<ActionResponsePayload> ExecuteChooseCapstoneOptionAsync(ActionRequest request)
    {
        var currentScreen = ActiveScreenContext.Instance.GetCurrentScreen();
        var screen = GameStateService.ResolveScreen(currentScreen);

        if (!GameStateService.CanChooseCapstoneOption(currentScreen))
        {
            throw new ApiException(409, "invalid_action", "Action is not available in the current state.", new
            {
                action = "choose_capstone_option",
                screen
            });
        }

        if (request.option_index == null)
        {
            throw new ApiException(400, "invalid_request", "choose_capstone_option requires option_index.", new
            {
                action = "choose_capstone_option"
            });
        }

        var buttons = GameStateService.GetCapstoneButtons(currentScreen);
        if (request.option_index < 0 || request.option_index >= buttons.Count)
        {
            throw new ApiException(409, "invalid_target", "option_index is out of range.", new
            {
                action = "choose_capstone_option",
                option_index = request.option_index,
                button_count = buttons.Count
            });
        }

        var button = buttons[request.option_index.Value];
        button.EmitSignal(BaseButton.SignalName.Pressed);

        // Wait for screen transition
        var deadline = DateTime.UtcNow + TimeSpan.FromSeconds(10);
        var stable = false;
        while (DateTime.UtcNow < deadline)
        {
            await WaitForNextFrameAsync();
            var newScreen = ActiveScreenContext.Instance.GetCurrentScreen();
            if (newScreen is not NCapstoneSubmenuStack)
            {
                stable = true;
                break;
            }
        }

        return new ActionResponsePayload
        {
            action = "choose_capstone_option",
            status = stable ? "completed" : "pending",
            stable = stable,
            message = stable ? "Action completed." : "Action queued but state is still transitioning.",
            state = GameStateService.BuildStatePayload()
        };
    }

    private static async Task<ActionResponsePayload> ExecuteChooseBundleAsync(ActionRequest request)
    {
        var currentScreen = ActiveScreenContext.Instance.GetCurrentScreen();
        var screen = GameStateService.ResolveScreen(currentScreen);

        if (!GameStateService.CanChooseBundle(currentScreen))
        {
            throw new ApiException(409, "invalid_action", "Action is not available in the current state.", new
            {
                action = "choose_bundle",
                screen
            });
        }

        if (request.option_index == null)
        {
            throw new ApiException(400, "invalid_request", "choose_bundle requires option_index.", new
            {
                action = "choose_bundle"
            });
        }

        var bundles = GameStateService.GetBundleOptions(currentScreen);
        if (request.option_index < 0 || request.option_index >= bundles.Count)
        {
            throw new ApiException(409, "invalid_target", "option_index is out of range.", new
            {
                action = "choose_bundle",
                option_index = request.option_index,
                bundle_count = bundles.Count
            });
        }

        var bundle = bundles[request.option_index.Value];
        // Call the screen's OnBundleClicked method directly
        if (currentScreen is NChooseABundleSelectionScreen bundleScreen)
        {
            ((Node)bundleScreen).Call("OnBundleClicked", bundle);
        }

        // Wait for screen transition
        var deadline = DateTime.UtcNow + TimeSpan.FromSeconds(10);
        var stable = false;
        while (DateTime.UtcNow < deadline)
        {
            await WaitForNextFrameAsync();
            var newScreen = ActiveScreenContext.Instance.GetCurrentScreen();
            if (newScreen is not NChooseABundleSelectionScreen)
            {
                stable = true;
                break;
            }
        }

        GameStatePayload? bundleState = null;
        try { bundleState = GameStateService.BuildStatePayload(); } catch { }

        return new ActionResponsePayload
        {
            action = "choose_bundle",
            status = stable ? "completed" : "pending",
            stable = stable,
            message = stable ? "Action completed." : "Action queued but state is still transitioning.",
            state = bundleState ?? new GameStatePayload()
        };
    }

    private static async Task<ActionResponsePayload> ExecuteConfirmBundleAsync()
    {
        var currentScreen = ActiveScreenContext.Instance.GetCurrentScreen();
        var screen = GameStateService.ResolveScreen(currentScreen);

        if (!GameStateService.CanConfirmBundle(currentScreen))
        {
            throw new ApiException(409, "invalid_action", "Action is not available in the current state.", new
            {
                action = "confirm_bundle",
                screen
            });
        }

        var buttons = GameStateService.GetBundleConfirmButtons(currentScreen);
        if (buttons.Count == 0)
        {
            throw new ApiException(409, "invalid_action", "No confirm button found.", new
            {
                action = "confirm_bundle",
                screen
            });
        }

        var confirmBtn = buttons[0];
        Log.Info($"[NekoComm] confirm_bundle: clicking {confirmBtn.GetType().Name} '{confirmBtn.Name}'");

        // Try ForceClick first
        confirmBtn.ForceClick();
        await WaitForNextFrameAsync();

        // If still on bundle screen, try calling OnConfirmPressed on the screen
        var stable = false;
        if (ActiveScreenContext.Instance.GetCurrentScreen() is NChooseABundleSelectionScreen bundleScreen2)
        {
            try
            {
                Log.Info("[NekoComm] confirm_bundle: trying OnConfirmPressed");
                ((Node)bundleScreen2).Call("OnConfirmPressed");
            }
            catch { }

            // Also try emitting the button's signal with no args
            try
            {
                confirmBtn.EmitSignal("pressed");
            }
            catch { }
        }

        var deadline = DateTime.UtcNow + TimeSpan.FromSeconds(5);
        while (!stable && DateTime.UtcNow < deadline)
        {
            await WaitForNextFrameAsync();
            var newScreen = ActiveScreenContext.Instance.GetCurrentScreen();
            if (newScreen is not NChooseABundleSelectionScreen)
            {
                stable = true;
            }
        }

        GameStatePayload? state = null;
        try { state = GameStateService.BuildStatePayload(); } catch { }

        return new ActionResponsePayload
        {
            action = "confirm_bundle",
            status = stable ? "completed" : "pending",
            stable = stable,
            message = stable ? "Action completed." : "Action queued but state is still transitioning.",
            state = state ?? new GameStatePayload()
        };
    }

    private static async Task<ActionResponsePayload> ExecuteChooseRestOptionAsync(ActionRequest request)
    {
        var currentScreen = ActiveScreenContext.Instance.GetCurrentScreen();
        var screen = GameStateService.ResolveScreen(currentScreen);

        if (!GameStateService.CanChooseRestOption(currentScreen))
        {
            throw new ApiException(409, "invalid_action", "Action is not available in the current state.", new
            {
                action = "choose_rest_option",
                screen
            });
        }

        if (request.option_index == null)
        {
            throw new ApiException(400, "invalid_request", "choose_rest_option requires option_index.", new
            {
                action = "choose_rest_option"
            });
        }

        var options = RunManager.Instance.RestSiteSynchronizer.GetLocalOptions();
        if (options == null || request.option_index < 0 || request.option_index >= options.Count)
        {
            throw new ApiException(409, "invalid_target", "option_index is out of range.", new
            {
                action = "choose_rest_option",
                option_index = request.option_index,
                option_count = options?.Count ?? 0
            });
        }

        if (!options[request.option_index.Value].IsEnabled)
        {
            throw new ApiException(409, "invalid_target", "The selected rest option is disabled.", new
            {
                action = "choose_rest_option",
                option_index = request.option_index
            });
        }

        var selectedOption = options[request.option_index.Value];
        var selectedOptionId = selectedOption.OptionId ?? string.Empty;
        var runState = RunManager.Instance.DebugOnlyGetState();
        var localPlayer = GameStateService.GetLocalPlayer(runState);
        var requiresTarget = GameStateService.RestOptionRequiresTarget(selectedOption, runState, localPlayer);
        Player? targetPlayer = null;
        if (requiresTarget)
        {
            targetPlayer = ResolveRestOptionTarget(request, runState, localPlayer, selectedOption);
        }

        var chooseTask = RunManager.Instance.RestSiteSynchronizer.ChooseLocalOption(request.option_index.Value);

        bool stable;
        if (requiresTarget)
        {
            stable = await CompleteRestOptionTargetSelectionAsync(chooseTask, targetPlayer!, TimeSpan.FromSeconds(10));
        }
        else if (selectedOptionId.Equals("SMITH", StringComparison.OrdinalIgnoreCase))
        {
            // SMITH keeps the task open until the follow-up card selection
            // completes. Return as soon as the transition into that screen is visible.
            ObserveBackgroundResult(chooseTask, "choose_rest_option");
            stable = await WaitForRestOptionTransitionAsync(TimeSpan.FromSeconds(10));
        }
        else
        {
            stable = await chooseTask;
            var transitionStable = await WaitForRestOptionTransitionAsync(TimeSpan.FromSeconds(stable ? 2 : 10));
            if (!stable)
            {
                stable = transitionStable;
            }
            else
            {
                stable = transitionStable || stable;
            }
        }

        return new ActionResponsePayload
        {
            action = "choose_rest_option",
            status = stable ? "completed" : "pending",
            stable = stable,
            message = stable ? "Action completed." : "Action queued but state is still transitioning.",
            state = GameStateService.BuildStatePayload()
        };
    }

    private static Player ResolveRestOptionTarget(
        ActionRequest request,
        RunState? runState,
        Player? localPlayer,
        RestSiteOption selectedOption)
    {
        var targetIndexSpace = GameStateService.GetRestOptionTargetIndexSpace(selectedOption, runState, localPlayer) ?? "run.players";
        var validTargetIndices = GameStateService.GetRestOptionTargetIndices(runState, localPlayer, allowSelf: false);
        if (request.target_index == null)
        {
            throw new ApiException(409, "invalid_target", "This rest option requires target_index.", new
            {
                action = "choose_rest_option",
                option_index = request.option_index,
                option_id = selectedOption.OptionId,
                target_index_space = targetIndexSpace,
                valid_target_indices = validTargetIndices
            });
        }

        if (!validTargetIndices.Contains(request.target_index.Value))
        {
            throw new ApiException(409, "invalid_target", "target_index is out of range for run.players[].", new
            {
                action = "choose_rest_option",
                option_index = request.option_index,
                option_id = selectedOption.OptionId,
                target_index = request.target_index,
                target_index_space = targetIndexSpace,
                valid_target_indices = validTargetIndices
            });
        }

        return GameStateService.ResolveRunPlayerTarget(runState, request.target_index.Value)
            ?? throw new ApiException(409, "invalid_target", "target_index is out of range for run.players[].", new
            {
                action = "choose_rest_option",
                option_index = request.option_index,
                option_id = selectedOption.OptionId,
                target_index = request.target_index,
                target_index_space = targetIndexSpace,
                valid_target_indices = validTargetIndices
            });
    }

    private static async Task<bool> CompleteRestOptionTargetSelectionAsync(
        Task<bool> chooseTask,
        Player targetPlayer,
        TimeSpan timeout)
    {
        var targetManager = await WaitForTargetManagerSelectionAsync(timeout);
        if (targetManager == null)
        {
            ObserveBackgroundResult(chooseTask, "choose_rest_option");
            return false;
        }

        var targetNode = ResolveRestSiteTargetNode(targetPlayer)
            ?? throw new ApiException(503, "state_unavailable", "Rest-site target node is unavailable.", new
            {
                action = "choose_rest_option",
                target_player_id = targetPlayer.NetId.ToString()
            }, retryable: true);

        targetManager.OnNodeHovered(targetNode);
        targetManager.Call(NTargetManager.MethodName.FinishTargeting, false);

        var result = await WaitForTaskResultAsync(chooseTask, timeout);
        if (result != true)
        {
            if (result == null)
            {
                ObserveBackgroundResult(chooseTask, "choose_rest_option");
            }

            return false;
        }

        return await WaitForRestOptionTransitionAsync(TimeSpan.FromSeconds(2)) || result.Value;
    }

    private static async Task<NTargetManager?> WaitForTargetManagerSelectionAsync(TimeSpan timeout)
    {
        var deadline = DateTime.UtcNow + timeout;
        while (DateTime.UtcNow < deadline)
        {
            var targetManager = NTargetManager.Instance;
            if (targetManager != null && GodotObject.IsInstanceValid(targetManager) && targetManager.IsInSelection)
            {
                return targetManager;
            }

            await WaitForNextFrameAsync();
        }

        var finalTargetManager = NTargetManager.Instance;
        return finalTargetManager != null && GodotObject.IsInstanceValid(finalTargetManager) && finalTargetManager.IsInSelection
            ? finalTargetManager
            : null;
    }

    private static Node? ResolveRestSiteTargetNode(Player targetPlayer)
    {
        var restSiteRoom = NRestSiteRoom.Instance;
        if (restSiteRoom == null || !GodotObject.IsInstanceValid(restSiteRoom))
        {
            return null;
        }

        var character = restSiteRoom.GetCharacterForPlayer(targetPlayer)
            ?? restSiteRoom.Characters.FirstOrDefault(candidate => candidate.Player.NetId == targetPlayer.NetId);
        return character != null && GodotObject.IsInstanceValid(character) ? character : null;
    }

    private static async Task<bool?> WaitForTaskResultAsync(Task<bool> task, TimeSpan timeout)
    {
        var completedTask = await Task.WhenAny(task, Task.Delay(timeout));
        if (completedTask != task)
        {
            return null;
        }

        return await task;
    }

    /// <summary>
    /// Waits for rest site state to change after choosing an option.
    /// Detects: screen change (SMITH 闂?card selection), ProceedButton appearance
    /// (HEAL), or options list change.
    /// </summary>
    private static async Task<bool> WaitForRestOptionTransitionAsync(TimeSpan timeout)
    {
        var deadline = DateTime.UtcNow + timeout;
        while (DateTime.UtcNow < deadline)
        {
            await WaitForNextFrameAsync();

            var currentScreen = ActiveScreenContext.Instance.GetCurrentScreen();

            // Screen changed entirely (e.g. SMITH opened card selection)
            if (currentScreen is not NRestSiteRoom restSiteRoom)
            {
                return true;
            }

            // ProceedButton became available (e.g. after HEAL)
            var proceedButton = restSiteRoom.ProceedButton;
            if (proceedButton != null && GodotObject.IsInstanceValid(proceedButton) && proceedButton.IsEnabled)
            {
                return true;
            }

            var options = RunManager.Instance.RestSiteSynchronizer.GetLocalOptions();
            if (options.Count == 0 || options.All(static option => !option.IsEnabled))
            {
                restSiteRoom.Call(NRestSiteRoom.MethodName.ShowProceedButton);
                ActiveScreenContext.Instance.Update();

                await WaitForNextFrameAsync();
                proceedButton = restSiteRoom.ProceedButton;
                return proceedButton != null && GodotObject.IsInstanceValid(proceedButton) && proceedButton.IsEnabled;
            }
        }

        return false;
    }

}
