# Reference moving-average strategy plugin

Offline proof of the ainvest Strategy API (`P03-T0`–`P03-T3`).

## Behavior

- Parameters: `fast_window`, `slow_window`, `target_weight`
- Reads SMAs from `context.research.technical` (`sma_20` / `sma_50` for the
  default windows) and prior crossover state from `context.strategy_state`
- Uses only `context.as_of` for timestamps — never `datetime.now()`
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

## Install (editable)

From the repository root (or via the ainvest `examples` dependency group):

```bash
uv pip install -e examples/strategies/moving_average
```

Entry point group: `ainvest.strategies` → `moving_average`.
