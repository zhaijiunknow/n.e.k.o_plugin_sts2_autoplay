"""把一批变长观测整理成网络输入。

注意力只作用于“核心记录”：全局、生物、手牌、出牌区、供选择区、充能球、药水、地图节点、选项。
抽牌堆、弃牌堆、消耗堆、牌组、已有遗物按区域汇总；能力加到所属生物上；招式预告经单独的映射加到所属怪物上。
候选引用的记录若不在核心里（如从弃牌堆选牌），用它自身的嵌入。

记录和候选都不填充，整批首尾相接成一维；只有进注意力的核心记录按批填充为 [B, K]。
整局观测的牌组、牌堆很长，按最长一条填充会让嵌入张量大几倍，显存和算力大半耗在填充上。
汇总、能力归属、特权汇总所需的下标和权重都在这里用 numpy 预先算好。
注意力按核心记录数分组、各组只填充到组内最长：地图决策有六七十个核心记录（地图节点），其余多在十个左右，
整批按最长填充会让注意力的计算量多出好几倍。
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass

import numpy as np

from sts2env.protocol import CAND_FEATS, CAND_INTS, HEADER_INTS, DecisionKind, Obs, TokenKind, Zone

# 汇总槽：0 抽牌堆，1 弃牌堆，2 消耗堆，3 牌组，4 已有遗物，5 能力（不进汇总，加到所属生物上）
N_POOLS = 5
POOL_OF_ZONE = {Zone.DRAW: 0, Zone.DISCARD: 1, Zone.EXHAUST: 2, Zone.DECK: 3}


# 局外帧扩展注意力（决策记录 262，ModelConfig.macro_attend）：局外决策帧里牌组、已有遗物、本幕出怪池、商店里买不起的商品也作为核心记录进注意力；
# 任何帧里被候选直接引用的记录（如战斗里从弃牌堆、抽牌堆选牌）也进注意力
# （原来的汇总槽、加到全局记录的通路照旧保留），另在每个局外帧末尾合成本幕 Boss 记录（类型 BOSS_REF，词表 ID 为 Boss 遭遇，特征 0 为第几个 Boss）。
# ATTEND 为 collate_ragged 的默认值，由 PolicyValueNet 建网时按配置设好；组出的批次记下实际用的值，网络前向时核对，对不上就报错
ATTEND = False
BOSS_REF = 18


# 牌组打分用的局面视图（deck_view）的决策类型：不对应引擎的任何决策，网络用它区分“给局面打分”和“做决策”
DECK_VIEW = 13


def deck_view(o: Obs) -> Obs:
    """牌组打分的输入：只留玩家自己的状态（全局、角色、牌组、遗物、药水），去掉地图、选项、供选择的牌和候选。
    同一个局面不论停在选路线、选牌还是商店，打分的输入都一样；只在战斗之外使用。"""
    k, z = o.tok_ints[:, 0], o.tok_ints[:, 2]
    keep = ((k == TokenKind.GLOBAL) | ((k == TokenKind.CREATURE) & (z == Zone.ALLY)) | ((k == TokenKind.CARD) & (z == Zone.DECK))
            | (((k == TokenKind.RELIC) | (k == TokenKind.POTION)) & (z == Zone.ALLY)))
    ti, tf = o.tok_ints[keep].copy(), o.tok_feats[keep].copy()
    ti[:, 3] = -1
    tf[ti[:, 0] == TokenKind.GLOBAL, 14:22] = 0         # 当前房间类型、已选数量随决策而变，与局面无关（格式 29 的新列 22–29 不在其内）
    h = o.header.copy(); h[1] = DECK_VIEW; h[2] = int(keep.sum()); h[3] = 0
    return Obs(h, ti, tf, o.tok_priv[keep].copy(), np.zeros((0, CAND_INTS), np.int32), np.zeros((0, CAND_FEATS), np.float32))


def token_role(kind: np.ndarray, zone: np.ndarray) -> np.ndarray:
    """每条记录的角色：-5 整局预生成内容（特权，只进价值头），-4 出怪池（加到全局记录，不进注意力），-3 招式预告（加到所属怪物），
    -2 能力与战斗计数（加到所属生物），0..4 汇总槽，-1 核心。"""
    role = np.full(kind.shape, -1, dtype=np.int64)
    card = kind == TokenKind.CARD
    for z, slot in POOL_OF_ZONE.items():
        role[card & (zone == z)] = slot
    role[(kind == TokenKind.RELIC) & (zone == Zone.ALLY)] = 4
    role[(kind == TokenKind.POWER) | (kind == TokenKind.COMBAT_STAT)] = -2      # 战斗计数（版本 11）与能力一样加到所属生物
    role[kind == TokenKind.MOVE] = -3
    role[kind == TokenKind.ENCOUNTER_POOL] = -4
    role[kind == TokenKind.FUTURE_CONTENT] = -5
    role[kind == TokenKind.ORACLE] = -6         # 预知记录（决策记录 280），同样只进价值头
    return role


def fut_code(tf: np.ndarray) -> np.ndarray:
    """整局预生成内容记录（类型 17）的位置编码：特征 0 第几幕之后（0–2）、1 类别（0–4）、2 本类里的先后（0–7）、3 是否弱池位，
    合成 0 .. FUT_CODES−1 的一个编号（model.FUT_CODES = 3 × 5 × 8 × 2）。特征是原值（未压缩）。"""
    off = np.clip(np.rint(tf[:, 0]), 0, 2).astype(np.int64)
    cat = np.clip(np.rint(tf[:, 1]), 0, 4).astype(np.int64)
    order = np.clip(np.rint(tf[:, 2]), 0, 7).astype(np.int64)
    weak = (tf[:, 3] > 0.5).astype(np.int64)
    return ((off * 5 + cat) * 8 + order) * 2 + weak


def _pad_cf(cf: np.ndarray) -> np.ndarray:
    """候选特征补齐到 CAND_FEATS 列：版本 18 及以前的帧（旧采集端、留存的观测）每个候选 8 个特征，版本 19 起 12 个（决策记录 227）。"""
    return cf if cf.shape[1] >= CAND_FEATS else np.pad(cf, ((0, 0), (0, CAND_FEATS - cf.shape[1])))


def squash(x: np.ndarray) -> np.ndarray:
    """数值特征统一做符号对数压缩，避免生命值、伤害等大数值主导。"""
    # 先截到 ±1e4（R2-编码第五节：瀑布巨兽倒地时生命 999,999,999，压缩后约 20.7，正常最大约 7；只影响这类离谱的值）
    return np.sign(x) * np.log1p(np.minimum(np.abs(x), 1e4))


@dataclass
class Batch:
    """B 为步数，S 为整批记录总数，C 为候选总数，K 为最大核心数，M 为最大候选数。"""
    tok_ints: np.ndarray      # [S, 5]
    tok_feats: np.ndarray     # [S, 16]（已压缩）
    pw_src: np.ndarray        # [P] 能力记录（整批下标）
    pw_dst: np.ndarray        # [P] 其所属生物（整批下标）
    mv_src: np.ndarray        # [V] 招式预告记录（整批下标）
    mv_dst: np.ndarray        # [V] 其所属怪物（整批下标）
    pool_tok: np.ndarray      # [Q] 进汇总槽的记录
    pool_dst: np.ndarray      # [Q] 汇总槽（步 × N_POOLS + 槽）
    pool_cnt: np.ndarray      # [B, N_POOLS] 各槽记录数
    core_idx: np.ndarray      # [B, K] 核心记录（整批下标，填充为 0）
    core_mask: np.ndarray     # [B, K]
    cand_ints: np.ndarray     # [C, 5]
    cand_feats: np.ndarray    # [C, 4]
    cand_step: np.ndarray     # [C] 所属步
    cand_slot: np.ndarray     # [C] 在本步候选中的序号
    cand_a: np.ndarray        # [C] 记录 A 在“上下文表示 [S] + 记录嵌入 [S]”拼接表中的位置（核心记录引用前者）；无引用为 -1
    cand_b: np.ndarray        # [C]
    has_cand: np.ndarray      # [B] 本步有候选
    draw_tok: np.ndarray      # [D] 抽牌堆里的记录（特权汇总用）
    draw_step: np.ndarray     # [D]
    draw_w: np.ndarray        # [D] 按真实位置的权重，顶部大
    draw_wsum: np.ndarray     # [B]
    decision: np.ndarray      # [B]
    mode: np.ndarray          # [B] 0 战斗课程，1 整局
    macro: np.ndarray         # [B] 整局里的局外决策（选路线、抓牌、事件、商店、休息处），用单独的策略头和价值头
    glob_tok: np.ndarray      # [G] 全局记录（整批下标）
    glob_boss: np.ndarray     # [G, 2] 其所在步的本幕 Boss、第二个 Boss（词表 ID，没有为 0）
    width: int                # M
    groups: list              # 注意力分组：[(步下标, 组内最大核心数), ...]
    # 版本 11 特权（只进价值头）：全局记录上预抽的下一场普通战、精英战，推演出的怪物下一招（招式记录特权特征 0 为 1、1 为 1）
    # 版本 14：全局记录所在步的本幕遭遇历史（头部 18–22）：普通战、精英战场数与还剩几场弱池普通战（已压缩），最近一场普通战、精英战（词表 ID）
    glob_hist: np.ndarray = None      # [G, 3]
    glob_last: np.ndarray = None      # [G, 2]
    # 版本 15：本幕已经打赢的 Boss 数、还要打的 Boss 数（头部 24、25；战斗模式的 Boss 练习按这次实际要打的场数）
    glob_stage: np.ndarray = None     # [G, 2]
    # 版本 16：出牌途中能否抽牌（头部 26）、手牌空位 / 10（头部 27）；更早的帧按能抽牌、空位按手牌张数算（决策记录 204）
    glob_draw: np.ndarray = None      # [G, 2]
    glob_shop: np.ndarray = None      # [G, 2] 版本 17：商店删牌次数 / 5、下一次删牌价格 / 300
    glob_v18: np.ndarray = None       # [G, 2] 版本 18：还没领的卡牌奖励个数 / 3、下回合名义抽牌张数 / 10
    glob_v19: np.ndarray = None       # [G, 2] 版本 19：留到下回合的手牌张数 / 10、下回合实际能进手的张数 / 10（决策记录 227）
    # 版本 16：遗物交换商换回的遗物（选项记录特征 15 的词表 ID；特征会被 squash，这里取原值另传，决策记录 205）
    gain_tok: np.ndarray = None       # [Q] 选项记录（整批下标）
    gain_id: np.ndarray = None        # [Q]
    # 版本 15：出怪池记录（整批下标）与其所在步的全局记录（整批下标）
    enc_src: np.ndarray = None        # [E]
    enc_dst: np.ndarray = None        # [E]
    priv_enc: np.ndarray = None       # [G, 2] 遭遇词表 ID
    priv_enc_step: np.ndarray = None  # [G]
    roll_tok: np.ndarray = None       # [R] 招式记录（整批下标）
    roll_step: np.ndarray = None      # [R]
    # 整局预生成内容（决策记录 259，特权，只进价值头）：类型 17 的记录（整批下标）、所在步、位置编码、按步平均的权重（1 / 这一步的记录数）
    fut_tok: np.ndarray = None        # [F]
    fut_step: np.ndarray = None       # [F]
    fut_code: np.ndarray = None       # [F]
    fut_w: np.ndarray = None          # [F]
    # 预知记录（决策记录 280，特权，只进价值头）：类型 20 的记录（整批下标）、所在步、类别 × 先后的编号、子类、按步平均的权重
    orc_tok: np.ndarray = None        # [O]
    orc_step: np.ndarray = None       # [O]
    orc_slot: np.ndarray = None       # [O]
    orc_sub: np.ndarray = None        # [O]
    orc_w: np.ndarray = None          # [O]
    attend: bool = False              # 组批时是否按局外帧扩展注意力（决策记录 262），网络前向时与 ModelConfig.macro_attend 核对

    def to(self, device):
        import torch
        out = {}
        for k, v in self.__dict__.items():
            if isinstance(v, np.ndarray):
                out[k] = torch.from_numpy(v).to(device, non_blocking=True)
            elif k == "groups":
                out[k] = [(torch.from_numpy(i).to(device, non_blocking=True), kg) for i, kg in v]
            else:
                out[k] = v
        return out


@dataclass
class Ragged:
    """一组观测的拼接形式：各段数组首尾相接，用偏移量分界。可直接跨进程传输，组批时全向量化。"""
    header: np.ndarray        # [N, 24] int32（版本 13 及以前的帧解析时补 0）
    tok_ints: np.ndarray      # [ΣT, 5]
    tok_feats: np.ndarray     # [ΣT, 16]
    tok_priv: np.ndarray      # [ΣT, 2]
    tok_off: np.ndarray       # [N+1]
    cand_ints: np.ndarray     # [ΣM, 5]
    cand_feats: np.ndarray    # [ΣM, 4]
    cand_off: np.ndarray      # [N+1]
    mode: np.ndarray | None = None   # [N] 对局模式：0 战斗课程，1 整局（由采集进程填写）

    def __len__(self) -> int:
        return self.header.shape[0]

    @staticmethod
    def from_obs(obs_list: list[Obs], mode: np.ndarray | None = None) -> "Ragged":
        tl = np.array([0] + [o.tok_ints.shape[0] for o in obs_list]); cl = np.array([0] + [o.cand_ints.shape[0] for o in obs_list])
        r = Ragged(np.stack([o.header for o in obs_list]),
                      np.concatenate([o.tok_ints for o in obs_list]), np.concatenate([o.tok_feats for o in obs_list]),
                      np.concatenate([o.tok_priv for o in obs_list]), np.cumsum(tl),
                      np.concatenate([o.cand_ints for o in obs_list]), np.concatenate([_pad_cf(o.cand_feats) for o in obs_list]), np.cumsum(cl))
        r.mode = np.zeros(len(obs_list), np.int64) if mode is None else np.asarray(mode, np.int64)
        return r

    def modes(self) -> np.ndarray:
        return self.mode if self.mode is not None else np.zeros(len(self), np.int64)

    @staticmethod
    def concat(parts: list["Ragged"]) -> "Ragged":
        def offs(key):
            out, base = [np.zeros(1, np.int64)], 0
            for p in parts:
                o = getattr(p, key)
                out.append(o[1:] + base); base += o[-1]
            return np.concatenate(out)
        return Ragged(np.concatenate([_pad_header(p.header) for p in parts]),
                      np.concatenate([p.tok_ints for p in parts]), np.concatenate([p.tok_feats for p in parts]),
                      np.concatenate([p.tok_priv for p in parts]), offs("tok_off"),
                      np.concatenate([p.cand_ints for p in parts]), np.concatenate([_pad_cf(p.cand_feats) for p in parts]), offs("cand_off"),
                      np.concatenate([p.modes() for p in parts]))

    def drop_kind(self, kind: int) -> "Ragged":
        """去掉某一类记录（如类型 17 的整局预生成内容），头部记录数与偏移量随之改。只用于每步排在最后输出的类型（引擎最后才写它们），
        这样其余记录在本步内的下标不变，候选与所属记录的引用照常有效。"""
        keep = self.tok_ints[:, 0] != kind
        if keep.all():
            return self
        step = np.repeat(np.arange(len(self)), np.diff(self.tok_off))
        n = np.bincount(step[keep], minlength=len(self))
        h = self.header.copy(); h[:, 2] = n
        return Ragged(h, self.tok_ints[keep], self.tok_feats[keep], self.tok_priv[keep], np.concatenate([[0], np.cumsum(n)]),
                      self.cand_ints, self.cand_feats, self.cand_off, self.modes())

    def select(self, idx: np.ndarray) -> "Ragged":
        """取第 idx 步（任意顺序），返回新的拼接形式。"""
        def take(arr, off):
            lens = off[idx + 1] - off[idx]
            starts = np.repeat(off[idx], lens)
            within = np.arange(lens.sum()) - np.repeat(np.cumsum(lens) - lens, lens)
            return arr[starts + within], np.concatenate([[0], np.cumsum(lens)])
        ti, to = take(self.tok_ints, self.tok_off)
        tf, _ = take(self.tok_feats, self.tok_off)
        tp, _ = take(self.tok_priv, self.tok_off)
        ci, co = take(self.cand_ints, self.cand_off)
        cf, _ = take(self.cand_feats, self.cand_off)
        return Ragged(self.header[idx], ti, tf, tp, to, ci, cf, co, self.modes()[idx])


CORE_BUCKETS = (12, 24, 48)     # 注意力分组的核心数上界；最后一组不设上界
MIN_GROUPED = 256               # 小批（采集进程推理）不分组：多几次小算子调用反而更慢


def _pad_header(h: np.ndarray) -> np.ndarray:
    """旧采集端送来的头部较短（版本 13 及以前 18 列，版本 14 为 24 列）：补 0 到 HEADER_INTS，新加的本幕遭遇历史、Boss 场数按没有算。"""
    return h if h.shape[1] >= HEADER_INTS else np.pad(h, ((0, 0), (0, HEADER_INTS - h.shape[1])))


def _add_boss_refs(r: Ragged) -> Ragged:
    """局外帧末尾补本幕 Boss 记录（第二个 Boss 有就再补一条）：放在本步记录的最后，其余记录在本步内的下标不变，候选与所属记录的引用照常有效。"""
    N = len(r)
    mac = macro_rows(r.header, r.modes())
    ids = r.header[:, 16:18].astype(np.int64) * mac[:, None]
    add = (ids > 0).sum(1)
    if not add.any():
        return r
    n_old = np.diff(r.tok_off); n_new = n_old + add
    off = np.concatenate([[0], np.cumsum(n_new)])
    S = int(off[-1])
    ti = np.zeros((S, r.tok_ints.shape[1]), r.tok_ints.dtype); tf = np.zeros((S, r.tok_feats.shape[1]), r.tok_feats.dtype)
    tp = np.zeros((S, r.tok_priv.shape[1]), r.tok_priv.dtype)
    step_o = np.repeat(np.arange(N), n_old)
    dst = off[step_o] + (np.arange(len(step_o)) - r.tok_off[step_o])
    ti[dst] = r.tok_ints; tf[dst] = r.tok_feats; tp[dst] = r.tok_priv
    for j in (0, 1):
        st = np.flatnonzero(ids[:, j] > 0)
        pos = off[st] + n_old[st] + (ids[st, :j] > 0).sum(1)
        ti[pos, 0] = BOSS_REF; ti[pos, 1] = ids[st, j]; ti[pos, 2] = Zone.NONE; ti[pos, 3] = -1; tf[pos, 0] = j
    h = r.header.copy(); h[:, 2] = n_new
    return Ragged(h, ti, tf, tp, off, r.cand_ints, r.cand_feats, r.cand_off, r.modes())


def collate_ragged(r: Ragged, attend: bool | None = None) -> Batch:
    """全向量化的组批（无逐条 Python 循环）。attend：局外帧扩展注意力（决策记录 262），None 时用 ATTEND（建网时按配置设好）。"""
    if r.header.shape[1] < HEADER_INTS:
        r = dataclasses.replace(r, header=_pad_header(r.header))
    attend = ATTEND if attend is None else attend
    if attend:
        r = _add_boss_refs(r)
    N = len(r)
    n_tok = np.diff(r.tok_off); n_cand = np.diff(r.cand_off)
    S, M = len(r.tok_ints), max(1, int(n_cand.max(initial=0)))
    step_t = np.repeat(np.arange(N), n_tok); loc_t = np.arange(S) - r.tok_off[step_t]
    role = token_role(r.tok_ints[:, 0], r.tok_ints[:, 2])
    is_core = role == -1
    if attend:      # 局外帧：牌组、已有遗物、出怪池、买不起的商品（能力类、特征 17 为 1）、补上的 Boss 记录也进注意力，原来的去处照旧
        k, z = r.tok_ints[:, 0], r.tok_ints[:, 2]
        is_core = is_core | (macro_rows(r.header, r.modes())[step_t] & (((k == TokenKind.CARD) & (z == Zone.DECK)) | ((k == TokenKind.RELIC) & (z == Zone.ALLY))
                                                                        | (k == TokenKind.ENCOUNTER_POOL) | ((k == TokenKind.POWER) & (r.tok_feats[:, 17] > 0))
                                                                        | (k == BOSS_REF)))
        # 被候选直接引用的记录都进注意力（战斗里从弃牌堆、抽牌堆、消耗堆选牌时，候选原来只能用牌自身的嵌入、没有上下文）
        sc = np.repeat(np.arange(N), np.diff(r.cand_off))
        for col in (1, 2):
            a = r.cand_ints[:, col].astype(np.int64); ok = (a >= 0) & (a < np.diff(r.tok_off)[sc])
            is_core[r.tok_off[sc[ok]] + a[ok]] = True
    core_cum = np.cumsum(is_core)
    core_before = np.concatenate([[0], core_cum])[r.tok_off[:-1]]          # 每步之前的核心累计数
    core_rank = core_cum - 1 - core_before[step_t]
    n_core = np.bincount(step_t[is_core], minlength=N) if S else np.zeros(N, np.int64)
    K = max(1, int(n_core.max(initial=0)))
    core_idx = np.zeros((N, K), np.int64); core_mask = np.zeros((N, K), bool)
    core_idx[step_t[is_core], core_rank[is_core]] = np.flatnonzero(is_core); core_mask[step_t[is_core], core_rank[is_core]] = True
    # 能力 → 所属生物（owner 是本步内下标）
    owner = r.tok_ints[:, 3].astype(np.int64)
    pw = np.flatnonzero((role == -2) & (owner >= 0) & (owner < n_tok[step_t]))
    pw_dst = r.tok_off[step_t[pw]] + owner[pw]
    mv = np.flatnonzero((role == -3) & (owner >= 0) & (owner < n_tok[step_t]))
    mv_dst = r.tok_off[step_t[mv]] + owner[mv]
    # 汇总槽
    pool_tok = np.flatnonzero(role >= 0)
    pool_dst = step_t[pool_tok] * N_POOLS + role[pool_tok]
    pool_cnt = np.bincount(pool_dst, minlength=N * N_POOLS).reshape(N, N_POOLS).astype(np.float32)
    # 候选与引用：核心记录引用其上下文表示（位置 = 整批下标），其余引用记录自身嵌入（位置 S + 整批下标）
    glob = np.where(is_core, np.arange(S), S + np.arange(S))
    step_c = np.repeat(np.arange(N), n_cand); loc_c = np.arange(len(step_c)) - r.cand_off[step_c]
    def ref(col):
        a = r.cand_ints[:, col].astype(np.int64)
        g = np.clip(r.tok_off[step_c] + a, 0, max(S - 1, 0))
        return np.where(a >= 0, glob[g] if S else -1, -1)
    # 价值网络的特权汇总：抽牌堆按真实位置加权
    draw = np.flatnonzero(role == 0)
    draw_w = np.exp(-4.0 * r.tok_priv[draw, 0]).astype(np.float32)
    draw_wsum = np.maximum(np.bincount(step_t[draw], weights=draw_w, minlength=N), 1e-6).astype(np.float32)
    glob_tok = np.flatnonzero(r.tok_ints[:, 0] == TokenKind.GLOBAL)
    glob_of = np.full(N, -1, np.int64); glob_of[step_t[glob_tok]] = glob_tok
    enc = np.flatnonzero(role == -4); enc_dst = glob_of[step_t[enc]]; enc = enc[enc_dst >= 0]; enc_dst = enc_dst[enc_dst >= 0]
    roll = np.flatnonzero((r.tok_ints[:, 0] == TokenKind.MOVE) & (r.tok_priv[:, 1] > 0) & (r.tok_priv[:, 0] > 0.5))
    fut = np.flatnonzero(role == -5)
    fut_n = np.bincount(step_t[fut], minlength=N)
    orc = np.flatnonzero(role == -6)
    orc_n = np.bincount(step_t[orc], minlength=N)
    otf = r.tok_feats[orc]      # 原值（未压缩）：0 类别、1 先后、2 子类
    return Batch(r.tok_ints.astype(np.int64), squash(r.tok_feats).astype(np.float32), pw, pw_dst, mv, mv_dst, pool_tok, pool_dst, pool_cnt,
                 core_idx, core_mask, r.cand_ints.astype(np.int64), squash(r.cand_feats).astype(np.float32), step_c, loc_c,
                 ref(1), ref(2), n_cand > 0, draw, step_t[draw], draw_w, draw_wsum,
                 r.header[:, 1].astype(np.int64), r.modes().astype(np.int64), macro_rows(r.header, r.modes()),
                 glob_tok, r.header[step_t[glob_tok], 16:18].astype(np.int64), M, _groups(n_core, K),
                 glob_hist=squash(r.header[step_t[glob_tok], 18:21].astype(np.float32)),
                 glob_last=r.header[step_t[glob_tok], 21:23].astype(np.int64),
                 glob_stage=r.header[step_t[glob_tok], 24:26].astype(np.float32),
                 glob_draw=_draw_feats(r, step_t, glob_tok),
                 glob_shop=_shop_feats(r, step_t, glob_tok),
                 glob_v18=_v18_feats(r, step_t, glob_tok),
                 glob_v19=_v19_feats(r, step_t, glob_tok),
                 gain_tok=(gt := np.flatnonzero((r.tok_ints[:, 0] == TokenKind.OPTION) & (r.tok_feats[:, 15] > 0))),
                 gain_id=np.rint(r.tok_feats[gt, 15]).astype(np.int64),
                 enc_src=enc, enc_dst=enc_dst,
                 priv_enc=np.rint(r.tok_priv[glob_tok, :2]).astype(np.int64), priv_enc_step=step_t[glob_tok],
                 roll_tok=roll, roll_step=step_t[roll],
                 fut_tok=fut, fut_step=step_t[fut], fut_code=fut_code(r.tok_feats[fut]),
                 fut_w=(1.0 / np.maximum(fut_n[step_t[fut]], 1)).astype(np.float32),
                 orc_tok=orc, orc_step=step_t[orc],
                 orc_slot=(np.clip(np.rint(otf[:, 0]), 0, 11) * 4 + np.clip(np.rint(otf[:, 1]), 0, 3)).astype(np.int64),
                 orc_sub=np.clip(np.rint(otf[:, 2]), 0, 31).astype(np.int64),
                 orc_w=(1.0 / np.maximum(orc_n[step_t[orc]], 1)).astype(np.float32), attend=bool(attend))


def _draw_feats(r: Ragged, step_t: np.ndarray, glob_tok: np.ndarray) -> np.ndarray:
    """版本 16 的有效抽牌（决策记录 204）：[出牌途中能否抽牌, 手牌空位 / 10]，战斗外为 [1, 0]。
    版本 15 及以前的帧（实机桥接换新引擎之前、队列里的旧轨迹）没有这两项，按能抽牌、空位 = 10 − 手牌张数算，不当成禁止抽牌。"""
    h = r.header[step_t[glob_tok]]
    old = h[:, 0] < 16
    hand = (r.tok_ints[:, 0] == TokenKind.CARD) & (r.tok_ints[:, 2] == Zone.HAND)
    n_hand = np.bincount(step_t[hand], minlength=len(r))[step_t[glob_tok]]
    can = np.where(old, 1, h[:, 26]).astype(np.float32)
    space = np.where(old, np.maximum(0, 10 - n_hand), h[:, 27]) * (h[:, 11] > 0)
    return np.stack([can, space.astype(np.float32) / 10.0], 1)


def _shop_feats(r: Ragged, step_t: np.ndarray, glob_tok: np.ndarray) -> np.ndarray:
    """版本 17 的商店删牌（第十五份外部评审，决策记录 209）：[已删次数 / 5, 下一次删牌价格 / 300]；版本 16 及以前的帧没有，为 0（走清零的映射）。"""
    h = r.header[step_t[glob_tok]]
    new = (h[:, 0] >= 17)[:, None]
    return np.where(new, h[:, 28:30].astype(np.float32) / np.array([5.0, 300.0], np.float32), 0.0).astype(np.float32)


def _v18_feats(r: Ragged, step_t: np.ndarray, glob_tok: np.ndarray) -> np.ndarray:
    """版本 18（决策记录 210）：[还没领的卡牌奖励个数 / 3, 下回合名义抽牌张数 / 10]；版本 17 及以前的帧没有，为 0（走清零的映射）。"""
    h = r.header[step_t[glob_tok]]
    new = (h[:, 0] >= 18)[:, None]
    return np.where(new, h[:, 30:32].astype(np.float32) / np.array([3.0, 10.0], np.float32), 0.0).astype(np.float32)


def _v19_feats(r: Ragged, step_t: np.ndarray, glob_tok: np.ndarray) -> np.ndarray:
    """版本 19（决策记录 227）：[留到下回合的手牌张数 / 10, 下回合实际能进手的张数 / 10]；版本 18 及以前的帧没有，为 0（走清零的映射）。"""
    h = r.header[step_t[glob_tok]]
    new = (h[:, 0] >= 19)[:, None]
    return np.where(new, h[:, 32:34].astype(np.float32) / 10.0, 0.0).astype(np.float32)


def macro_rows(header: np.ndarray, modes: np.ndarray) -> np.ndarray:
    """整局里战斗之外的决策：不在战斗中，且不是战斗、结束、卡住这几类（与 actor.is_macro 一致）。"""
    return (modes == 1) & (header[:, 11] == 0) & ~np.isin(header[:, 1], [DecisionKind.COMBAT, DecisionKind.GAME_OVER, DecisionKind.STUCK])


def _groups(n_core: np.ndarray, K: int) -> list:
    if len(n_core) < MIN_GROUPED:
        return [(np.arange(len(n_core)), K)]
    bucket = np.searchsorted(np.array(CORE_BUCKETS), n_core)
    out = []
    for g in np.unique(bucket):
        idx = np.flatnonzero(bucket == g)
        out.append((idx, max(1, int(n_core[idx].max()))))
    return out


def collate(obs_list: list[Obs]) -> Batch:
    return collate_ragged(Ragged.from_obs(obs_list))
