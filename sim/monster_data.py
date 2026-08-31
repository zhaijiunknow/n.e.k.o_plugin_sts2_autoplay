"""自动生成：全部怪物的 move 表（由 sim/gen_monsters.py 生成，勿手改）。"""
from .monster_ai import EnemyMove
MOVE_TABLES = {
    'AEONGLASS': {
        'EBB_MOVE': EnemyMove('EBB_MOVE', damage=26, block=0, hits=1, followup='EYE_LASERS_MOVE'),
        'EYE_LASERS_MOVE': EnemyMove('EYE_LASERS_MOVE', damage=12, block=0, hits=1, followup='INCREASING_INTENSITY_MOVE'),
        'INCREASING_INTENSITY_MOVE': EnemyMove('INCREASING_INTENSITY_MOVE', damage=0, block=0, hits=1, followup='EBB_MOVE'),
    },
    'ASSASSIN_RUBY_RAIDER': {
        'KILLSHOT_MOVE': EnemyMove('KILLSHOT_MOVE', damage=0, block=0, hits=1, followup='KILLSHOT_MOVE'),
    },
    'AXE_RUBY_RAIDER': {
        'SWING_1': EnemyMove('SWING_1', damage=6, block=0, hits=1, followup='SWING_2'),
        'SWING_2': EnemyMove('SWING_2', damage=6, block=0, hits=1, followup='BIG_SWING'),
        'BIG_SWING': EnemyMove('BIG_SWING', damage=13, block=0, hits=1, followup='SWING_1'),
    },
    'AXEBOT': {
        'BOOT_UP_MOVE': EnemyMove('BOOT_UP_MOVE', damage=0, block=0, hits=1, followup=''),
        'ONE_TWO_MOVE': EnemyMove('ONE_TWO_MOVE', damage=11, block=0, hits=2, followup=''),
        'HAMMER_UPPERCUT_MOVE': EnemyMove('HAMMER_UPPERCUT_MOVE', damage=18, block=0, hits=1, followup=''),
    },
    'BIG_DUMMY': {
        'NOTHING': EnemyMove('NOTHING', damage=0, block=0, hits=1, followup='NOTHING'),
    },
    'BOWLBUG_EGG': {
        'BITE_MOVE': EnemyMove('BITE_MOVE', damage=8, block=0, hits=1, followup='BITE_MOVE'),
    },
    'BOWLBUG_NECTAR': {
        'THRASH_MOVE': EnemyMove('THRASH_MOVE', damage=0, block=0, hits=1, followup='BUFF_MOVE'),
        'BUFF_MOVE': EnemyMove('BUFF_MOVE', damage=0, block=0, hits=1, followup='THRASH2_MOVE'),
        'THRASH2_MOVE': EnemyMove('THRASH2_MOVE', damage=0, block=0, hits=1, followup='THRASH2_MOVE'),
    },
    'BOWLBUG_ROCK': {
        'HEADBUTT_MOVE': EnemyMove('HEADBUTT_MOVE', damage=0, block=0, hits=1, followup=''),
        'DIZZY_MOVE': EnemyMove('DIZZY_MOVE', damage=0, block=0, hits=1, followup='HEADBUTT_MOVE'),
    },
    'BOWLBUG_SILK': {
        'THRASH_MOVE': EnemyMove('THRASH_MOVE', damage=5, block=0, hits=2, followup=''),
        'TOXIC_SPIT_MOVE': EnemyMove('TOXIC_SPIT_MOVE', damage=0, block=0, hits=1, followup=''),
    },
    'BRUTE_RUBY_RAIDER': {
        'BEAT_MOVE': EnemyMove('BEAT_MOVE', damage=8, block=0, hits=1, followup=''),
        'ROAR_MOVE': EnemyMove('ROAR_MOVE', damage=0, block=0, hits=1, followup=''),
    },
    'BYGONE_EFFIGY': {
        'SLEEP_MOVE': EnemyMove('SLEEP_MOVE', damage=0, block=0, hits=1, followup='WAKE_MOVE'),
        'WAKE_MOVE': EnemyMove('WAKE_MOVE', damage=0, block=0, hits=1, followup='SLASHES_MOVE'),
        'SLEEP_MOVE_2': EnemyMove('SLEEP_MOVE_2', damage=0, block=0, hits=1, followup='SLASHES_MOVE'),
        'SLASHES_MOVE': EnemyMove('SLASHES_MOVE', damage=15, block=0, hits=1, followup='SLASHES_MOVE'),
    },
    'BYRDONIS': {
        'PECK_MOVE': EnemyMove('PECK_MOVE', damage=0, block=0, hits=1, followup='SWOOP_MOVE'),
        'SWOOP_MOVE': EnemyMove('SWOOP_MOVE', damage=0, block=0, hits=1, followup='PECK_MOVE'),
    },
    'CALCIFIED_CULTIST': {
        'INCANTATION_MOVE': EnemyMove('INCANTATION_MOVE', damage=0, block=0, hits=1, followup=''),
        'DARK_STRIKE_MOVE': EnemyMove('DARK_STRIKE_MOVE', damage=11, block=0, hits=1, followup=''),
    },
    'CEREMONIAL_BEAST': {
        'STAMP_MOVE': EnemyMove('STAMP_MOVE', damage=0, block=0, hits=1, followup='PLOW_MOVE'),
        'PLOW_MOVE': EnemyMove('PLOW_MOVE', damage=20, block=0, hits=1, followup='PLOW_MOVE'),
        'STUN_MOVE': EnemyMove('STUN_MOVE', damage=0, block=0, hits=1, followup='BEAST_CRY_MOVE'),
        'BEAST_CRY_MOVE': EnemyMove('BEAST_CRY_MOVE', damage=0, block=0, hits=1, followup='STOMP_MOVE'),
        'STOMP_MOVE': EnemyMove('STOMP_MOVE', damage=17, block=0, hits=1, followup='CRUSH_MOVE'),
        'CRUSH_MOVE': EnemyMove('CRUSH_MOVE', damage=19, block=0, hits=1, followup='BEAST_CRY_MOVE'),
    },
    'CHOMPER': {
        'CLAMP_MOVE': EnemyMove('CLAMP_MOVE', damage=0, block=0, hits=2, followup=''),
        'SCREECH_MOVE': EnemyMove('SCREECH_MOVE', damage=0, block=0, hits=1, followup=''),
    },
    'CORPSE_SLUG': {
        'WHIP_SLAP_MOVE': EnemyMove('WHIP_SLAP_MOVE', damage=0, block=0, hits=1, followup='GLOMP_MOVE'),
        'GLOMP_MOVE': EnemyMove('GLOMP_MOVE', damage=9, block=0, hits=1, followup='GOOP_MOVE'),
        'GOOP_MOVE': EnemyMove('GOOP_MOVE', damage=0, block=0, hits=1, followup='WHIP_SLAP_MOVE'),
    },
    'CROSSBOW_RUBY_RAIDER': {
        'FIRE_MOVE': EnemyMove('FIRE_MOVE', damage=16, block=0, hits=1, followup=''),
        'RELOAD_MOVE': EnemyMove('RELOAD_MOVE', damage=0, block=0, hits=1, followup=''),
    },
    'CRUSHER': {
        'THRASH_MOVE': EnemyMove('THRASH_MOVE', damage=14, block=0, hits=1, followup='ENLARGING_STRIKE_MOVE'),
        'ENLARGING_STRIKE_MOVE': EnemyMove('ENLARGING_STRIKE_MOVE', damage=4, block=0, hits=1, followup='BUG_STING_MOVE'),
        'BUG_STING_MOVE': EnemyMove('BUG_STING_MOVE', damage=7, block=0, hits=1, followup='ADAPT_MOVE'),
        'ADAPT_MOVE': EnemyMove('ADAPT_MOVE', damage=0, block=0, hits=1, followup='GUARDED_STRIKE_MOVE'),
        'GUARDED_STRIKE_MOVE': EnemyMove('GUARDED_STRIKE_MOVE', damage=14, block=0, hits=1, followup='THRASH_MOVE'),
    },
    'CUBEX_CONSTRUCT': {
        'CHARGE_UP_MOVE': EnemyMove('CHARGE_UP_MOVE', damage=0, block=0, hits=1, followup='REPEATER_BLAST_MOVE'),
        'REPEATER_BLAST_MOVE': EnemyMove('REPEATER_BLAST_MOVE', damage=8, block=0, hits=1, followup='REPEATER_BLAST_MOVE_2'),
        'REPEATER_BLAST_MOVE_2': EnemyMove('REPEATER_BLAST_MOVE_2', damage=8, block=0, hits=1, followup='EXPEL_MOVE'),
        'EXPEL_MOVE': EnemyMove('EXPEL_MOVE', damage=6, block=0, hits=2, followup='REPEATER_BLAST_MOVE'),
    },
    'DAMP_CULTIST': {
        'INCANTATION_MOVE': EnemyMove('INCANTATION_MOVE', damage=0, block=0, hits=1, followup=''),
        'DARK_STRIKE_MOVE': EnemyMove('DARK_STRIKE_MOVE', damage=3, block=0, hits=1, followup=''),
    },
    'DECIMILLIPEDE_SEGMENT': {
        'WRITHE_MOVE': EnemyMove('WRITHE_MOVE', damage=6, block=0, hits=2, followup='CONSTRICT_MOVE'),
        'BULK_MOVE': EnemyMove('BULK_MOVE', damage=7, block=0, hits=1, followup='WRITHE_MOVE'),
        'CONSTRICT_MOVE': EnemyMove('CONSTRICT_MOVE', damage=9, block=0, hits=1, followup='BULK_MOVE'),
        'REATTACH_MOVE': EnemyMove('REATTACH_MOVE', damage=0, block=0, hits=1, followup=''),
    },
    'DEVOTED_SCULPTOR': {
        'FORBIDDEN_INCANTATION_MOVE': EnemyMove('FORBIDDEN_INCANTATION_MOVE', damage=0, block=0, hits=1, followup=''),
        'SAVAGE_MOVE': EnemyMove('SAVAGE_MOVE', damage=15, block=0, hits=1, followup=''),
    },
    'ENTOMANCER': {
        'PHEROMONE_SPIT_MOVE': EnemyMove('PHEROMONE_SPIT_MOVE', damage=0, block=0, hits=1, followup='BEES_MOVE'),
        'BEES_MOVE': EnemyMove('BEES_MOVE', damage=3, block=0, hits=1, followup=''),
        'SPEAR_MOVE': EnemyMove('SPEAR_MOVE', damage=20, block=0, hits=1, followup=''),
    },
    'EXOSKELETON': {
        'SKITTER_MOVE': EnemyMove('SKITTER_MOVE', damage=0, block=0, hits=1, followup=''),
        'MANDIBLES_MOVE': EnemyMove('MANDIBLES_MOVE', damage=9, block=0, hits=1, followup='ENRAGE_MOVE'),
        'ENRAGE_MOVE': EnemyMove('ENRAGE_MOVE', damage=0, block=0, hits=1, followup=''),
    },
    'EYE_WITH_TEETH': {
        'DISTRACT_MOVE': EnemyMove('DISTRACT_MOVE', damage=0, block=0, hits=1, followup='DISTRACT_MOVE'),
    },
    'FABRICATOR': {
        'FABRICATE_MOVE': EnemyMove('FABRICATE_MOVE', damage=0, block=0, hits=1, followup=''),
        'FABRICATING_STRIKE_MOVE': EnemyMove('FABRICATING_STRIKE_MOVE', damage=21, block=0, hits=1, followup=''),
        'DISINTEGRATE_MOVE': EnemyMove('DISINTEGRATE_MOVE', damage=13, block=0, hits=1, followup=''),
    },
    'FAKE_MERCHANT_MONSTER': {
        'SWIPE_MOVE': EnemyMove('SWIPE_MOVE', damage=15, block=0, hits=1, followup=''),
        'SPEW_COINS_MOVE': EnemyMove('SPEW_COINS_MOVE', damage=0, block=0, hits=8, followup=''),
        'THROW_RELIC_MOVE': EnemyMove('THROW_RELIC_MOVE', damage=10, block=0, hits=1, followup=''),
        'ENRAGE_MOVE': EnemyMove('ENRAGE_MOVE', damage=0, block=0, hits=1, followup=''),
    },
    'FAT_GREMLIN': {
        'SPAWNED_MOVE': EnemyMove('SPAWNED_MOVE', damage=0, block=0, hits=1, followup=''),
        'FLEE_MOVE': EnemyMove('FLEE_MOVE', damage=0, block=0, hits=1, followup=''),
    },
    'FLAIL_KNIGHT': {
        'WAR_CHANT': EnemyMove('WAR_CHANT', damage=0, block=0, hits=1, followup=''),
        'FLAIL_MOVE': EnemyMove('FLAIL_MOVE', damage=10, block=0, hits=2, followup=''),
        'RAM_MOVE': EnemyMove('RAM_MOVE', damage=17, block=0, hits=1, followup=''),
    },
    'FLYCONID': {
        'VULNERABLE_SPORES_MOVE': EnemyMove('VULNERABLE_SPORES_MOVE', damage=0, block=0, hits=1, followup=''),
        'FRAIL_SPORES_MOVE': EnemyMove('FRAIL_SPORES_MOVE', damage=9, block=0, hits=1, followup=''),
        'SMASH_MOVE': EnemyMove('SMASH_MOVE', damage=12, block=0, hits=1, followup=''),
    },
    'FOGMOG': {
        'ILLUSION_MOVE': EnemyMove('ILLUSION_MOVE', damage=0, block=0, hits=1, followup='SWIPE_MOVE'),
        'SWIPE_MOVE': EnemyMove('SWIPE_MOVE', damage=9, block=0, hits=1, followup=''),
        'SWIPE_RANDOM_MOVE': EnemyMove('SWIPE_RANDOM_MOVE', damage=9, block=0, hits=1, followup='HEADBUTT_MOVE'),
        'HEADBUTT_MOVE': EnemyMove('HEADBUTT_MOVE', damage=16, block=0, hits=1, followup='SWIPE_MOVE'),
    },
    'FOSSIL_STALKER': {
        'TACKLE_MOVE': EnemyMove('TACKLE_MOVE', damage=11, block=0, hits=1, followup=''),
        'LATCH_MOVE': EnemyMove('LATCH_MOVE', damage=14, block=0, hits=1, followup=''),
        'LASH_MOVE': EnemyMove('LASH_MOVE', damage=4, block=0, hits=1, followup=''),
    },
    'FROG_KNIGHT': {
        'FOR_THE_QUEEN': EnemyMove('FOR_THE_QUEEN', damage=0, block=0, hits=1, followup=''),
        'STRIKE_DOWN_EVIL': EnemyMove('STRIKE_DOWN_EVIL', damage=23, block=0, hits=1, followup='FOR_THE_QUEEN'),
        'TONGUE_LASH': EnemyMove('TONGUE_LASH', damage=14, block=0, hits=1, followup='STRIKE_DOWN_EVIL'),
        'BEETLE_CHARGE': EnemyMove('BEETLE_CHARGE', damage=40, block=0, hits=1, followup='TONGUE_LASH'),
    },
    'FUZZY_WURM_CRAWLER': {
        'FIRST_ACID_GOOP': EnemyMove('FIRST_ACID_GOOP', damage=6, block=0, hits=1, followup=''),
        'ACID_GOOP': EnemyMove('ACID_GOOP', damage=6, block=0, hits=1, followup='FIRST_ACID_GOOP'),
        'INHALE': EnemyMove('INHALE', damage=0, block=0, hits=1, followup=''),
    },
    'GLOBE_HEAD': {
        'THUNDER_STRIKE': EnemyMove('THUNDER_STRIKE', damage=7, block=0, hits=3, followup='GALVANIC_BURST'),
        'SHOCKING_SLAP': EnemyMove('SHOCKING_SLAP', damage=14, block=0, hits=1, followup='THUNDER_STRIKE'),
        'GALVANIC_BURST': EnemyMove('GALVANIC_BURST', damage=17, block=0, hits=1, followup='SHOCKING_SLAP'),
    },
    'GREMLIN_MERC': {
        'GIMME_MOVE': EnemyMove('GIMME_MOVE', damage=8, block=0, hits=1, followup='DOUBLE_SMASH_MOVE'),
        'DOUBLE_SMASH_MOVE': EnemyMove('DOUBLE_SMASH_MOVE', damage=7, block=0, hits=1, followup='HEHE_MOVE'),
        'HEHE_MOVE': EnemyMove('HEHE_MOVE', damage=9, block=0, hits=1, followup='GIMME_MOVE'),
    },
    'GUARDBOT': {
        'GUARD_MOVE': EnemyMove('GUARD_MOVE', damage=0, block=0, hits=1, followup='GUARD_MOVE'),
    },
    'HAUNTED_SHIP': {
        'SWIPE_MOVE': EnemyMove('SWIPE_MOVE', damage=14, block=0, hits=1, followup='STOMP_MOVE'),
        'STOMP_MOVE': EnemyMove('STOMP_MOVE', damage=5, block=0, hits=1, followup='SWIPE_MOVE'),
        'HAUNT_MOVE': EnemyMove('HAUNT_MOVE', damage=0, block=0, hits=1, followup='SWIPE_MOVE'),
    },
    'HUNTER_KILLER': {
        'TENDERIZING_GOOP_MOVE': EnemyMove('TENDERIZING_GOOP_MOVE', damage=0, block=0, hits=1, followup=''),
        'BITE_MOVE': EnemyMove('BITE_MOVE', damage=19, block=0, hits=1, followup=''),
        'PUNCTURE_MOVE': EnemyMove('PUNCTURE_MOVE', damage=8, block=0, hits=3, followup=''),
    },
    'INFESTED_PRISM': {
        'JAB_MOVE': EnemyMove('JAB_MOVE', damage=17, block=0, hits=1, followup='RADIATE_MOVE'),
        'RADIATE_MOVE': EnemyMove('RADIATE_MOVE', damage=13, block=0, hits=1, followup='WHIRLWIND_MOVE'),
        'WHIRLWIND_MOVE': EnemyMove('WHIRLWIND_MOVE', damage=6, block=0, hits=1, followup='PULSATE_MOVE'),
        'PULSATE_MOVE': EnemyMove('PULSATE_MOVE', damage=10, block=0, hits=1, followup='JAB_MOVE'),
    },
    'INKLET': {
        'JAB_MOVE': EnemyMove('JAB_MOVE', damage=4, block=0, hits=1, followup=''),
        'WHIRLWIND_MOVE': EnemyMove('WHIRLWIND_MOVE', damage=3, block=0, hits=3, followup='JAB_MOVE'),
        'PIERCING_GAZE_MOVE': EnemyMove('PIERCING_GAZE_MOVE', damage=11, block=0, hits=1, followup='JAB_MOVE'),
    },
    'KIN_FOLLOWER': {
        'QUICK_SLASH_MOVE': EnemyMove('QUICK_SLASH_MOVE', damage=5, block=0, hits=1, followup='BOOMERANG_MOVE'),
        'BOOMERANG_MOVE': EnemyMove('BOOMERANG_MOVE', damage=2, block=0, hits=2, followup='POWER_DANCE_MOVE'),
        'POWER_DANCE_MOVE': EnemyMove('POWER_DANCE_MOVE', damage=0, block=0, hits=1, followup='QUICK_SLASH_MOVE'),
    },
    'KIN_PRIEST': {
        'ORB_OF_FRAILTY_MOVE': EnemyMove('ORB_OF_FRAILTY_MOVE', damage=9, block=0, hits=1, followup='ORB_OF_WEAKNESS_MOVE'),
        'ORB_OF_WEAKNESS_MOVE': EnemyMove('ORB_OF_WEAKNESS_MOVE', damage=9, block=0, hits=1, followup='BEAM_MOVE'),
        'BEAM_MOVE': EnemyMove('BEAM_MOVE', damage=3, block=0, hits=3, followup='RITUAL_MOVE'),
        'RITUAL_MOVE': EnemyMove('RITUAL_MOVE', damage=0, block=0, hits=1, followup='ORB_OF_FRAILTY_MOVE'),
    },
    'KNOWLEDGE_DEMON': {
        'CURSE_OF_KNOWLEDGE_MOVE': EnemyMove('CURSE_OF_KNOWLEDGE_MOVE', damage=0, block=0, hits=1, followup='SLAP_MOVE'),
        'SLAP_MOVE': EnemyMove('SLAP_MOVE', damage=18, block=0, hits=1, followup='KNOWLEDGE_OVERWHELMING_MOVE'),
        'KNOWLEDGE_OVERWHELMING_MOVE': EnemyMove('KNOWLEDGE_OVERWHELMING_MOVE', damage=9, block=0, hits=3, followup='PONDER_MOVE'),
        'PONDER_MOVE': EnemyMove('PONDER_MOVE', damage=13, block=0, hits=1, followup=''),
    },
    'LAGAVULIN_MATRIARCH': {
        'SLEEP_MOVE': EnemyMove('SLEEP_MOVE', damage=0, block=0, hits=1, followup=''),
        'SLASH_MOVE': EnemyMove('SLASH_MOVE', damage=21, block=0, hits=1, followup='DISEMBOWEL_MOVE'),
        'SLASH2_MOVE': EnemyMove('SLASH2_MOVE', damage=14, block=0, hits=1, followup='SOUL_SIPHON_MOVE'),
        'DISEMBOWEL_MOVE': EnemyMove('DISEMBOWEL_MOVE', damage=10, block=0, hits=1, followup='SLASH2_MOVE'),
        'SOUL_SIPHON_MOVE': EnemyMove('SOUL_SIPHON_MOVE', damage=0, block=0, hits=1, followup='SLASH_MOVE'),
    },
    'LEAF_SLIME_M': {
        'CLUMP_SHOT': EnemyMove('CLUMP_SHOT', damage=9, block=0, hits=1, followup='STICKY_SHOT'),
        'STICKY_SHOT': EnemyMove('STICKY_SHOT', damage=0, block=0, hits=1, followup='CLUMP_SHOT'),
    },
    'LEAF_SLIME_S': {
        'TACKLE_MOVE': EnemyMove('TACKLE_MOVE', damage=4, block=0, hits=1, followup=''),
        'GOOP_MOVE': EnemyMove('GOOP_MOVE', damage=0, block=0, hits=1, followup=''),
    },
    'LIVING_FOG': {
        'ADVANCED_GAS_MOVE': EnemyMove('ADVANCED_GAS_MOVE', damage=9, block=0, hits=1, followup='BLOAT_MOVE'),
        'BLOAT_MOVE': EnemyMove('BLOAT_MOVE', damage=6, block=0, hits=1, followup='SUPER_GAS_BLAST_MOVE'),
        'SUPER_GAS_BLAST_MOVE': EnemyMove('SUPER_GAS_BLAST_MOVE', damage=9, block=0, hits=1, followup='BLOAT_MOVE'),
    },
    'LIVING_SHIELD': {
        'SHIELD_SLAM_MOVE': EnemyMove('SHIELD_SLAM_MOVE', damage=0, block=0, hits=1, followup=''),
        'SMASH_MOVE': EnemyMove('SMASH_MOVE', damage=18, block=0, hits=1, followup='SMASH_MOVE'),
    },
    'LOUSE_PROGENITOR': {
        'WEB_CANNON_MOVE': EnemyMove('WEB_CANNON_MOVE', damage=10, block=0, hits=1, followup=''),
        'POUNCE_MOVE': EnemyMove('POUNCE_MOVE', damage=16, block=0, hits=1, followup='WEB_CANNON_MOVE'),
        'CURL_AND_GROW_MOVE': EnemyMove('CURL_AND_GROW_MOVE', damage=0, block=0, hits=1, followup=''),
    },
    'MAGI_KNIGHT': {
        'POWER_SHIELD_MOVE': EnemyMove('POWER_SHIELD_MOVE', damage=7, block=0, hits=1, followup='DAMPEN_MOVE'),
        'DAMPEN_MOVE': EnemyMove('DAMPEN_MOVE', damage=0, block=0, hits=1, followup='RAM_MOVE'),
        'PREP_MOVE': EnemyMove('PREP_MOVE', damage=0, block=0, hits=1, followup='MAGIC_BOMB'),
        'MAGIC_BOMB': EnemyMove('MAGIC_BOMB', damage=40, block=0, hits=1, followup='RAM_MOVE'),
        'RAM_MOVE': EnemyMove('RAM_MOVE', damage=11, block=0, hits=1, followup='PREP_MOVE'),
    },
    'MAWLER': {
        'RIP_AND_TEAR_MOVE': EnemyMove('RIP_AND_TEAR_MOVE', damage=16, block=0, hits=1, followup=''),
        'ROAR_MOVE': EnemyMove('ROAR_MOVE', damage=0, block=0, hits=1, followup=''),
        'CLAW_MOVE': EnemyMove('CLAW_MOVE', damage=5, block=0, hits=2, followup=''),
    },
    'MECHA_KNIGHT': {
        'CHARGE_MOVE': EnemyMove('CHARGE_MOVE', damage=0, block=0, hits=1, followup='FLAMETHROWER_MOVE'),
        'FLAMETHROWER_MOVE': EnemyMove('FLAMETHROWER_MOVE', damage=0, block=0, hits=1, followup='WINDUP_MOVE'),
        'WINDUP_MOVE': EnemyMove('WINDUP_MOVE', damage=0, block=0, hits=1, followup='HEAVY_CLEAVE_MOVE'),
        'HEAVY_CLEAVE_MOVE': EnemyMove('HEAVY_CLEAVE_MOVE', damage=0, block=0, hits=1, followup='FLAMETHROWER_MOVE'),
    },
    'MULTI_ATTACK_MOVE_MONSTER': {
        'POKE': EnemyMove('POKE', damage=0, block=0, hits=5, followup='POKE'),
    },
    'MYTE': {
        'TOXIC_MOVE': EnemyMove('TOXIC_MOVE', damage=0, block=0, hits=1, followup='BITE_MOVE'),
        'BITE_MOVE': EnemyMove('BITE_MOVE', damage=15, block=0, hits=1, followup='SUCK_MOVE'),
        'SUCK_MOVE': EnemyMove('SUCK_MOVE', damage=6, block=0, hits=1, followup='TOXIC_MOVE'),
    },
    'NIBBIT': {
        'BUTT_MOVE': EnemyMove('BUTT_MOVE', damage=13, block=0, hits=1, followup='SLICE_MOVE'),
        'SLICE_MOVE': EnemyMove('SLICE_MOVE', damage=7, block=0, hits=1, followup='HISS_MOVE'),
        'HISS_MOVE': EnemyMove('HISS_MOVE', damage=0, block=0, hits=1, followup='BUTT_MOVE'),
    },
    'NOISEBOT': {
        'NOISE_MOVE': EnemyMove('NOISE_MOVE', damage=0, block=0, hits=1, followup='NOISE_MOVE'),
    },
    'ONE_HP_MONSTER': {
        'NOTHING': EnemyMove('NOTHING', damage=0, block=0, hits=1, followup='NOTHING'),
    },
    'OVICOPTER': {
        'LAY_EGGS_MOVE': EnemyMove('LAY_EGGS_MOVE', damage=0, block=0, hits=1, followup='SMASH_MOVE'),
        'SMASH_MOVE': EnemyMove('SMASH_MOVE', damage=17, block=0, hits=1, followup='TENDERIZER_MOVE'),
        'TENDERIZER_MOVE': EnemyMove('TENDERIZER_MOVE', damage=8, block=0, hits=1, followup=''),
        'NUTRITIONAL_PASTE_MOVE': EnemyMove('NUTRITIONAL_PASTE_MOVE', damage=0, block=0, hits=1, followup='SMASH_MOVE'),
    },
    'OWL_MAGISTRATE': {
        'MAGISTRATE_SCRUTINY': EnemyMove('MAGISTRATE_SCRUTINY', damage=17, block=0, hits=1, followup='PECK_ASSAULT'),
        'PECK_ASSAULT': EnemyMove('PECK_ASSAULT', damage=4, block=0, hits=6, followup='JUDICIAL_FLIGHT'),
        'JUDICIAL_FLIGHT': EnemyMove('JUDICIAL_FLIGHT', damage=0, block=0, hits=1, followup='VERDICT'),
        'VERDICT': EnemyMove('VERDICT', damage=36, block=0, hits=1, followup='MAGISTRATE_SCRUTINY'),
    },
    'PARAFRIGHT': {
        'SLAM_MOVE': EnemyMove('SLAM_MOVE', damage=17, block=0, hits=1, followup='SLAM_MOVE'),
    },
    'PHANTASMAL_GARDENER': {
        'BITE_MOVE': EnemyMove('BITE_MOVE', damage=5, block=0, hits=1, followup='LASH_MOVE'),
        'LASH_MOVE': EnemyMove('LASH_MOVE', damage=7, block=0, hits=1, followup='FLAIL_MOVE'),
        'FLAIL_MOVE': EnemyMove('FLAIL_MOVE', damage=0, block=0, hits=1, followup='ENLARGE_MOVE'),
        'ENLARGE_MOVE': EnemyMove('ENLARGE_MOVE', damage=0, block=0, hits=1, followup='BITE_MOVE'),
    },
    'PHROG_PARASITE': {
        'INFECT_MOVE': EnemyMove('INFECT_MOVE', damage=0, block=0, hits=1, followup='LASH_MOVE'),
        'LASH_MOVE': EnemyMove('LASH_MOVE', damage=5, block=0, hits=4, followup='INFECT_MOVE'),
    },
    'PUNCH_CONSTRUCT': {
        'READY_MOVE': EnemyMove('READY_MOVE', damage=0, block=0, hits=1, followup=''),
        'STRONG_PUNCH_MOVE': EnemyMove('STRONG_PUNCH_MOVE', damage=16, block=0, hits=1, followup='READY_MOVE'),
        'FAST_PUNCH_MOVE': EnemyMove('FAST_PUNCH_MOVE', damage=6, block=0, hits=1, followup=''),
    },
    'QUEEN': {
        'PUPPET_STRINGS_MOVE': EnemyMove('PUPPET_STRINGS_MOVE', damage=0, block=0, hits=1, followup='YOU_ARE_MINE_MOVE'),
        'YOU_ARE_MINE_MOVE': EnemyMove('YOU_ARE_MINE_MOVE', damage=0, block=0, hits=1, followup=''),
        'BURN_BRIGHT_FOR_ME_MOVE': EnemyMove('BURN_BRIGHT_FOR_ME_MOVE', damage=0, block=0, hits=1, followup=''),
        'OFF_WITH_YOUR_HEAD_MOVE': EnemyMove('OFF_WITH_YOUR_HEAD_MOVE', damage=4, block=0, hits=5, followup='EXECUTION_MOVE'),
        'EXECUTION_MOVE': EnemyMove('EXECUTION_MOVE', damage=18, block=0, hits=1, followup='ENRAGE_MOVE'),
        'ENRAGE_MOVE': EnemyMove('ENRAGE_MOVE', damage=0, block=0, hits=1, followup='OFF_WITH_YOUR_HEAD_MOVE'),
    },
    'ROCKET': {
        'TARGETING_RETICLE_MOVE': EnemyMove('TARGETING_RETICLE_MOVE', damage=4, block=0, hits=1, followup='PRECISION_BEAM_MOVE'),
        'PRECISION_BEAM_MOVE': EnemyMove('PRECISION_BEAM_MOVE', damage=20, block=0, hits=1, followup='CHARGE_UP_MOVE'),
        'CHARGE_UP_MOVE': EnemyMove('CHARGE_UP_MOVE', damage=0, block=0, hits=1, followup='LASER_MOVE'),
        'LASER_MOVE': EnemyMove('LASER_MOVE', damage=35, block=0, hits=1, followup='RECHARGE_MOVE'),
        'RECHARGE_MOVE': EnemyMove('RECHARGE_MOVE', damage=0, block=0, hits=1, followup='TARGETING_RETICLE_MOVE'),
    },
    'SCROLL_OF_BITING': {
        'CHOMP': EnemyMove('CHOMP', damage=16, block=0, hits=1, followup='MORE_TEETH'),
        'CHEW': EnemyMove('CHEW', damage=6, block=0, hits=2, followup=''),
        'MORE_TEETH': EnemyMove('MORE_TEETH', damage=0, block=0, hits=1, followup='CHEW'),
    },
    'SEAPUNK': {
        'SEA_KICK_MOVE': EnemyMove('SEA_KICK_MOVE', damage=13, block=0, hits=1, followup='SPINNING_KICK_MOVE'),
        'SPINNING_KICK_MOVE': EnemyMove('SPINNING_KICK_MOVE', damage=0, block=0, hits=1, followup='BUBBLE_BURP_MOVE'),
        'BUBBLE_BURP_MOVE': EnemyMove('BUBBLE_BURP_MOVE', damage=0, block=0, hits=1, followup='SEA_KICK_MOVE'),
    },
    'SEWER_CLAM': {
        'PRESSURIZE_MOVE': EnemyMove('PRESSURIZE_MOVE', damage=0, block=0, hits=1, followup=''),
        'JET_MOVE': EnemyMove('JET_MOVE', damage=11, block=0, hits=1, followup=''),
    },
    'SHRINKER_BEETLE': {
        'SHRINKER_MOVE': EnemyMove('SHRINKER_MOVE', damage=0, block=0, hits=1, followup='CHOMP_MOVE'),
        'CHOMP_MOVE': EnemyMove('CHOMP_MOVE', damage=8, block=0, hits=1, followup='STOMP_MOVE'),
        'STOMP_MOVE': EnemyMove('STOMP_MOVE', damage=14, block=0, hits=1, followup='CHOMP_MOVE'),
    },
    'SINGLE_ATTACK_MOVE_MONSTER': {
        'POKE': EnemyMove('POKE', damage=0, block=0, hits=1, followup='POKE'),
    },
    'SKULKING_COLONY': {
        'ZOOM_MOVE': EnemyMove('ZOOM_MOVE', damage=16, block=0, hits=1, followup='ZOOM_MOVE_2'),
        'ZOOM_MOVE_2': EnemyMove('ZOOM_MOVE_2', damage=16, block=0, hits=1, followup='INERTIA_MOVE'),
        'INERTIA_MOVE': EnemyMove('INERTIA_MOVE', damage=11, block=0, hits=1, followup='PIERCING_STABS_MOVE'),
        'PIERCING_STABS_MOVE': EnemyMove('PIERCING_STABS_MOVE', damage=8, block=0, hits=1, followup='ZOOM_MOVE'),
    },
    'SLIMED_BERSERKER': {
        'VOMIT_ICHOR_MOVE': EnemyMove('VOMIT_ICHOR_MOVE', damage=0, block=0, hits=1, followup=''),
        'LEECHING_HUG_MOVE': EnemyMove('LEECHING_HUG_MOVE', damage=0, block=0, hits=1, followup='SMOTHER_MOVE'),
        'SMOTHER_MOVE': EnemyMove('SMOTHER_MOVE', damage=33, block=0, hits=1, followup='VOMIT_ICHOR_MOVE'),
        'FURIOUS_PUMMELING_MOVE': EnemyMove('FURIOUS_PUMMELING_MOVE', damage=5, block=0, hits=4, followup=''),
    },
    'SLITHERING_STRANGLER': {
        'CONSTRICT': EnemyMove('CONSTRICT', damage=0, block=0, hits=1, followup=''),
        'THWACK': EnemyMove('THWACK', damage=8, block=0, hits=1, followup='CONSTRICT'),
        'LASH': EnemyMove('LASH', damage=13, block=0, hits=1, followup='CONSTRICT'),
    },
    'SLUDGE_SPINNER': {
        'OIL_SPRAY_MOVE': EnemyMove('OIL_SPRAY_MOVE', damage=9, block=0, hits=1, followup=''),
        'SLAM_MOVE': EnemyMove('SLAM_MOVE', damage=12, block=0, hits=1, followup=''),
        'RAGE_MOVE': EnemyMove('RAGE_MOVE', damage=7, block=0, hits=1, followup=''),
    },
    'SLUMBERING_BEETLE': {
        'SNORE_MOVE': EnemyMove('SNORE_MOVE', damage=0, block=0, hits=1, followup=''),
        'ROLL_OUT_MOVE': EnemyMove('ROLL_OUT_MOVE', damage=18, block=0, hits=1, followup='ROLL_OUT_MOVE'),
    },
    'SNAPPING_JAXFRUIT': {
        'ENERGY_ORB_MOVE': EnemyMove('ENERGY_ORB_MOVE', damage=4, block=0, hits=1, followup='ENERGY_ORB_MOVE'),
    },
    'SNEAKY_GREMLIN': {
        'SPAWNED_MOVE': EnemyMove('SPAWNED_MOVE', damage=0, block=0, hits=1, followup=''),
        'TACKLE_MOVE': EnemyMove('TACKLE_MOVE', damage=10, block=0, hits=1, followup=''),
    },
    'SOUL_FYSH': {
        'BECKON_MOVE': EnemyMove('BECKON_MOVE', damage=0, block=0, hits=1, followup='DE_GAS_MOVE'),
        'DE_GAS_MOVE': EnemyMove('DE_GAS_MOVE', damage=18, block=0, hits=1, followup='GAZE_MOVE'),
        'GAZE_MOVE': EnemyMove('GAZE_MOVE', damage=8, block=0, hits=1, followup='FADE_MOVE'),
        'FADE_MOVE': EnemyMove('FADE_MOVE', damage=0, block=0, hits=1, followup='SCREAM_MOVE'),
        'SCREAM_MOVE': EnemyMove('SCREAM_MOVE', damage=15, block=0, hits=1, followup='BECKON_MOVE'),
    },
    'SOUL_NEXUS': {
        'SOUL_BURN_MOVE': EnemyMove('SOUL_BURN_MOVE', damage=31, block=0, hits=1, followup=''),
        'MAELSTROM_MOVE': EnemyMove('MAELSTROM_MOVE', damage=7, block=0, hits=1, followup=''),
        'DRAIN_LIFE_MOVE': EnemyMove('DRAIN_LIFE_MOVE', damage=19, block=0, hits=1, followup=''),
    },
    'SPECTRAL_KNIGHT': {
        'HEX': EnemyMove('HEX', damage=0, block=0, hits=1, followup='SOUL_SLASH'),
        'SOUL_SLASH': EnemyMove('SOUL_SLASH', damage=17, block=0, hits=1, followup=''),
        'SOUL_FLAME': EnemyMove('SOUL_FLAME', damage=4, block=0, hits=3, followup=''),
    },
    'SPINY_TOAD': {
        'PROTRUDING_SPIKES_MOVE': EnemyMove('PROTRUDING_SPIKES_MOVE', damage=0, block=0, hits=1, followup='SPIKE_EXPLOSION_MOVE'),
        'SPIKE_EXPLOSION_MOVE': EnemyMove('SPIKE_EXPLOSION_MOVE', damage=25, block=0, hits=1, followup='TONGUE_LASH_MOVE'),
        'TONGUE_LASH_MOVE': EnemyMove('TONGUE_LASH_MOVE', damage=19, block=0, hits=1, followup='PROTRUDING_SPIKES_MOVE'),
    },
    'STABBOT': {
        'STAB_MOVE': EnemyMove('STAB_MOVE', damage=12, block=0, hits=1, followup='STAB_MOVE'),
    },
    'TEN_HP_MONSTER': {
        'NOTHING': EnemyMove('NOTHING', damage=0, block=0, hits=1, followup='NOTHING'),
    },
    'TERROR_EEL': {
        'CRASH_MOVE': EnemyMove('CRASH_MOVE', damage=18, block=0, hits=1, followup='THRASH_MOVE'),
        'THRASH_MOVE': EnemyMove('THRASH_MOVE', damage=4, block=0, hits=1, followup='CRASH_MOVE'),
        'STUN_MOVE': EnemyMove('STUN_MOVE', damage=0, block=0, hits=1, followup='TERROR_MOVE'),
        'TERROR_MOVE': EnemyMove('TERROR_MOVE', damage=0, block=0, hits=1, followup='CRASH_MOVE'),
    },
    'TEST_SUBJECT': {
        'RESPAWN_MOVE': EnemyMove('RESPAWN_MOVE', damage=0, block=0, hits=1, followup=''),
        'BITE_MOVE': EnemyMove('BITE_MOVE', damage=22, block=0, hits=1, followup='SKULL_BASH_MOVE'),
        'SKULL_BASH_MOVE': EnemyMove('SKULL_BASH_MOVE', damage=16, block=0, hits=1, followup='BITE_MOVE'),
        'PHASE3_LACERATE_MOVE': EnemyMove('PHASE3_LACERATE_MOVE', damage=11, block=0, hits=3, followup='BIG_POUNCE'),
        'BIG_POUNCE': EnemyMove('BIG_POUNCE', damage=0, block=0, hits=1, followup='BURNING_GROWL_MOVE'),
        'BURNING_GROWL_MOVE': EnemyMove('BURNING_GROWL_MOVE', damage=0, block=0, hits=1, followup='PHASE3_LACERATE_MOVE'),
    },
    'THE_ADVERSARY_MK_ONE': {
        'SMASH_MOVE': EnemyMove('SMASH_MOVE', damage=0, block=0, hits=1, followup='BEAM_MOVE'),
        'BEAM_MOVE': EnemyMove('BEAM_MOVE', damage=0, block=0, hits=1, followup='BARRAGE_MOVE'),
        'BARRAGE_MOVE': EnemyMove('BARRAGE_MOVE', damage=0, block=0, hits=1, followup='SMASH_MOVE'),
    },
    'THE_ADVERSARY_MK_THREE': {
        'CRASH_MOVE': EnemyMove('CRASH_MOVE', damage=0, block=0, hits=1, followup='FLAME_BEAM_MOVE'),
        'FLAME_BEAM_MOVE': EnemyMove('FLAME_BEAM_MOVE', damage=0, block=0, hits=1, followup='BARRAGE_MOVE'),
        'BARRAGE_MOVE': EnemyMove('BARRAGE_MOVE', damage=0, block=0, hits=1, followup='CRASH_MOVE'),
    },
    'THE_ADVERSARY_MK_TWO': {
        'BASH_MOVE': EnemyMove('BASH_MOVE', damage=0, block=0, hits=1, followup='FLAME_BEAM_MOVE'),
        'FLAME_BEAM_MOVE': EnemyMove('FLAME_BEAM_MOVE', damage=0, block=0, hits=1, followup='BARRAGE_MOVE'),
        'BARRAGE_MOVE': EnemyMove('BARRAGE_MOVE', damage=0, block=0, hits=1, followup='BASH_MOVE'),
    },
    'THE_FORGOTTEN': {
        'MIASMA': EnemyMove('MIASMA', damage=0, block=0, hits=1, followup=''),
    },
    'THE_INSATIABLE': {
        'LIQUIFY_GROUND_MOVE': EnemyMove('LIQUIFY_GROUND_MOVE', damage=0, block=0, hits=1, followup='THRASH_MOVE'),
        'THRASH_MOVE': EnemyMove('THRASH_MOVE', damage=9, block=0, hits=2, followup='LUNGING_BITE_MOVE'),
        'THRASH_MOVE_2': EnemyMove('THRASH_MOVE_2', damage=9, block=0, hits=2, followup='THRASH_MOVE'),
        'LUNGING_BITE_MOVE': EnemyMove('LUNGING_BITE_MOVE', damage=31, block=0, hits=1, followup='SALIVATE_MOVE'),
        'SALIVATE_MOVE': EnemyMove('SALIVATE_MOVE', damage=0, block=0, hits=1, followup='THRASH_MOVE_2'),
    },
    'THE_LOST': {
        'DEBILITATING_SMOG': EnemyMove('DEBILITATING_SMOG', damage=0, block=0, hits=1, followup=''),
        'EYE_LASERS': EnemyMove('EYE_LASERS', damage=5, block=0, hits=2, followup=''),
    },
    'THE_OBSCURA': {
        'ILLUSION_MOVE': EnemyMove('ILLUSION_MOVE', damage=0, block=0, hits=1, followup=''),
        'PIERCING_GAZE_MOVE': EnemyMove('PIERCING_GAZE_MOVE', damage=11, block=0, hits=1, followup=''),
        'SAIL_MOVE': EnemyMove('SAIL_MOVE', damage=0, block=0, hits=1, followup=''),
        'HARDENING_STRIKE_MOVE': EnemyMove('HARDENING_STRIKE_MOVE', damage=7, block=0, hits=1, followup=''),
    },
    'THIEVING_HOPPER': {
        'THIEVERY_MOVE': EnemyMove('THIEVERY_MOVE', damage=19, block=0, hits=1, followup='FLUTTER_MOVE'),
        'NAB_MOVE': EnemyMove('NAB_MOVE', damage=16, block=0, hits=1, followup='ESCAPE_MOVE'),
        'HAT_TRICK_MOVE': EnemyMove('HAT_TRICK_MOVE', damage=23, block=0, hits=1, followup='NAB_MOVE'),
        'FLUTTER_MOVE': EnemyMove('FLUTTER_MOVE', damage=0, block=0, hits=1, followup='HAT_TRICK_MOVE'),
        'ESCAPE_MOVE': EnemyMove('ESCAPE_MOVE', damage=0, block=0, hits=1, followup='ESCAPE_MOVE'),
    },
    'TOADPOLE': {
        'SPIKE_SPIT_MOVE': EnemyMove('SPIKE_SPIT_MOVE', damage=4, block=0, hits=1, followup='WHIRL_MOVE'),
        'WHIRL_MOVE': EnemyMove('WHIRL_MOVE', damage=8, block=0, hits=1, followup='SPIKEN_MOVE'),
        'SPIKEN_MOVE': EnemyMove('SPIKEN_MOVE', damage=0, block=0, hits=1, followup='SPIKE_SPIT_MOVE'),
    },
    'TORCH_HEAD_AMALGAM': {
        'STRONG_TACKLE_MOVE': EnemyMove('STRONG_TACKLE_MOVE', damage=32, block=0, hits=1, followup='TACKLE_2_MOVE'),
        'TACKLE_2_MOVE': EnemyMove('TACKLE_2_MOVE', damage=22, block=0, hits=1, followup='BEAM_MOVE'),
        'BEAM_MOVE': EnemyMove('BEAM_MOVE', damage=8, block=0, hits=3, followup='TACKLE_3_MOVE'),
        'TACKLE_3_MOVE': EnemyMove('TACKLE_3_MOVE', damage=16, block=0, hits=1, followup='TACKLE_4_MOVE'),
        'TACKLE_4_MOVE': EnemyMove('TACKLE_4_MOVE', damage=16, block=0, hits=1, followup='BEAM_MOVE'),
    },
    'TOUGH_EGG': {
        'HATCH_MOVE': EnemyMove('HATCH_MOVE', damage=0, block=0, hits=1, followup=''),
        'NIBBLE_MOVE': EnemyMove('NIBBLE_MOVE', damage=0, block=0, hits=1, followup=''),
    },
    'TRACKER_RUBY_RAIDER': {
        'TRACK_MOVE': EnemyMove('TRACK_MOVE', damage=0, block=0, hits=1, followup=''),
        'HOUNDS_MOVE': EnemyMove('HOUNDS_MOVE', damage=1, block=0, hits=1, followup=''),
    },
    'TUNNELER': {
        'BITE_MOVE': EnemyMove('BITE_MOVE', damage=15, block=0, hits=1, followup='BURROW_MOVE'),
        'BURROW_MOVE': EnemyMove('BURROW_MOVE', damage=0, block=0, hits=1, followup='BELOW_MOVE'),
        'BELOW_MOVE': EnemyMove('BELOW_MOVE', damage=26, block=0, hits=1, followup='BELOW_MOVE'),
        'DIZZY_MOVE': EnemyMove('DIZZY_MOVE', damage=0, block=0, hits=1, followup='BITE_MOVE'),
    },
    'TURRET_OPERATOR': {
        'UNLOAD_MOVE': EnemyMove('UNLOAD_MOVE', damage=4, block=0, hits=5, followup='UNLOAD_MOVE_2'),
        'UNLOAD_MOVE_2': EnemyMove('UNLOAD_MOVE_2', damage=4, block=0, hits=5, followup='RELOAD_MOVE'),
        'RELOAD_MOVE': EnemyMove('RELOAD_MOVE', damage=0, block=0, hits=1, followup='UNLOAD_MOVE'),
    },
    'TWIG_SLIME_M': {
        'POKEY_POUNCE_MOVE': EnemyMove('POKEY_POUNCE_MOVE', damage=12, block=0, hits=1, followup=''),
        'STICKY_SHOT_MOVE': EnemyMove('STICKY_SHOT_MOVE', damage=0, block=0, hits=1, followup=''),
    },
    'TWIG_SLIME_S': {
        'TACKLE_MOVE': EnemyMove('TACKLE_MOVE', damage=5, block=0, hits=1, followup='TACKLE_MOVE'),
    },
    'TWO_TAILED_RAT': {
        'SCRATCH_MOVE': EnemyMove('SCRATCH_MOVE', damage=9, block=0, hits=1, followup=''),
        'DISEASE_BITE_MOVE': EnemyMove('DISEASE_BITE_MOVE', damage=7, block=0, hits=1, followup=''),
        'SCREECH_MOVE': EnemyMove('SCREECH_MOVE', damage=0, block=0, hits=1, followup=''),
        'CALL_FOR_BACKUP_MOVE': EnemyMove('CALL_FOR_BACKUP_MOVE', damage=0, block=0, hits=1, followup=''),
    },
    'VANTOM': {
        'INK_BLOT_MOVE': EnemyMove('INK_BLOT_MOVE', damage=8, block=0, hits=1, followup='INKY_LANCE_MOVE'),
        'INKY_LANCE_MOVE': EnemyMove('INKY_LANCE_MOVE', damage=7, block=0, hits=2, followup='DISMEMBER_MOVE'),
        'DISMEMBER_MOVE': EnemyMove('DISMEMBER_MOVE', damage=30, block=0, hits=1, followup='PREPARE_MOVE'),
        'PREPARE_MOVE': EnemyMove('PREPARE_MOVE', damage=0, block=0, hits=1, followup='INK_BLOT_MOVE'),
    },
    'VINE_SHAMBLER': {
        'GRASPING_VINES_MOVE': EnemyMove('GRASPING_VINES_MOVE', damage=9, block=0, hits=1, followup='CHOMP_MOVE'),
        'SWIPE_MOVE': EnemyMove('SWIPE_MOVE', damage=7, block=0, hits=2, followup='GRASPING_VINES_MOVE'),
        'CHOMP_MOVE': EnemyMove('CHOMP_MOVE', damage=18, block=0, hits=1, followup='SWIPE_MOVE'),
    },
    'WATERFALL_GIANT': {
        'PRESSURIZE_MOVE': EnemyMove('PRESSURIZE_MOVE', damage=0, block=0, hits=1, followup='STOMP_MOVE'),
        'STOMP_MOVE': EnemyMove('STOMP_MOVE', damage=16, block=0, hits=1, followup='RAM_MOVE'),
        'RAM_MOVE': EnemyMove('RAM_MOVE', damage=11, block=0, hits=1, followup='SIPHON_MOVE'),
        'SIPHON_MOVE': EnemyMove('SIPHON_MOVE', damage=0, block=0, hits=1, followup=''),
        'PRESSURE_UP_MOVE': EnemyMove('PRESSURE_UP_MOVE', damage=14, block=0, hits=1, followup='STOMP_MOVE'),
        'ABOUT_TO_BLOW_MOVE': EnemyMove('ABOUT_TO_BLOW_MOVE', damage=0, block=0, hits=1, followup=''),
    },
    'WRIGGLER': {
        'NASTY_BITE_MOVE': EnemyMove('NASTY_BITE_MOVE', damage=7, block=0, hits=1, followup='WRIGGLE_MOVE'),
        'WRIGGLE_MOVE': EnemyMove('WRIGGLE_MOVE', damage=0, block=0, hits=1, followup='NASTY_BITE_MOVE'),
        'SPAWNED_MOVE': EnemyMove('SPAWNED_MOVE', damage=0, block=0, hits=1, followup=''),
    },
    'ZAPBOT': {
        'ZAP': EnemyMove('ZAP', damage=15, block=0, hits=1, followup='ZAP'),
    },
}