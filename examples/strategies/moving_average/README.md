# Reference moving-average strategy plugin

Offline proof of the ainvest Strategy API (`P03-T0`–`P03-T3`).

The implementation shipped with ainvest lives at
`src/ainvest/strategies/reference/moving_average/` and is registered under the
`ainvest.strategies` entry-point group as `moving_average`.

This directory is a **packaging sample** for third-party teams: it shows the
`pyproject.toml` entry-point shape and re-exports the reference implementation.

## Behavior

- Parameters: `fast_window`, `slow_window`, `target_weight`
- Reads SMAs from `context.research.technical` (`sma_20` / `sma_50` for the
  default windows) and prior crossover state from `context.strategy_state`
- Uses only `context.as_of` for timestamps — never the system clock
- Emits `BUY` / `SELL` / `HOLD` intents with stable reason codes
- No network, broker credentials, or order submission

## Reason codes

| Code | Meaning |
|------|---------|
| `SMA_FAST_CROSSED_ABOVE_SLOW` | Fast SMA crossed above slow → BUY |
| `SMA_FAST_CROSSED_BELOW_SLOW` | Fast SMA crossed below slow → SELL |
| `SMA_NO_CROSS` | Relationship unchanged → HOLD |
| `SMA_RELATIONSHIP_INITIALIZED` | First observation, no prior state → HOLD |
| `INSUFFICIENT_DATA` | Required SMA missing → HOLD |

## Third-party packaging sample

```toml
[project.entry-points."ainvest.strategies"]
moving_average = "ainvest_ma.plugin:plugin"
```
