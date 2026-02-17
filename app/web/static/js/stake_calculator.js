// Stake Calculator for BetFinder
// Calculates stake amounts based on different staking strategies

function calculateStake(
    strategy,
    defaultStake,
    bankroll,
    probability,
    odds,
    percentRisk,
    kellyMultiplier,
    maxStake
) {
    let stake = 0.0;

    if (strategy === 'fixed') {
        stake = defaultStake || 10.0;
    }
    else if (strategy === 'risk') {
        if (!percentRisk) {
            console.warn("Percent risk not provided for 'risk' strategy, using 10%");
            percentRisk = 10.0;
        }
        // Calculate stake as percentage of bankroll
        stake = bankroll * (percentRisk / 100.0);
    }
    else if (strategy === 'kelly') {
        if (!probability || !odds) {
            console.error("Probability and odds are required for Kelly strategy, falling back to fixed");
            stake = defaultStake || 10.0;
        } else {
            if (!kellyMultiplier) {
                console.warn("Kelly multiplier not provided, using 1.0");
                kellyMultiplier = 1.0;
            }

            // Kelly Criterion: f = (bp - q) / b
            // where:
            //   f = fraction of bankroll to bet
            //   b = decimal odds - 1 (net odds)
            //   p = probability of winning (as decimal, e.g., 0.5 for 50%)
            //   q = probability of losing (1 - p)

            const b = odds - 1.0;  // Net odds
            const p = probability;  // Already in decimal form
            const q = 1.0 - p;

            // Calculate Kelly fraction
            let kellyFraction = (b * p - q) / b;

            // Apply multiplier to reduce volatility
            kellyFraction = kellyFraction * kellyMultiplier;

            // Kelly fraction should be between 0 and 1
            // Negative kelly means no edge, don't bet
            if (kellyFraction <= 0) {
                console.warn(`Kelly fraction is ${kellyFraction.toFixed(4)} (negative or zero), setting stake to 0`);
                stake = 0.0;
            } else if (kellyFraction > 1) {
                console.warn(`Kelly fraction is ${kellyFraction.toFixed(4)} (>1), capping at 1.0`);
                kellyFraction = 1.0;
                stake = bankroll * kellyFraction;
            } else {
                stake = bankroll * kellyFraction;
            }
        }
    } else {
        console.error(`Unknown staking strategy: ${strategy}, using fixed`);
        stake = defaultStake || 10.0;
    }

    // Apply max stake cap if provided
    if (maxStake !== null && maxStake !== undefined && stake > maxStake) {
        console.info(`Calculated stake ${stake.toFixed(2)} exceeds max_stake ${maxStake.toFixed(2)}, capping at max`);
        stake = maxStake;
    }

    // Ensure stake is at least 0
    stake = Math.max(0.0, stake);

    // Round to 2 decimal places
    stake = Math.round(stake * 100) / 100;

    console.debug(`Calculated stake: ${stake.toFixed(2)} using strategy '${strategy}'`);
    return stake;
}
