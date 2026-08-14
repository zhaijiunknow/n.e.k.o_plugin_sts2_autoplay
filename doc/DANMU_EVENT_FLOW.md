# STS2 弹幕 — 快照 diff 事件流 详细说明

本文档说明 `danmu_events.py`（DanmuEventTracker）+ `danmu_spire.py`（match_events）
如何从「局面快照 diff」发布事件、再由事件触发 56 条 DanmakuSpire 弹幕规则，
以及每条规则**依赖哪些 tracker 状态、从什么数据源来、近似逻辑与误报局限**。

## 1. 架构总览

```
每次 tick（service.refresh_state）
  └─ DanmuEventTracker.feed(raw_state, snapshot)
       1. 提取特征（_extract）→ 与上一快照 diff（_diff）→ 发布事件 list[DanmuEvent]
       2. 更新跨快照 run 状态（已遇敌人 / 无伤连胜 / 组合 / 计数 等）
  └─ match_events(events, tracker) → list[DanmuTriggerHit]
  └─ 每条 hit → pick_rule_phrase(trigger, context, variant) → push_text(text, style)
```

关键设计：**tracker 只发布"能从快照可靠观察到的变化事实"**，规则引擎只做
"事件 → 规则"的映射，跨快照的长期状态（计数/连胜/已遇集合）由 tracker 持有。

## 2. 数据源（raw_state 字段，防御性解析）

| 数据源 | 字段 | 说明 |
|---|---|---|
| `raw_state.run` | `floor / act / gold / current_hp / max_hp / character_id` | run 级数值 |
| `raw_state.combat` | `player{current_hp,max_hp,block,energy} / hand[] / enemies[] / turn` | 战斗状态 |
| `raw_state.combat.enemies[]` | `id / enemy_id / model_id / name / current_hp / hp / intent / move_id` | 敌方身份/血量/意图 |
| `raw_state.combat.hand[]` | `id / card_id / name` | 手牌 |
| `raw_state.deck` | `{cards: [{id, card_id, name, upgrade_level}]}` | 牌库（含升级等级） |
| `raw_state.relics[]` | `id / relic_id / name` | 遗物 |
| `raw_state.potions[]` | `id / potion_id / name` | 药水 |
| `raw_state.reward` | `{cards: [{id, card_id, name}]}` | 奖励候选 |
| `raw_state.selection` | `{cards: [...]}` | 选牌候选 |
| `raw_state.shop` | `{cards[], relics[], potions[]}` | 商店货品 |
| `raw_state.event` | `name / event_id` | 事件名 |
| `raw_state.map` | `current_node{type} / nodes[]` | 当前节点类型（精英判定） |
| `snapshot` | `screen / in_combat / floor / act / character` | 归一化快照 |

## 3. tracker 状态字段（DanmuEventTracker）

### 3.1 场景上下文（进入场景时快照，离开时清理）

| 状态 | 含义 | 来源 / 更新时机 | 服务规则 |
|---|---|---|---|
| `_scene` | 当前场景（combat/reward/shop/rest/event/map/...） | 每帧 `_scene_of()` | 全局 |
| `_rest_enter_hp` | 进入火堆时的 HP | `_on_scene_enter` rest | G4/G5/G6 |
| `_rest_enter_deck_upgrades` | 进入火堆时的卡牌升级等级表 | 同上 | G7 |
| `_rest_upgraded` | 本次火堆是否升级过卡牌 | `card_upgraded` + rest 场景 | G7 |
| `_reward_enter_candidates` | 进入奖励/选牌时的候选牌 id 列表 | `_on_scene_enter` reward/selection | E6/E7/E9/E10/E12 |
| `_shop_enter_gold` | 进入商店时的金币 | `_on_scene_enter` shop | F1 |

### 3.2 跨快照 run 状态

| 状态 | 含义 | 更新时机 | 服务规则 |
|---|---|---|---|
| `_seen_enemies` | 本 run 已遇过的敌怪 id 集合 | `combat_started` 时并入 | A3 |
| `_won_combat_enemies` | 本 run 已胜利战斗的敌怪 id 集合 | `combat_ended`(won) 时并入 | A4 |
| `_no_damage_streak` | 连续无伤战斗数 | `combat_ended`；被攻击时清零 | C6/C9 |
| `_upgrade_streak` | 连续升级卡牌的火堆数 | 火堆结束；其他选项清零 | G7 |
| `_card_visit_count` | 卡牌在选牌/商店出现次数 dict | 奖励进入 + 获得牌时累加 | E12 |
| `_elite_count_by_act` | 每 Act 打过的精英节点（按 floor）集合 | `combat_started` + 精英房 | A6 |
| `_owned` | 本 run 已获得的卡牌/遗物 id 集合 | `card_obtained`/`relic_obtained` | E13 |
| `_pair_notified` | 已通知过的组合 (pair_id, phase) 集合 | 组合检测时 | E13 |
| `_big_deck_triggered` | 本 run 是否已触发 BigDeck | `card_obtained` + deck>40 | E11 |

### 3.3 战斗级状态（进入战斗时重置，离开时清理）

| 状态 | 含义 | 服务规则 |
|---|---|---|
| `_current_combat_enemies` | 本场敌人集合 | A2/A4/Reconviction |
| `_combat_turn` | 本场当前回合 | C4 |
| `_combat_is_first_turn` | 是否首回合 | C4 |
| `_potion_used_in_combat` | 本场药水使用次数 | B7 |
| `_combat_turn_plays` | 本回合打出牌数（回合切换重置） | B5 |
| `_big_turn_fired_this_turn` | 本回合 BigTurn 是否已触发（每回合一次） | B5 |
| `_combat_damage_count` | 本场玩家受伤次数 | C7 |
| `_combat_enemy_hps` | 每敌当前血量（用于敌方掉血 diff） | C5 |
| `_combat_enemy_intents` | 每敌当前意图 | B1/B2/C2 |
| `_idle_ticks` | 连续无操作 tick 数 | B6 |
| `_elite_combat` | 本场是否精英房 | A6 |
| `_queen_combat` | 本场是否含女王/火炬头 | D3 |
| `_scroll_biting_combat` | 本场是否含咬人卷轴 | D6/D7 |
| `_bowlbug_rock_combat` | 本场是否含盛碗虫（石） | C3 |
| `_test_subject_combat` | 本场是否含实验体 | C7 |
| `_sculptor_combat` | 本场是否含虔诚雕刻师 | D4/D5 |
| `_sculptor_chaned` | 本场雕刻师是否用过禁忌唱颂（按行动名） | D4/D5 |
| `_scroll_max_hp_lost` | 本场是否掉过血上限 | D7 |
| `_combat_notified` | 本场已通知过的手牌质量规则（防刷屏） | B1/B2/C2 |
| `_discarded_block_this_turn` | 本回合是否弃掉过可打防御牌 | B3 |

### 3.4 其它

| 状态 | 含义 |
|---|---|
| `_run_id` / `_character` / `_event_architect` | run id / 角色 / 当前是否建筑师事件 |

## 4. 事件 → 规则 总表

| tracker 事件 | 携带 context | 命中规则 |
|---|---|---|
| `run_started` / `run_ended` / `save_loaded` | character / — | — / — / H1 |
| `floor_changed` / `act_changed` | floor, act | —（触发条件） |
| `combat_started` | enemy_ids, hp, max_hp, block, floor, act, encountered_before | A1/A3/A5/A6 |
| `combat_ended` | won, no_damage_streak, damaged | C9 / D7 |
| `turn_started` | turn | —（B3 标记用） |
| `player_damaged` | amount, hp, block, streak_broken | C1/C3/C6/C7/B3 |
| `player_death` | hp | C8 |
| `max_hp_lost` | amount | D6 |
| `card_obtained` | card, act, floor, max_hp, hp, duplicate, deck_size | E1-E5/E8/E11/E12 |
| `card_removed` | card | F2 / G2（看场景） |
| `card_upgraded` | card, level | —（G7 计数用） |
| `relic_obtained` | item | E4 |
| `enemy_killed` | enemy | A2/A4/D2/D4 |
| `card_played` | card | B4/D1/D3 |
| `reward_opened` | candidates | E10 |
| `reward_skipped` | candidates | E6/E7/E9 |
| `shop_purchased` | gold_before/after, spent | F1 |
| `rest_sleep` | hp_before/after, max_hp | G4/G5 |
| `rest_other` | hp_before, hp, max_hp | G6 |
| `upgrade_streak` | count | G7 |
| `event_opened` | event_name | — |
| `combat_binge` / `draw_overflow` | count | B7 / B8 |
| `elite_streak` | count, act | A6 |
| `collectible_pair` | item, variant | E13 |
| `one_turn_kill` | enemy | C4 |
| `queen_damaged` | card | D3 |
| `scroll_max_hp_protected` | — | D7 |
| `architect_with_potion` | potions | G1 |
| `big_deck` | deck_size | E11 |
| `big_turn` | count | B5 |
| `fake_thinking` | ticks | B6 |
| `single_card_high_damage` | amount, card | C5 |
| `number_extreme` | — | C2 |
| `defense_lack` / `offense_lack` | act | B1 / B2 |
| `has_block_no_play` | amount | B3 |
| `bowlbug_rock_extreme` | amount | C3 |
| `experiment_chip_damage` | count | C7 |
| `sculptor_pre_chant` / `sculptor_chant` | enemy | D4 / D5 |
| `counter_match` | card, enemies | D1 |
| `multiplayer_reward_select` | card, card_name | I1 |
| `multiplayer_shop_purchase` | item, variant(removal) | I2 |
| `multiplayer_rest_site` | card, card_name | I3 |

## 5. ① 规则详细（快照信息足够，仅差实现）

### A6 EliteStreak — 连续打精英
- **触发事件**：`elite_streak`（第 3 个不同精英房进入时）
- **依赖状态**：`_elite_count_by_act`、`_elite_combat`
- **数据源**：`raw_state.map.current_node.type == "elite"` + `floor`（作精英节点 key）
- **逻辑**：进入精英战斗时按 Act 记录该层；同一 Act 内第 3 个不同精英房触发
- **近似/局限**：精英判定依赖 map 当前节点类型（战斗时游戏是否仍报该节点不确定）；按层数去重，SL 回档可能重复计数

### E13 CollectiblePair — 强力组合
- **触发事件**：`collectible_pair`（variant=`waiting` 获 A 时 / `completed` 集齐时）
- **依赖状态**：`_owned`、`_pair_notified`
- **数据源**：`card_obtained`/`relic_obtained` 的 id
- **组合表**（`_COLLECTIBLE_PAIRS`）：TEMPEST→VOLTAIC、DOUBLE_ENERGY→冰淇淋、会员卡→送货员(双向)、切肉刀→微型帐篷
- **近似/局限**：跳过需要分类判断的 A=None 组合（X 费牌→化学物X、附魔攻击→打火机）；timeout 变体未实现；waiting 每个组合只通知一次

### C4 OneTurnKill — 首回合击杀
- **触发事件**：`one_turn_kill`
- **依赖状态**：`_combat_is_first_turn`
- **数据源**：`combat.enemies[]` 数量减少（`enemy_killed`）+ `combat.turn`
- **逻辑**：首个玩家回合内（turn 仍为 1）敌怪消失
- **近似/局限**：无法区分"回合内击杀"与"回合结算消失"的精确时机

### D3 QueenDamaged — 女王战单体攻击女王
- **触发事件**：`queen_damaged`
- **依赖状态**：`_queen_combat`
- **数据源**：`card_played` + `_card_in_category(card, "Attack")` + 非 Aoe + `combat.enemies` 含女王/火炬头
- **近似/局限**：无法确认目标是否为女王本人；"单体攻击"用"非 AOE 的攻击牌"近似

### D7 ScrollMaxHpProtected — 卷轴战保护血上限
- **触发事件**：`scroll_max_hp_protected`（卷轴战胜利时）
- **依赖状态**：`_scroll_biting_combat`、`_scroll_max_hp_lost`
- **数据源**：`combat.enemies` 含 `SCROLL_OF_BITING` + `max_hp_lost` + `combat_ended`(won)
- **逻辑**：战斗含卷轴 + 胜利 + 整场未掉 max_hp → 触发
- **近似/局限**：无

### G1 ArchitectWithPotion — 建筑师带药水
- **触发事件**：`architect_with_potion`
- **依赖状态**：`_event_architect`
- **数据源**：`raw_state.event.name` 含 "architect" + `potions` 非空
- **近似/局限**：事件名匹配英文 "architect"；若游戏返回中文事件名则不命中

### E5 AttackDefenseCard — 攻防一体牌
- **触发事件**：`card_obtained` 时规则引擎判断
- **依赖状态**：`_card_in_category(card, "Attack")` 且 `_card_in_category(card, "Block")`
- **数据源**：`danmu_card_categories.json`（模组 CardCategories.cs）+ 获得牌 id
- **近似/局限**：依赖分类表 id 与游戏返回 card_id 一致；铁斩波类牌需同时在攻防分类

### E11 BigDeck — 大卡组
- **触发事件**：`big_deck`（牌库首次 >40）
- **依赖状态**：`_big_deck_triggered`
- **数据源**：`raw_state.deck.cards` 数量（`deck_size`）
- **近似/局限**：模组有倒计数（触发后每增 5 张再触发）；仓库简化为一 run 一次

### E12 CardThreeVisits — 卡牌多次出现
- **触发事件**：`card_obtained` 时规则引擎判断
- **依赖状态**：`_card_visit_count`
- **数据源**：奖励/选牌候选 + 获得牌（每次出现 +1）
- **近似/局限**：候选重复帧已去重（进入场景时只加一次）；商店候选计次目前未接入

### E10 HardChoice — 候选 ≥2 超模
- **触发事件**：`reward_opened`
- **依赖状态**：`_reward_enter_candidates`（候选）、`_KEY_CARDS`/`_OVERPOWERED_CARDS`
- **数据源**：`raw_state.reward.cards` / `raw_state.selection.cards`
- **近似/局限**：无

## 6. ② 规则详细（近似可达，有误报风险）

### B1 DefenseLack / B2 OffenseLack — 无防/无攻（Act1）
- **触发事件**：`defense_lack` / `offense_lack`
- **依赖状态**：`_combat_enemy_intents`、手牌分类（Attack/Block/Draw）、`_combat_notified`
- **数据源**：`combat.hand[]` + `combat.enemies[].intent/move_id` + `act`
- **逻辑**：DefenseLack = 敌有攻击意图 + 手牌无防御牌无过牌；OffenseLack = 敌无攻击意图 + 手牌无攻击牌无过牌
- **近似/局限**：① 每场战斗只触发一次（notified），模组是每回合检查；② 意图关键字匹配可能漏判；③ "过牌牌"用 Draw 分类近似；④ 手牌含不可打出的防御牌时误报

### B3 HasBlockNoPlay — 有防不出
- **触发事件**：`has_block_no_play`
- **依赖状态**：`_discarded_block_this_turn`
- **数据源**：turn 变化时手牌防御牌消失 + 随后 `player_damaged`
- **近似/局限**：无法区分"弃牌"与"打出防御牌"；仅在 turn 切换边界检测，误报/漏报并存

### B5 BigTurn — 大回合
- **触发事件**：`big_turn`（同一回合内打出 ≥5 张牌，每回合最多一次）
- **依赖状态**：`_combat_turn_plays`、`_big_turn_fired_this_turn`、手牌计数
- **数据源**：`combat.hand[]` 减少（打出/弃牌近似）
- **近似/局限**：模组"等待区停留 ≥3s"部分无法检测（快照无队列）；同一快照内一次减多张只计 1（按唯一牌 id），逐张打出计数更准

### B6 FakeThinking — 假装思考
- **触发事件**：`fake_thinking`（连续无操作 tick ≥8）
- **依赖状态**：`_idle_ticks`
- **数据源**：combat 内无 `card_played`/受伤/药水消耗的连续帧
- **近似/局限**：**tick 数非真实时间**（poll 间隔不定）；阈值 8 tick ≈ 12s 仅当 tick ~1.5s；触发后重置

### C2 NumberExtreme — 精确格挡
- **触发事件**：`number_extreme`
- **依赖状态**：`_combat_enemy_intents`、`prev.has_block` → `new.has_block`、`hp` 不变
- **数据源**：block 从 >0 → 0 + hp 不变 + 敌攻击意图
- **近似/局限**：仅覆盖"精确格挡"分支；无法确认是攻击结算消耗格挡（可能是其他原因）；每场一次

### C3 BowlbugRockExtreme — 盛碗虫恰好 1 血
- **触发事件**：`bowlbug_rock_extreme`
- **依赖状态**：`_bowlbug_rock_combat`
- **数据源**：`combat.enemies` 含 `BOWLBUG_ROCK` + `player_damaged` amount==1
- **近似/局限**：无法确认伤害来源是该虫

### C5 SingleCardHighDamage — 单卡高伤害
- **触发事件**：`single_card_high_damage`
- **依赖状态**：`_combat_enemy_hps`
- **数据源**：本帧敌方总掉血 ≥40 + 本帧有 `card_played`
- **近似/局限**：**是"本帧敌方总掉血"而非单卡伤害**；多敌合并/多次攻击会误报；阈值 40 对齐模组

### C7 ExperimentChipDamage — 磨实验体
- **触发事件**：`experiment_chip_damage`（受伤 ≥5）
- **依赖状态**：`_test_subject_combat`、`_combat_damage_count`
- **数据源**：`combat.enemies` 含 `TEST_SUBJECT` + 玩家受伤计数
- **近似/局限**：无法检测"无实体" power；"伤害次数"近似为玩家每次受伤 +1；忽略单次多段

### D1 CounterMatch — 专业对口
- **触发事件**：`counter_match`
- **依赖状态**：手牌分类 Aoe、`enemy_ids` 数量
- **数据源**：`card_played` + `_card_in_category(card, "Aoe")` + 敌怪 ≥3
- **近似/局限**：仅实现"AOE 打 ≥3 敌"分支；"多段打外骨骼虫"未实现（需目标）

### D4 SculptorPreChant — 雕刻师未唱颂被击杀
- **触发事件**：`sculptor_pre_chant`
- **依赖状态**：`_sculptor_combat`、`_sculptor_chaned`
- **数据源**：`combat.enemies` 含 `DEVOTED_SCULPTOR` + `enemy_killed`
- **逻辑**：击杀雕刻师时，若本场**从未出现**禁忌唱颂行动名 → PreChant
- **近似/局限**：无（行动名判断可靠）

### D5 SculptorChant — 雕刻师唱颂后击杀
- **触发事件**：`sculptor_chant`
- **依赖状态**：`_sculptor_combat`、`_sculptor_chaned`
- **数据源**：`combat.enemies[].intent/move_id` 含 `FORBIDDEN_INCANTATION_MOVE`（关键字 FORBIDDEN/INCANTATION）+ `enemy_killed`
- **逻辑**：雕刻师意图变为禁忌唱颂后，击杀它 → Chant（与 D4 互斥）
- **近似/局限**：按个体行动名判断，可靠

### G2 BridgeEvent — 桥事件失去卡牌
- **触发事件**：`card_removed` + `tracker.scene == "event"`
- **依赖状态**：`_scene`（event）
- **数据源**：`raw_state.deck.cards` 减少
- **近似/局限**：**无法区分失去弱牌/强牌**，固定用 variant="weak"；词条组中 strong 变体（我恨桥等）不会出现

## 7. 多人规则（I1-I3，行为播报）

多人模式（`_multiplayer`）通过配置 `danmu_multiplayer_enabled` 强制开启，或
tracker 从 `session`/`run` 的 `player_count/num_players/players/player_ids` 字段
自动检测。多人模式下把本地玩家的行为以播报弹幕显示（观众视角）。

| 规则 | 触发事件 | 依赖状态 | 词条 |
|---|---|---|---|
| **I1 MultiplayerRewardSelect** | `multiplayer_reward_select`（reward/selection 场景获牌） | `_multiplayer`、`_card_names` | 选择了{card}（Character） |
| **I2 MultiplayerShopPurchase** | `multiplayer_shop_purchase`（商店购买/删牌） | `_multiplayer` | 购买了{item} / 删了{card}（removal 变体） |
| **I3 MultiplayerRestSite** | `multiplayer_rest_site`（火堆升级卡牌） | `_multiplayer`、`_card_names` | 敲了{card}（Character） |

- `{card}`/`{item}` 用**显示名**（`raw_state.deck/reward/hand` 卡的 `name` 字段），而非英文 id
- 与单人规则（E 组等）同时触发，文本不同互不干扰
- 多人播报词条风格为 Character（带头像）

## 8. 明确跳过的规则（快照无数据源）

| 规则 | 原因 |
|---|---|
| **G3 CloneEnchantment** | 需附魔信息，快照无 |

## 9. 阈值常量（danmu_events.py）

| 常量 | 值 | 规则 |
|---|---|---|
| `DRAW_OVERFLOW_HAND` | 10 | B8 |
| `COMBAT_BINGE_POTIONS` | 2 | B7 |
| `HIGH_DAMAGE_THRESHOLD` | 40 | C5 |
| `BIG_TURN_PLAYS` | 5 | B5 |
| `EXPERIMENT_CHIP_HITS` | 5 | C7 |
| `FAKE_THINKING_TICKS` | 8 | B6 |
| `_LOW_HP_RATIO` | 0.3 | A5/G6 |
| `_STRONG_MONSTERS_A/B` | 模组名单 | A1 |

## 9.5 发射机制（对齐 mod，danmu_spire.py + service.py）

| 维度 | 规则 | 说明 |
|---|---|---|
| 强度分级 | `_RULE_INTENSITY` | 每条规则标 light/medium/strong（对齐 DanmakuIntensityCatalog） |
| 抽选条数 | `_INTENSITY_RANGE` | light 2-3 / medium 4-6 / strong 10-12 条/次（密度 100%） |
| 角色弹幕 | `pick_rule_burst` | 每批保证 ≥1 条 catgirl（带头像），其余 narration 补足，组内不重复 |
| 顶部概率 | `_INTENSITY_TOP` | light 0 / medium 15% / strong 30%；**只 narration 置顶**（catgirl 不置顶） |
| 延迟分布 | `create_delays` | 首条 0.1-0.4s、中间截断正态 1.6±0.5s、末条 3.3-5.6s，乱序 |
| 词条密度 | `danmu_density` 配置 | 50-200%（默认 100），线性缩放抽选条数 |
| 去重 | bridge 30s TTL | 逐条按 style+placement+text 去重 |

## 10. 卡牌分类（danmu_card_categories.json）

来源：模组 `CardCategories.cs`。分类：`Attack`(198) / `Block`(78) / `Draw`(85) /
`Aoe`(29) / `MultiHit`(36) / `XCost`(11)。服务规则：B1/B2（攻防过牌）、B5（攻击牌计数）、
D1（AOE）、D3（单体攻击）、E5（攻防一体）。
