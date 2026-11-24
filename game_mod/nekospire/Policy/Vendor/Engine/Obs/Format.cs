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

// 观测格式的版本：常量、按角色定版本、本帧的版本门控。（从 Observation.cs 拆出，见 引擎模块与游戏更新适配.md）
public sealed partial class Observation
{
    public const int FormatVersion = 19;
    /// <summary>
    /// 格式 20（储君，2026-11-06，储君观测调研.md）：已知的抽牌堆顶部（区域 16–18，DrawTop）、手牌星费带修正与“星 X 费”标记、X 费攻击的预览段数、
    /// 回合结束预演按能量与星两种资源取最大伤害（已知在顶部的“我无敌”算进回合结束格挡）、遗物“本回合已触发”、能力的内部数据、星的战斗统计。
    /// 只对储君输出：这一帧的版本按角色定（Encode 开头），其他角色照旧是格式 19，帧与以前逐字节相同；reset 带 "obs_format" 时以它为准（核对用）。
    /// FormatVersion 仍是 19：实机桥接按它向策略端报版本。
    /// </summary>
    public const int FormatRegent = 20;
    /// <summary>
    /// 格式 21（静默猎手，2026-11-08，猎人观测调研.md）：格式 20 的全部内容，再加：敌人中毒记录 12、13（下一个敌方回合开始时毒实际打掉的血、毒不毒得死；PoisonNext），
    /// 刀刃陷阱与毒性爆发的候选 4–7，出牌附带的紧勒、群蛇形态算进候选 4–7、余像的格挡算进候选 8（SilentPreview），
    /// 战斗计数子类型 2（本回合弃牌数、本场抽牌数）。只对静默猎手输出；故障机器人仍是 19、储君仍是 20，帧与以前逐字节相同。
    /// </summary>
    public const int FormatSilent = 21;
    /// <summary>
    /// 格式 22（铁甲战士、亡灵契约师，2026-11-08，铁甲与亡灵契约师观测调研.md）：格式 21 的全部内容，再加：
    /// 奥斯提（Osty，玩家的宠物）替玩家挨打算进回合结束预演（玩家 17、战斗计数 13、候选 10；奥斯提自己的记录 16–17 为它预计掉的血与剩下的生命），
    /// 奥斯提的能力记录放在我方区域、不输出招式；奥斯提出手的攻击牌（OstyDamage、奥斯提不在时为 0）的伤害预览；召唤让候选 10 按奥斯提多出的生命重算；
    /// 灾厄记录 12–13（敌方回合结束时会不会被灾厄处决、离处决还差多少血），末日降临当场处决的候选 7；
    /// 出牌的自伤算进候选 10；狂怒、无惧疼痛、卡戎之灰、势不可当（Juggernaut）、狱火、骨笛这些出牌附带效果算进候选 4–8；
    /// 回合结束预演算荆棘、火焰屏障的反伤（攻击者被反伤打死后剩下的段不再打）与虚无牌回合末消耗时的无惧疼痛、卡戎之灰；
    /// 战斗计数子类型 3（本场受到没被挡住的伤害次数、本回合非回合开始抽的牌数、本场打出的虚无牌数、奥斯提本回合攻击次数、本场召唤总量）。
    /// 只对铁甲战士、亡灵契约师输出；故障机器人 19、储君 20、静默猎手 21 的帧与以前逐字节相同。
    /// </summary>
    public const int FormatIronNecro = 22;
    /// <summary>
    /// 格式 23（静默猎手第二版，2026-11-10，猎人观测调研.md 第七节第一档）：格式 21 的全部内容，加格式 22 里对猎人起作用的两项
    /// （回合结束预演的荆棘反伤、再打一张牌就塞进手里的凋萎算进候选 10），再加候选 10 的三项（见 Fx23）：
    /// 出牌、用药水给敌人上的虚弱、减力量（中和、扫腿、烟雾、萎靡、钩拳、尖啸、虚弱药水、束缚药水，人工制品按次数挡），
    /// 出牌让敌人涨的伤害（实验体的激怒：打出技能加力量；感染棱柱的感染：打出被感染的技能，本回合每段攻击加伤），
    /// 这一步加毒、打伤之后毒得死的敌人（致命毒药、蛇咬、带毒刺击、咕嘟冒泡、烟雾、只剩一个敌人时的弹跳药瓶、毒药水、触媒、毒性爆发结算后剩下的毒）不算它的攻击；
    /// 回合结束预演判断毒得死改用 PoisonNext（含滑溜、缓冲、硬化外壳）。布局与格式 21 相同，只改候选 10–11 与回合结束预演的数值。
    /// 不含格式 22 的其余内容（奥斯提、灾厄、铁甲战士的附带效果、战斗计数子类型 3）：对猎人不起作用，或是猎人模型没见过的新记录。
    /// 2026-11-10 起静默猎手默认输出格式 23（猎人决策记录第 9 条）；reset 带 "obs_format": 21 仍可要格式 21。故障机器人 19、储君 20、铁甲战士与亡灵契约师 22 的帧不变。
    /// </summary>
    public const int FormatSilent2 = 23;
    /// <summary>
    /// 格式 24（Fool，游戏 v0.113.0 的新角色，2026-11-12，适配报告与 fool_analysis）：格式 23 的全部内容（含格式 20 的已知牌堆顶、能力内部数据、
    /// 遗物本回合已触发，格式 21/23 的候选与预演口径，荆棘反伤），加格式 22 的灾厄 12–13（塔罗“死亡”给 75 灾厄），再加 Fool 专属（V24）：
    /// 战斗计数子类型 4（箭矢、旋律、本场生成的悲伤，AddFoolStats）；扎穿（ImpaledPower）能力记录 12 扎着几支箭、13 全部拔出还要打几段；
    /// 射击牌（标签 Shoot）没有可用的箭时伤害预览为 0（ArrowCmd 没箭时整张牌不生效），钉住对没被扎穿的目标伤害为 0；弹跳（Bounce）同流星锤记下回合回手。
    /// 只对 Fool 输出；其余五个角色的帧不变（tools/engine_equal.py 核对）。
    /// </summary>
    public const int FormatFool = 24;
    /// <summary>
    /// 格式 25（Fool 第二版，2026-11-13，游戏更新v0.113.0/观测独立核查.md）：格式 24 的全部内容，再加（V25）：
    /// 出牌候选 4–7 的 Fool 附带伤害（FoolPreview：爆炸箭矢扎穿时打全体、散雨射击的溅射、暴力和弦第一张哇打全体、大树将倾打死目标后打全体、审判斩杀；
    /// 射击牌打出多次时只有前几次有箭）；候选 8 的多次格挡（啾 四肢两次、啾 坚定有箭时两次）与附带格挡（歌之道第一张啾、摇篮曲凑齐三种旋律、
    /// 万事无忧每生成一张悲伤、合股线每拔出一支箭）；重复射击的 X 段（XHits）；Fx23 的减攻击加刻薄、蹒跚射击（射中才上）、钉住（扎穿才上）、女皇；
    /// 回合结束预演：傻瓜回合末自动打出费用最低的牌（最低费用的牌唯一或完全相同时）、瓦皮（WuppysLoyaltyPower）在格挡之前替玩家挨有力攻击、
    /// 敌人 18“本回合最多还能打多少”的背包里射击牌受可用箭数限制；我方宠物（瓦皮）的能力放在我方区域、不输出招式（同格式 22 的奥斯提）。
    /// 布局与格式 24 相同。只对 Fool 输出；reset 带 "obs_format": 24 仍可要格式 24。其余五个角色的帧不变。
    /// </summary>
    public const int FormatFool2 = 25;
    /// <summary>
    /// 格式 26（Fool 第三版，2026-11-13，游戏更新v0.113.0/观测独立核查.md 第十二节）：格式 25 的全部内容（含 F113 各轮修正），
    /// 候选特征从 12 项加到 14 项（V26）：12 这一步当场得到的能量，13 这一步之后进手的牌数（抽牌、回手）。别的格式的帧照旧 12 项（Python 端补 0）。
    /// 只对 Fool 输出；reset 带 "obs_format": 25 仍可要格式 25。
    /// </summary>
    public const int FormatFool3 = 26;
    /// <summary>
    /// 格式 27（Fool 第四版，2026-11-14，观测格式.md 四之十二）：格式 26 的全部内容（含 F113 各轮修正），每条记录的特征从 20 项加到 22 项（V27）：
    /// 手牌记录 20 标黄、21 标红（游戏自己给牌算的发光判断 CardModel.ShouldGlowGold / ShouldGlowRed：条件满足、额外效果生效时标黄，
    /// 射击牌没箭、奥斯提不在场、凡庸挡住出牌等标红；附魔的发光 CardModel 已经算进去了）。手牌以外的牌和别的记录这两项为 0。
    /// 别的格式的帧照旧 20 项（Python 端补 0）。Fool 默认是这个格式；别的角色 reset 带 "obs_format": 27 也可以要（恐惧线的故障机器人）。
    /// reset 带 "obs_format": 24、25、26 仍可要旧格式。
    /// </summary>
    public const int FormatFool4 = 27;
    /// <summary>
    /// 格式 28（铁甲战士第二版，2026-11-17，铁甲战士决策记录第 1 条）：格式 27 的全部内容（V23–V27 为真，含 F113 各轮修正、候选 12–13、手牌标黄标红），
    /// 再加格式 22 的全部铁甲战士、亡灵契约师内容（V22 也为真）：出牌自伤与召唤进候选 10、狂怒、无惧疼痛、卡戎之灰、势不可当、狱火、骨笛、恶魔之焰段数等附带效果（Riders22），
    /// 虚无牌回合末消耗、奥斯提替玩家挨打与亡灵精通进回合结束预演、奥斯提出手的伤害预览、战斗计数子类型 3。格式 23 起的回合结束预演分支按格式 22 的口径带上奥斯提与召唤（Loss22）。
    /// 起因：V22 原来只在格式 22 为真，铁甲战士要格式 27 就会丢掉格式 22 的内容，用格式 22 又没有格式 23–27 的内容。
    /// 2026-11-17 起铁甲战士默认是这个格式，亡灵契约师同日稍后也改为默认 28（亡灵契约师决策记录第 2 条）。reset 带 "obs_format": 22、27 仍可要旧格式，那两个格式的帧不变。
    /// （AlphaDefect 快照：亡灵契约师的默认 28 取自亡灵契约师训练线；铁甲战士训练线上它仍是 22。）
    /// </summary>
    public const int FormatIron2 = 28;
    /// <summary>格式 27、28 的每条记录特征数（格式 26 及以前为 TokFeats = 20）。</summary>
    public const int TokFeats27 = 22;
    /// <summary>
    /// 格式 29（六个角色统一，2026-11-20，研究报告/2026-11-20_全角色观测缺口调研/R3-格式29规格.md，铁甲战士决策记录第 27 条）：格式 28 的全部内容
    /// （V20–V27、V22、F113 全开），再加这次调研的修正与新内容（V29）。所有修正只在 V29 下生效，格式 19–28 的帧逐字节不变。
    /// 每条记录的特征从 22 项加到 30 项（新列 22–29，各记录类型各自编号，写在 _y 里），每个候选的特征从 14 项加到 20 项
    /// （新列 14–19，战斗内、战斗外各自编号，写在 _cy 里）；帧头不扩。reset 带 "obs_format": 29 可要；各角色默认格式另定（FormatFor）。
    /// </summary>
    public const int FormatAll = 29;
    public const int TokFeats29 = 30, CandFeats29 = 20, CandFeats26 = 14;
    public static int? FormatOverride;
    /// <summary>这一帧是格式 20、21 及以上，格式 22，格式 23（Encode 开头设、结尾清；Preview 等静态方法按它分支）。
    /// 格式 23 由格式 21 分出，不含格式 22，所以 V22 只在格式 22、28 为真；格式 22 里猎人也用的荆棘与凋萎看 V22T。
    /// 格式 24–28 含格式 23（V23 为真）；格式 25–28 含格式 24（V24 为真）；格式 26–28 含格式 25、26 的内容（V25、V26 为真）；V27 在格式 27、28 为真；
    /// 格式 28 = 格式 27 + 格式 22（V22 与 V23–V27 都为真）。</summary>
    static bool V20, V21, V22, V23, V24, V25, V26, V27, V29;
    /// <summary>
    /// v0.113.0 的观测修正（2026-11-13，游戏更新v0.113.0/观测独立核查.md）：格式 19–23 与 25 都打开，格式 24 不打开（冻结成 Fool 第一批检查点用的帧）。
    /// 内容见 观测格式.md 四之七：瓦皮在格挡之前替玩家挨打、我方宠物的能力放我方区域且不输出招式、瓦皮遗物的最大生命与休息处“恢复”、浴巾浴袍进休息回血、
    /// 龙涎香与倒吊人的额外回合进候选 10、头部 31 补上本回合刚挂的“下回合抽牌”、冰枪不再按 Repeat 算 3 段、拆卸与怨恨按条件给段数、恶魔之焰与疯狂科学的
    /// 段数过拇指夹、折射的玻璃球进候选 8–9、亡者之门的多次格挡、手牌外计算型伤害按局面算、异鸟猛击不进本回合背包，以及别的角色拿到 Fool 的牌时同样生效的
    /// Fool 口径（没箭的射击牌、钉住、弹跳、重复射击、哇 爆破、啾 四肢与啾 坚定、Fool 的附带伤害与格挡、傻瓜、背包的箭数约束、Fx23 的 Fool 减攻击牌）。
    /// 只有这些情况出现时帧才变。环境变量 STS2_OBS_NO_FIX113=1 时只有格式 25 打开（格式 19–23 与这次修正之前逐字节相同，对拍、老模型直接上新版本用）。
    /// </summary>
    static bool F113;
    static readonly bool NoFix113 = Environment.GetEnvironmentVariable("STS2_OBS_NO_FIX113") == "1";
    /// <summary>格式 22 的荆棘反伤与凋萎（格式 23 也有）。</summary>
    static bool V22T => V22 || V23;

    /// <summary>这个角色的帧用哪个观测格式（按角色查表，与 Python 的 protocol.format_for 一致）：储君 20，静默猎手 23（2026-11-10 前为 21），铁甲战士 29（2026-11-20 前为 28，2026-11-17 前为 22），亡灵契约师 28（2026-11-17 前为 22），Fool 27（2026-11-14 前为 26，再之前 25、24），其余 19。
    /// 格式 29 六个角色都能用（reset 带 "obs_format": 29）；别的角色的默认格式等各自的训练线恢复时再定。</summary>
    public static int FormatFor(CharacterModel? c) => c switch
    {
        MegaCrit.Sts2.Core.Models.Characters.Regent => FormatRegent,
        MegaCrit.Sts2.Core.Models.Characters.Silent => FormatSilent2,
        MegaCrit.Sts2.Core.Models.Characters.Ironclad => FormatAll,            // 2026-11-20 起 29（铁甲战士决策记录第 27 条；之前 28，再之前 22；旧格式 reset 带 "obs_format" 仍可要）
        MegaCrit.Sts2.Core.Models.Characters.Necrobinder => FormatIron2,       // 2026-11-17 起 28（亡灵契约师决策记录第 2 条；格式 22 的帧 reset 带 "obs_format": 22 仍可要）
        MegaCrit.Sts2.Core.Models.Characters.Fool => FormatFool4,      // 2026-11-14 起 27（格式 24、25、26 的帧 reset 带 "obs_format" 仍可要）
        _ => FormatVersion,
    };
}
