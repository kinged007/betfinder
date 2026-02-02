
from typing import Optional

class EdgeCalculator:
    @staticmethod
    def calculate_implied_probability(odds: float) -> float:
        if odds <= 0:
            return 0.0
        return 1 / odds

    @staticmethod
    def calculate_edge(odds: float, benchmark_prob: float) -> float:
        """
        Calculate edge (value) based on odds and true probability (benchmark).
        Edge = (Probability * Odds) - 1
        """
        if benchmark_prob <= 0:
            return 0.0
        
        return (benchmark_prob * odds) - 1

    @staticmethod
    def remove_vig(odds_list: list[float], method: str = "multiplicative") -> list[float]:
        """
        Remove vigorish (margin) from a set of odds to estimate true probabilities.
        Simple multiplicative method: Normalize probs to sum to 1.
        """
        probs = [1/o for o in odds_list if o > 0]
        total_implied_prob = sum(probs)
        
        if total_implied_prob == 0:
            return [0.0] * len(odds_list)
            
        true_probs = [p / total_implied_prob for p in probs]
        return true_probs
