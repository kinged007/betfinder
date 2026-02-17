// Stake Calculator for BetFinder
// Calculates stake amounts based on different staking strategies

function calculateStakeDetails(
    strategyOrConfig,
    defaultStake,
    bankroll,
    probability,
    odds,
    percentRisk,
    kellyMultiplier,
    maxStake
) {
    // Support object argument
    let strategy = strategyOrConfig;
    if (typeof strategyOrConfig === 'object' && strategyOrConfig !== null) {
        const config = strategyOrConfig;
        strategy = config.strategy;
        defaultStake = config.defaultStake;
        bankroll = config.bankroll;
        probability = config.probability;
        odds = config.odds;
        percentRisk = config.percentRisk;
        kellyMultiplier = config.kellyMultiplier;
        maxStake = config.maxStake;
    }
    let stake = 0.0;
    let details = {
        risk_pct: 0,
        ev_pct: 0,
        port_pct: 0,
        strategy: strategy,
        bankroll: bankroll
    };

    // Calculate EV% (Edge)
    if (probability && odds) {
        details.ev_pct = ((probability * odds) - 1) * 100;
    }

    if (strategy === 'fixed') {
        stake = defaultStake || 10.0;
    }
    else if (strategy === 'risk') {
        if (percentRisk === undefined || percentRisk === null) {
            console.warn("Percent risk not provided for 'risk' strategy, using 1.0%");
            percentRisk = 1.0;
        }
        details.risk_pct = parseFloat(percentRisk);

        // Calculate stake as percentage of bankroll
        stake = bankroll * (details.risk_pct / 100.0);
    }
    else if (strategy === 'kelly') {
        if (!probability || !odds) {
            console.error("Probability and odds are required for Kelly strategy, falling back to fixed");
            stake = defaultStake || 10.0;
        } else {
            if (kellyMultiplier === undefined || kellyMultiplier === null) {
                console.warn("Kelly multiplier not provided, using 1.0");
                kellyMultiplier = 1.0;
            }

            // Kelly Criterion: f = (bp - q) / b
            const b = odds - 1.0;  // Net odds
            const p = probability;  // Probability of winning
            const q = 1.0 - p;      // Probability of losing

            // Calculate Kelly fraction
            let kellyFraction = (b * p - q) / b;

            // Store raw Kelly fraction as risk percentage (before multiplier)
            details.risk_pct = kellyFraction * 100;

            // Apply multiplier
            kellyFraction = kellyFraction * kellyMultiplier;

            // Kelly fraction should be between 0 and 1
            if (kellyFraction <= 0) {
                console.log(`Kelly fraction is ${kellyFraction.toFixed(4)} (negative or zero), setting stake to 0`);
                stake = 0.0;
            } else if (kellyFraction > 1) {
                console.log(`Kelly fraction is ${kellyFraction.toFixed(4)} (>1), capping at 1.0`);
                stake = bankroll; // Cap at bankroll (100%)
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

    // Final calculation of portfolio % based on the actual stake
    if (bankroll > 0) {
        details.port_pct = (stake / bankroll) * 100;
    }

    // Round numbers for display
    stake = Math.round(stake * 100) / 100;
    details.risk_pct = Math.round(details.risk_pct * 100) / 100;
    details.ev_pct = Math.round(details.ev_pct * 100) / 100;
    details.port_pct = Math.round(details.port_pct * 100) / 100;

    return { stake, details };
}

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
    const result = calculateStakeDetails(
        strategy,
        defaultStake,
        bankroll,
        probability,
        odds,
        percentRisk,
        kellyMultiplier,
        maxStake
    );
    return result.stake;
}
