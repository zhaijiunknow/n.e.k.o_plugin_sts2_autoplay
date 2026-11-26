using MegaCrit.Sts2.Core.Combat;
using MegaCrit.Sts2.Core.Entities.Cards;
using MegaCrit.Sts2.Core.Entities.FoolArrows;
using MegaCrit.Sts2.Core.Entities.Players;
using MegaCrit.Sts2.Core.Models.Powers;
using NekoComm.Game;

int assertions = 0;
void Check(bool condition, string message)
{
    assertions++;
    if (!condition) throw new Exception(message);
}
var card = new MegaCrit.Sts2.Core.Models.CardModel();
GameApiCompat.UpgradeInternal(card);
Check(card.UpgradeCount == 1, "upgrade uses the runtime signature exactly once");
Check(!card.Silent, "upgrade preserves the non-silent default");
var arrow = new ArrowSlot();
Check(!GameApiCompat.ArrowDestroyAfterImpaled(arrow), "unmarked arrow must survive");
Check(!GameApiCompat.IsDestroyedArrow(ArrowStatus.Away), "away is not destroyed");
Check(!GameApiCompat.IsDestroyedArrow(ArrowStatus.Retrieved), "retrieved is not destroyed");
Check(!GameApiCompat.IsFreeCardPower(new FreeAttackPower()), "typed free power is not general free power");
#if LEGACY_API
Check(GameApiCompat.SupportsArrowDestruction, "pinned game supports destruction");
arrow.DestroyAfterImpaled = true;
Check(GameApiCompat.ArrowDestroyAfterImpaled(arrow), "preserve pinned destroy flag");
Check(GameApiCompat.IsDestroyedArrow(ArrowStatus.Destroyed), "recognize pinned enum");
Check(GameApiCompat.DestroyedArrowStatus == ArrowStatus.Destroyed, "preserve numeric enum value");
Check(GameApiCompat.IsFreeCardPower(new FreeCardPower()), "preserve pinned free-card power");
#else
Check(!GameApiCompat.SupportsArrowDestruction, "installed API cannot destroy arrows");
Check(!GameApiCompat.IsDestroyedArrow((ArrowStatus)15), "sentinel must not be a real destroyed arrow");
#endif
var me = new Player();
var teammate = new Player();
var history = CombatManager.Instance.History;
Check(GameApiCompat.MelodyCardsPlayedThisTurn(me) == 0, "empty history");
Entry Play(Player who, int turn, params CardTag[] tags)
{
    var play = new CardPlay { Player = who };
    play.Card.Tags.UnionWith(tags);
    return new Entry { CardPlay = play, Turn = turn };
}
var multi = Play(me, 2, CardTag.Wah, CardTag.Cho);
history.CardPlaysFinished.Add(multi);
Check(GameApiCompat.MelodyCardsPlayedThisTurn(me) == 1, "multi-tag card counts once");
history.CardPlaysFinished.Add(multi);
Check(GameApiCompat.MelodyCardsPlayedThisTurn(me) == 2, "replay counts again");
history.CardPlaysFinished.Add(Play(teammate, 2, CardTag.Ki));
history.CardPlaysFinished.Add(Play(me, 1, CardTag.Ki));
history.CardPlaysFinished.Add(Play(me, 2, CardTag.Shoot));
history.CardPlaysStarted.Add(Play(me, 2, CardTag.Ki));
Check(GameApiCompat.MelodyCardsPlayedThisTurn(me) == 2,
    "exclude teammate, previous turn, non-melody and unfinished plays");
history.CardPlaysFinished.Add(Play(me, 2, CardTag.Ki));
Check(GameApiCompat.MelodyCardsPlayedThisTurn(me) == 3, "all melody tags recognized");
Console.WriteLine($"ALL PASS ({assertions} compatibility assertions)");
