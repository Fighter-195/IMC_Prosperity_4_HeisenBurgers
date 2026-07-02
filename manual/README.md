# Manual Challenge — Capital Allocation Optimization

> **This is where Team HeisenBurgers placed #6 in the world.** No code — a pure
> constrained-optimization problem, solved analytically on paper.

## The problem

We were given a fixed investment budget of **50,000** and had to split it across
three **multiplicative** pillars, each transforming budget into a return
multiplier applied to our trading PnL:

| Pillar | Return shape | Definition |
|---|---|---|
| **Research** | logarithmic (concave, **diminishing returns**) | `Research(x) = 200,000 · ln(1 + x) / ln(101)` |
| **Scale** | **linear** | scales `0 → 7` as investment goes `0% → 100%` of budget |
| **Speed** | rank-based hit rate | traders ranked on a `[0.1, 0.9]` hit-rate scale, linearly interpolated by rank |

The pillars **multiply**, and the budget is then recovered:

```
Gross PnL = Research · Scale · Speed · PnL
Net PnL   = Gross PnL − 50,000
```

## Why it's a real optimization (not just "spend on the best one")

The trap is over-investing in **Research**: because it grows logarithmically, its
**marginal return collapses** — doubling investment from 10k → 20k lifts projected
returns by only **~7.5%**. Meanwhile **Scale** pays back linearly. And since the
three pillars enter as a **product**, the objective is maximized not by dumping
capital into any single pillar but by **balancing the marginal return of each
pillar** — the classic equal-marginal-return / Lagrange-multiplier condition for
allocating a fixed budget across a concave-plus-linear objective.

Concretely: push the last rupee wherever its marginal `∂(Net)/∂(allocation)` is
highest, until all three marginals equalize. That naturally **caps** the concave
Research pillar early and pushes the remainder into the pillars that keep paying.

## Our allocation

Solving the marginal-balance condition by hand, we split the 50,000 budget as:

| Pillar | Allocation | Rationale |
|---|---:|---|
| **Research** | **~14%** | deliberately **under-weighted** — its marginal return dies fast; extra capital here is wasted |
| **Scale** | **~44%** | heaviest weight — linear payoff keeps compounding the multiplier |
| **Speed** | **~42%** | near-equal to Scale — protects hit rate so a profitable strategy actually gets its trades filled |

The signature of a correct solution is exactly this shape: **least into the
diminishing-returns pillar, most into the linear/rank pillars** — the opposite of
the naive "Research sounds most valuable, fund it fully" instinct that sinks most
teams.

## Result

**Global Rank #6 in Manual Trading**, out of **18,000+ teams** — the team's
single best placement, driven entirely by getting this allocation right.
