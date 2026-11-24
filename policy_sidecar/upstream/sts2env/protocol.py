"""观测帧的解析。布局与 engine/Sts2Headless/Engine/Observation.cs 一致，改动时两边同时改 FORMAT_VERSION。"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import IntEnum

import numpy as np

FORMAT_VERSION = 19     # 版本 19（决策记录 227）：候选级的格挡与回合末掉血预演、下回合实际进手张数、商店里买不起的商品。
# 版本 18（决策记录 210）：奖励页与选牌时亮出全部没领的卡牌奖励和其他奖励、问号房实际概率、滑脚木桥重抽候选池、下回合抽牌张数
# 版本 17（决策记录 209）：商店删牌次数与下一次价格、能力在列表里的先后、无休手斧与流星锤的回手资格、迭代本回合是否还能触发
# 版本 16（决策记录 204–206）：有效抽牌、事件选项展示过的信息、休息前确定的回血、会员卡折后价、传送带取菜数
# 版本 13 只在原有布局里加了记录（卡牌苦痛的能力记录、战斗外的路线摘要）和以前为 0 的特征位置，版本 12 的帧照样能读（新信息为 0）：
# 实机桥接换新引擎之前，新的 Python 端仍能接旧桥接
# 版本 20（储君，2026-11-06，储君观测调研.md）：只有储君的帧是这个版本（引擎按角色定，其他角色仍为 19，逐字节与以前相同）。布局与 19 相同，
# 新信息都在原有的列或新的区域、记录里：已知的抽牌堆顶部（区域 16–18）、手牌星费带修正与“星 X 费”（卡牌特征 1 为 2）、X 费攻击的预览段数、
# 遗物 8 本回合已触发、能力 8–11 内部数据、星的战斗统计（能力类记录 STAT:STARS）。FORMAT_VERSION 仍为 19（实机桥接按它核对）
# 版本 21（静默猎手，2026-11-08，猎人观测调研.md）：只有静默猎手的帧是这个版本。布局与 20 相同，内容是格式 20 再加：敌人中毒记录 12、13
# （下一个敌方回合开始时毒实际打掉的血、毒不毒得死），刀刃陷阱与毒性爆发的候选 4–7，出牌附带的紧勒、群蛇形态算进候选 4–7、余像算进候选 8，
# 战斗计数子类型 2（本回合弃牌数、本场抽牌数）。故障机器人仍为 19、储君仍为 20
# 版本 22（铁甲战士、亡灵契约师，2026-11-08，铁甲与亡灵契约师观测调研.md）：只有这两个角色的帧是这个版本。布局与 21 相同，内容是格式 21 再加：
# 奥斯提替玩家挨打算进回合结束预演（奥斯提记录 16–17 为它预计掉的血与剩下的生命）、奥斯提的能力在我方区域、奥斯提出手的伤害预览、召唤与出牌自伤进候选 10、
# 灾厄记录 12–13、末日降临的处决、狂怒 / 无惧疼痛 / 骨笛的格挡与卡戎之灰 / 势不可当 / 狱火的伤害进候选 4–8、荆棘与火焰屏障的反伤、
# 虚无牌回合末消耗的无惧疼痛与卡戎之灰、战斗计数子类型 3。故障机器人 19、储君 20、静默猎手 21 不变
# 版本 23（静默猎手第二版，2026-11-10，猎人观测调研.md 第七节）：由格式 21 分出（不含格式 22 的其余内容），布局与 21 相同，只改数值：
# 候选 10–11 算上这一步给敌人上的虚弱与减力量、让敌人涨的伤害（实验体的激怒、感染棱柱的感染）、加毒与打伤之后毒得死的敌人，
# 格式 22 的荆棘反伤与凋萎，回合结束预演判断毒得死改用含滑溜等修正的毒伤。2026-11-10 起静默猎手默认就是这个格式（猎人决策记录第 9 条），
# 与引擎 Observation.FormatFor 一起改的；reset 带 "obs_format": 21 仍可要格式 21
# 版本 24（Fool，游戏 v0.113.0 的新角色，2026-11-12）：格式 23 的全部内容加格式 22 的灾厄 12–13，再加 Fool 专属：战斗计数子类型 4（箭矢、旋律、
# 本场生成的悲伤）、扎穿能力记录 12–13（扎着几支箭、全部拔出还要打几段）、没有可用的箭时射击牌伤害预览为 0、钉住对没被扎穿的目标为 0、
# 弹跳下回合回手。布局与 21–23 相同，只有 Fool 的帧是这个版本
# 版本 25（Fool 第二版，2026-11-13，游戏更新v0.113.0/观测独立核查.md）：格式 24 的全部内容，再加出牌候选 4–8 的 Fool 附带伤害与附带格挡（爆炸箭矢、散雨溅射、
# 暴力和弦、大树将倾、审判；歌之道、摇篮曲、万事无忧、合股线；啾 四肢、啾 坚定的多次格挡），重复射击的 X 段，Fx23 加刻薄、蹒跚射击、钉住、女皇，
# 回合结束预演算傻瓜的回合末自动出牌、瓦皮替玩家挨打、背包里射击牌受可用箭数限制，我方宠物（瓦皮）的能力在我方区域、不输出招式。
# 布局与 24 相同。2026-11-13 起 Fool 默认就是这个格式（与引擎 Observation.FormatFor 一起改的）；reset 带 "obs_format": 24 仍可要格式 24
# 版本 26（Fool 第三版，2026-11-13 下午，游戏更新v0.113.0/观测独立核查.md 第十二节）：格式 25 的全部内容，候选特征从 12 项加到 14 项：
# 12 这一步当场得到的能量，13 这一步之后进手的牌数（抽牌、回手）。只有 Fool 的帧是这个版本；别的版本的帧解析时补 0。
# 2026-11-13 下午起 Fool 默认就是这个格式；reset 带 "obs_format": 25 仍可要格式 25
# 版本 27（Fool 第四版，2026-11-14，观测格式.md 四之十二）：格式 26 的全部内容，每条记录的特征从 20 项加到 22 项：手牌记录 20 标黄、21 标红
# （游戏自己给牌算的发光判断 CardModel.ShouldGlowGold / ShouldGlowRed），别的记录这两项为 0；别的版本的帧解析时补 0。
# 2026-11-14 起 Fool 默认就是这个格式；别的角色 reset 带 "obs_format": 27 也可以要（恐惧线的故障机器人）；reset 带 "obs_format": 26 仍可要格式 26
# 版本 28（铁甲战士第二版，2026-11-17，铁甲战士决策记录第 1 条）：格式 27 的全部内容（候选 14 项、记录 22 项特征）再加格式 22 的铁甲战士、亡灵契约师内容
# （出牌自伤与召唤进候选 10，狂怒、无惧疼痛、卡戎之灰、势不可当、狱火等附带效果，虚无回合末消耗，奥斯提，战斗计数子类型 3）。布局同 27。
# 2026-11-17 起铁甲战士默认就是这个格式，亡灵契约师同日稍后也改为 28（亡灵契约师决策记录第 2 条）；reset 带 "obs_format": 22、27 仍可要旧格式
# 版本 29（六个角色统一，2026-11-20，研究报告/2026-11-20_全角色观测缺口调研/R3-格式29规格.md，铁甲战士决策记录第 27 条）：格式 28 的全部内容加本次修正；
# 每条记录的特征 22 → 30 项（新列 22–29 按记录类型各自编号），每个候选的特征 14 → 20 项（新列 14–19 战斗内外各自编号），帧头不变
COMPAT_VERSIONS = (12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29)
FORMAT_REGENT = 20
FORMAT_SILENT = 21
FORMAT_IRON_NECRO = 22
FORMAT_SILENT2 = 23
FORMAT_FOOL = 24
FORMAT_FOOL2 = 25
FORMAT_FOOL3 = 26
FORMAT_FOOL4 = 27
FORMAT_IRON2 = 28
FORMAT_ALL = 29
FORMAT_BY_CHARACTER = {"REGENT": FORMAT_REGENT, "SILENT": FORMAT_SILENT2, "IRONCLAD": FORMAT_ALL, "NECROBINDER": FORMAT_IRON2,
                       "FOOL": FORMAT_FOOL4}


def format_for(character: str) -> int:
    """这个角色的帧是哪个观测格式（与引擎 Observation.FormatFor 的按角色查表一致）：储君 20，静默猎手 23（2026-11-10 前为 21），铁甲战士 29（2026-11-20 前为 28，2026-11-17 前为 22），亡灵契约师 28（2026-11-17 前为 22），Fool 27（2026-11-14 前为 26，再之前 25、24），其余 19。"""
    return FORMAT_BY_CHARACTER.get(character, FORMAT_VERSION)
HEADER_INTS = 34
HEADER_INTS_V18 = 32     # 版本 18 的头部长度（版本 19 加了 32、33：留到下回合的手牌张数、下回合实际能进手的张数，决策记录 227）
HEADER_INTS_V17 = 30     # 版本 17 的头部长度（版本 18 加了 30、31：还没领的卡牌奖励个数、下回合名义抽牌张数，决策记录 210）
HEADER_INTS_V16 = 28     # 版本 16 的头部长度（版本 17 加了 28、29：商店删牌次数、下一次删牌价格，决策记录 209）
HEADER_INTS_V15 = 26     # 版本 15 的头部长度（版本 16 加了 26、27：出牌途中能否抽牌、手牌空位，决策记录 204）
HEADER_INTS_V14 = 24     # 版本 14 的头部长度（版本 15 加了 24、25：本幕已打赢、还要打的 Boss 数）
HEADER_INTS_V13 = 18     # 版本 13 及以前的头部长度；解析时补 0 到 HEADER_INTS
TOK_INTS, TOK_FEATS, TOK_PRIV = 5, 30, 2
TOK_FEATS_V28 = 22      # 版本 27、28 每条记录 22 个特征（版本 29 加了 22–29）
TOK_FEATS_V26 = 20      # 版本 26 及以前每条记录 20 个特征（版本 27 加了 20、21：手牌的标黄、标红）


def tok_feats_n(ver: int) -> int:
    """这个版本的帧里每条记录有几个特征（解析原始字节的工具用；parse 之后一律补到 TOK_FEATS）。"""
    return TOK_FEATS if ver >= 29 else TOK_FEATS_V28 if ver >= 27 else TOK_FEATS_V26
CAND_INTS, CAND_FEATS = 5, 20
CAND_FEATS_V28 = 14     # 版本 26–28 每个候选 14 个特征（版本 29 加了 14–19）
CAND_FEATS_V25 = 12     # 版本 19–25 每个候选 12 个特征（版本 26 加了 12、13：这一步当场得到的能量、之后进手的牌数）
CAND_FEATS_V18 = 8      # 版本 18 及以前每个候选 8 个特征（版本 19 加了 8–11：当场格挡、冰霜被动变化、做完这一步结束回合的预计掉血、剩余生命）


class DecisionKind(IntEnum):
    STUCK = 0
    COMBAT = 1
    CARD_SELECT = 2
    CARD_REWARD = 3
    BUNDLE = 4
    REWARDS = 5
    EVENT = 6
    REST_SITE = 7
    SHOP = 8
    TREASURE = 9
    MINIGAME = 10
    MAP = 11
    GAME_OVER = 12


class ActionKind(IntEnum):
    NONE = 0
    END_TURN = 1
    PLAY_CARD = 2
    USE_POTION = 3
    DISCARD_POTION = 4
    SELECT_CARD = 5
    CONFIRM_SELECTION = 6
    PICK_CARD_REWARD = 7
    CARD_REWARD_ALT = 8
    PICK_BUNDLE = 9
    TAKE_REWARD = 10
    LEAVE = 11
    EVENT_OPTION = 12
    REST_OPTION = 13
    BUY = 14
    MAP_MOVE = 15
    PICK_RELIC = 16
    SKIP_RELIC = 17
    MINIGAME_CELL = 18


class TokenKind(IntEnum):
    PAD = 0
    GLOBAL = 1
    CREATURE = 2
    POWER = 3
    CARD = 4
    RELIC = 5
    POTION = 6
    ORB = 7
    MAP_NODE = 8
    OPTION = 9
    MOVE = 10       # 怪物招式预告（格式版本 3 起），所属记录为怪物
    SPHERE_CELL = 11  # Crystal Sphere 已揭开的格子（格式版本 4 起），只在小游戏决策里出现
    SELECT_PROMPT = 12  # 选牌说明（格式版本 5 起），只在选牌决策里出现
    SELECTED_CARD = 13  # 选牌界面里已选中的牌（格式版本 5 起）
    ROUTE_SUMMARY = 14  # 路线摘要（格式版本 7 起）：地图决策时每个现在可前往的节点一条，reset 带 route_summary 时才有
    COMBAT_STAT = 15    # 战斗计数（格式版本 11 起）：玩家的本回合、本场计数，多形态怪物的形态；所属记录为对应生物
    ENCOUNTER_POOL = 16  # 出怪池（格式版本 15 起）：本幕各池的每种遭遇，打过几次、还在不在袋里、下一场是它的概率；只加到全局记录上
    FUTURE_CONTENT = 17  # 整局预生成内容（决策记录 259，格式版本不变）：reset 带 privileged 与 future_content 时，整局模式战斗外的帧才有；特权，只进价值头
    ORACLE = 20     # 预知记录（决策记录 280，格式版本不变）：reset 带 privileged 与 oracle 时，整局模式战斗外的帧才有——近期卡牌奖励、药水、精英遗物、遗物袋、问号房的推算结果；特权，只进价值头


class Zone(IntEnum):
    NONE = 0
    ALLY = 1
    ENEMY = 2
    HAND = 3
    DRAW = 4
    DISCARD = 5
    EXHAUST = 6
    DECK = 7
    OFFER = 8
    PLAY = 9
    PACK1 = 10      # 卡包界面第 1 包的牌（格式版本 6 起）
    PACK2 = 11      # 卡包界面第 2 包的牌
    UPGRADE_PREVIEW = 12    # 升级类选牌里某张牌升级后的样子（格式版本 9 起；候选的对象 B）
    REWARD_GROUP1 = 13      # 这一组奖励里第 1 个卡牌奖励的选项记录与它的牌（格式版本 18 起）
    REWARD_GROUP2 = 14      # 第 2 个
    REWARD_GROUP3 = 15      # 第 3 个及以后
    DRAW_TOP1 = 16          # 格式 20：玩家知道位置的抽牌堆第 1 张（被放到顶部、还没抽走或洗掉；不进抽牌堆的汇总，作为核心记录）
    DRAW_TOP2 = 17          # 第 2 张
    DRAW_TOP3 = 18          # 第 3 张及以后


@dataclass
class Obs:
    """一个决策点的观测。tok_* 为实体记录，cand_* 为候选；tok_priv 是隐藏信息，只能给价值网络用。"""
    header: np.ndarray            # int32[18]
    tok_ints: np.ndarray          # int32[n, 5]：类型、词表 ID、区域、所属记录、附加 ID
    tok_feats: np.ndarray         # float32[n, TOK_FEATS]（版本 27 以前的帧 20–21 补 0）
    tok_priv: np.ndarray          # float32[n, 2]
    cand_ints: np.ndarray         # int32[m, 5]：动作类型、记录 A、记录 B、词表 ID、保留
    cand_feats: np.ndarray        # float32[m, 4]
    labels: list[str] = field(default_factory=list)
    extra: dict | None = None     # 整局 reset 带 snapshots 时：选路线帧有 "snapshot"（玩家快照 JSON 文本），战斗开始帧有 "combat"

    @property
    def decision(self) -> DecisionKind: return DecisionKind(int(self.header[1]))
    @property
    def num_actions(self) -> int: return int(self.header[3])
    @property
    def done(self) -> bool: return bool(self.header[4])
    @property
    def victory(self) -> bool: return bool(self.header[5])
    @property
    def act(self) -> int: return int(self.header[6])
    @property
    def floor(self) -> int: return int(self.header[7])
    @property
    def hp(self) -> int: return int(self.header[8])
    @property
    def max_hp(self) -> int: return int(self.header[9])
    @property
    def gold(self) -> int: return int(self.header[10])
    @property
    def in_combat(self) -> bool: return bool(self.header[11])
    @property
    def combats_won(self) -> int: return int(self.header[12])
    @property
    def steps(self) -> int: return int(self.header[13])
    @property
    def stuck(self) -> bool: return bool(self.header[15])
    @property
    def bosses(self) -> tuple[int, int]:
        """本幕 Boss 与第二个 Boss 的词表 ID（没有为 0）；战斗模式只在打 Boss 时有。"""
        return int(self.header[16]), int(self.header[17])


def parse(buf: bytes) -> Obs:
    ver = int(np.frombuffer(buf, dtype=np.int32, count=1)[0])
    if ver not in COMPAT_VERSIONS:
        raise ValueError(f"观测格式版本不符：引擎 {ver}，Python {FORMAT_VERSION}")
    nh = HEADER_INTS if ver >= 19 else HEADER_INTS_V18 if ver == 18 else HEADER_INTS_V17 if ver == 17 else HEADER_INTS_V16 if ver == 16 else HEADER_INTS_V15 if ver == 15 else HEADER_INTS_V14 if ver == 14 else HEADER_INTS_V13
    a = np.zeros(HEADER_INTS, np.int32); a[:nh] = np.frombuffer(buf, dtype=np.int32, count=nh)
    n, m = int(a[2]), int(a[3])
    off = nh * 4

    def take(dtype, count, shape):
        nonlocal off
        arr = np.frombuffer(buf, dtype=dtype, count=count, offset=off).reshape(shape)
        off += count * 4
        return arr

    tok_ints = take(np.int32, n * TOK_INTS, (n, TOK_INTS))
    ntf = tok_feats_n(ver)
    tok_feats = take(np.float32, n * ntf, (n, ntf))
    if ntf < TOK_FEATS:         # 旧版本的帧：新加的记录特征补 0
        tok_feats = np.pad(tok_feats, ((0, 0), (0, TOK_FEATS - ntf)))
    tok_priv = take(np.float32, n * TOK_PRIV, (n, TOK_PRIV))
    cand_ints = take(np.int32, m * CAND_INTS, (m, CAND_INTS))
    ncf = CAND_FEATS if ver >= 29 else CAND_FEATS_V28 if ver >= 26 else CAND_FEATS_V25 if ver >= 19 else CAND_FEATS_V18
    cand_feats = take(np.float32, m * ncf, (m, ncf))
    if ncf < CAND_FEATS:        # 旧版本的帧：新加的候选特征补 0
        cand_feats = np.pad(cand_feats, ((0, 0), (0, CAND_FEATS - ncf)))
    label_len = int(np.frombuffer(buf, dtype=np.int32, count=1, offset=off)[0])
    off += 4
    labels = json.loads(buf[off:off + label_len].decode("utf-8")) if label_len else []
    off += label_len
    extra = None
    if len(buf) >= off + 4:
        extra_len = int(np.frombuffer(buf, dtype=np.int32, count=1, offset=off)[0])
        extra = json.loads(buf[off + 4:off + 4 + extra_len].decode("utf-8"))
    return Obs(a, tok_ints, tok_feats, tok_priv, cand_ints, cand_feats, labels, extra)
