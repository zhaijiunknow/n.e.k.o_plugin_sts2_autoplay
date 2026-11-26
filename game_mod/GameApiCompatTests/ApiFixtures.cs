// Minimal game API fixtures: compile both with and without the removed members/types.
namespace MegaCrit.Sts2.Core.Entities.FoolArrows
{
    public enum ArrowStatus { Retrieved, Away,
#if LEGACY_API
        Destroyed,
#endif
    }
    public class ArrowSlot
    {
#if LEGACY_API
        public bool DestroyAfterImpaled { get; set; }
#endif
    }
}
namespace MegaCrit.Sts2.Core.Models
{
    public class AbstractModel;
    public class PowerModel : AbstractModel;
    public class CardModel : AbstractModel
    {
        public int UpgradeCount { get; private set; }
        public bool Silent { get; private set; }
#if LEGACY_API
        public void UpgradeInternal() { UpgradeCount++; }
#else
        public void UpgradeInternal(bool silent = false) { UpgradeCount++; Silent = silent; }
#endif
    }
}
namespace MegaCrit.Sts2.Core.Models.Powers
{
    public class FreeAttackPower : PowerModel;
#if LEGACY_API
    public class FreeCardPower : PowerModel;
#endif
}
namespace MegaCrit.Sts2.Core.Entities.Cards
{
    public enum CardTag { Wah, Cho, Ki, Shoot }
    public class Card { public HashSet<CardTag> Tags { get; } = []; }
    public class CardPlay
    {
        public required Players.Player Player { get; init; }
        public Card Card { get; } = new();
    }
}
namespace MegaCrit.Sts2.Core.Entities.Players
{
    public class Player { public Creature Creature { get; } = new(); }
    public class Creature { public CombatState CombatState { get; } = new(); }
    public class CombatState { public int Turn { get; set; } = 2; }
}
namespace MegaCrit.Sts2.Core.Combat
{
    public class CombatManager
    {
        public static CombatManager Instance { get; } = new();
        public History History { get; } = new();
    }
    public class History
    {
        public List<Entry> CardPlaysFinished { get; } = [];
        public List<Entry> CardPlaysStarted { get; } = [];
    }
    public class Entry
    {
        public required Entities.Cards.CardPlay CardPlay { get; init; }
        public int Turn { get; init; } = 2;
        public bool HappenedThisTurn(Entities.Players.CombatState state) => Turn == state.Turn;
    }
}
