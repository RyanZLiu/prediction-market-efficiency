# Project Notes

## Best next research extensions

1. Build a settlement-rule verifier before accepting automatic matches.
2. Add exact venue fee functions by market rather than using constant bps assumptions.
3. Resample WebSocket quotes to a common clock for lead/lag inference.
4. Measure quote staleness and reject pairs farther apart than a chosen latency tolerance.
5. Store full depth and simulate walking the book for larger position sizes.
6. Add threshold-market parsers so monotonic probability constraints can be discovered automatically.
7. Add event-graph YAML generation for subset/complement relationships.
8. Add resolved outcomes and calibration analysis.
9. Add a sportsbook adapter only after the core prediction-market study is stable.
