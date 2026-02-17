"""
Stake Calculator Service

Handles calculation of stake amounts based on different staking strategies.
"""

from typing import Optional
import logging
import math

logger = logging.getLogger(__name__)


class StakeCalculator:
    """Calculate stake amounts based on different staking strategies."""
    
    @staticmethod
    def calculate_stake(
        strategy: str,
        default_stake: float,
        bankroll: float,
        probability: Optional[float] = None,
        odds: Optional[float] = None,
        percent_risk: Optional[float] = None,
        kelly_multiplier: Optional[float] = None,
        max_stake: Optional[float] = None
    ) -> float:
        """
        Calculate stake based on the specified strategy.
        
        Args:
            strategy: The staking strategy to use ('fixed', 'risk', 'kelly')
            default_stake: Default stake amount (used for 'fixed' strategy)
            bankroll: Available balance/bankroll for the bookmaker
            probability: True probability of the outcome (required for 'kelly')
            odds: Decimal odds (required for 'kelly')
            percent_risk: Percentage of bankroll to risk (required for 'risk' strategy)
            kelly_multiplier: Multiplier to apply to Kelly criterion (required for 'kelly')
            max_stake: Maximum stake amount (optional cap for 'risk' and 'kelly')
            
        Returns:
            Calculated stake amount
        """
        stake = 0.0
        
        if strategy == "fixed":
            stake = default_stake or 10.0
            
        elif strategy == "risk":
            if percent_risk is None:
                logger.warning("Percent risk not provided for 'risk' strategy, using 10%")
                percent_risk = 10.0
            
            # Calculate stake as percentage of bankroll
            stake = bankroll * (percent_risk / 100.0)
            
        elif strategy == "kelly":
            if probability is None or odds is None:
                logger.error("Probability and odds are required for Kelly strategy, falling back to fixed")
                stake = default_stake or 10.0
            else:
                if kelly_multiplier is None:
                    logger.warning("Kelly multiplier not provided, using 1.0")
                    kelly_multiplier = 1.0
                
                # Kelly Criterion: f = (bp - q) / b
                # where:
                #   f = fraction of bankroll to bet
                #   b = decimal odds - 1 (net odds)
                #   p = probability of winning
                #   q = probability of losing (1 - p)
                
                b = odds - 1.0  # Net odds
                p = probability
                q = 1.0 - p
                
                # Calculate Kelly fraction
                kelly_fraction = (b * p - q) / b
                
                # Apply multiplier to reduce volatility
                kelly_fraction = kelly_fraction * kelly_multiplier
                
                # Kelly fraction should be between 0 and 1
                # Negative kelly means no edge, don't bet
                if kelly_fraction <= 0:
                    logger.debug(f"Kelly fraction is {kelly_fraction:.4f} (negative or zero), setting stake to minimum")
                    stake = 0.0
                elif kelly_fraction > 1:
                    logger.debug(f"Kelly fraction is {kelly_fraction:.4f} (>1), capping at 1.0")
                    kelly_fraction = 1.0
                    stake = bankroll * kelly_fraction
                else:
                    stake = bankroll * kelly_fraction
        else:
            logger.error(f"Unknown staking strategy: {strategy}, using fixed")
            stake = default_stake or 10.0
        
        # Apply max stake cap if provided
        if max_stake is not None and stake > max_stake:
            logger.info(f"Calculated stake {stake:.2f} exceeds max_stake {max_stake:.2f}, capping at max")
            stake = max_stake
        
        # Ensure stake is at least 0
        stake = max(0.0, stake)
        
        # Round to 2 decimal places
        stake = round(stake, 2)
        
        logger.debug(f"Calculated stake: {stake:.2f} using strategy '{strategy}'")
        return stake
