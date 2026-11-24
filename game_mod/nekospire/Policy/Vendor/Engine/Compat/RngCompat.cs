using System.Collections.Generic;
using MegaCrit.Sts2.Core.Random;

namespace Sts2Headless;

/// <summary>
/// v0.113.0 删掉了 Rng.Shuffle（游戏改用 ListExtensions.StableShuffle / UnstableShuffle，二者都先从 rng 抽一个新种子再洗）。
/// 引擎自己的分支与重新抽签（Branch.cs、RunSession 的 MockRng 之后打乱抽牌堆）不是复现游戏行为，只要求同一种子结果相同，
/// 所以照旧版 Rng.Shuffle 原样实现：从末尾往前，每步 NextInt(i + 1) 与第 i 个交换，抽数次数与顺序同旧版，同一种子的结果与 v0.111.0 相同。
/// </summary>
public static class RngCompat
{
    public static void Shuffle<T>(this Rng rng, IList<T> list)
    {
        for (int i = list.Count - 1; i > 0; i--)
        {
            int j = rng.NextInt(i + 1);
            (list[i], list[j]) = (list[j], list[i]);
        }
    }
}
