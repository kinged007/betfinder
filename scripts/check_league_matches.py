import asyncio
import sys
import os
import difflib

sys.path.append(os.getcwd())

from app.db.session import AsyncSessionLocal
from app.services.bookmakers.sx_bet import SXBetBookmaker
from app.db.models import League
from sqlalchemy import select

def simple_ratio(s1, s2):
    return difflib.SequenceMatcher(None, s1.lower(), s2.lower()).ratio()

def tokenize(s):
    import re
    return [t for t in re.split(r'[^a-zA-Z0-9]+', s.lower()) if t]

COUNTRY_SYNONYMS = {
    "dutch": "netherlands",
    "french": "france",
    "german": "germany",
    "spanish": "spain",
    "italian": "italy",
    "english": "england",
    "portuguese": "portugal",
    "brazilian": "brazil",
    "russian": "russia",
    "belgian": "belgium",
    "american": "usa",
}

def normalize_title(s):
    tokens = tokenize(s)
    normalized = []
    for t in tokens:
        normalized.append(COUNTRY_SYNONYMS.get(t, t))
    return " ".join(normalized)

def token_sort_ratio(s1, s2):
    s1 = normalize_title(s1)
    s2 = normalize_title(s2)
    t1 = tokenize(s1)
    t2 = tokenize(s2)
    t1.sort()
    t2.sort()
    return difflib.SequenceMatcher(None, " ".join(t1), " ".join(t2)).ratio()

def token_set_ratio(s1, s2):
    s1 = normalize_title(s1)
    s2 = normalize_title(s2)
    t1 = set(tokenize(s1))
    t2 = set(tokenize(s2))
    
    intersection = t1.intersection(t2)
    diff1 = t1.difference(t2)
    diff2 = t2.difference(t1)
    
    if not intersection: return 0.0
    
    sorted_inter = " ".join(sorted(list(intersection)))
    sorted_t1 = " ".join(sorted(list(t1)))
    sorted_t2 = " ".join(sorted(list(t2)))
    
    # Combinations to check (FuzzyWuzzy logic)
    # 1. Intersection vs Intersection (Always 1.0, not useful alone, but implies subset)
    # 2. Intersection vs Full S1
    # 3. Intersection vs Full S2
    
    # Actually FuzzyWuzzy does:
    # t0 = sorted_inter
    # t1 = sorted_inter + sorted_diff1
    # t2 = sorted_inter + sorted_diff2
    # But wait, t1 IS matches + unique_in_s1 which IS sorted_t1 (just reconstructed)
    
    # Comparing Intersection against the full strings allows "subset" matching.
    # e.g. "NBA" (inter) vs "NBA Basketball" (full). Ratio ~0.5? 
    # No, ratio("NBA", "NBA Basketball") -> 3*2 / (3+14) = 6/17 = 0.35.
    
    # Wait, FuzzyWuzzy token_set_ratio handles "NBA" vs "NBA Basketball" as 100.
    # explicitly because it checks:
    # ratio(intersection, intersection) which is 100?
    # If intersection IS one of the strings?
    
    # Let's implement a "Subset Ratio":
    # If one set is a subset of the other, return 1.0?
    # That might be dangerous (e.g. "League" vs "Premier League").
    # We need a penalty for length difference or commonly used words.
    
    # Let's stick to standard FuzzyWuzzy-ish logic:
    # scores = [ratio(t0, t1), ratio(t0, t2), ratio(t1, t2)]
    # t0 = sorted_inter
    # t1 = sorted_t1
    # t2 = sorted_t2
    
    s_inter = sorted_inter
    s1_full = sorted_t1
    s2_full = sorted_t2
    
    vals = [
        difflib.SequenceMatcher(None, s_inter, s1_full).ratio(),
        difflib.SequenceMatcher(None, s_inter, s2_full).ratio(),
        difflib.SequenceMatcher(None, s1_full, s2_full).ratio()
    ]
    return max(vals)

async def main():
    print("Fetching active SX.Bet leagues...")
    
    # 1. Fetch SX Bet Leagues
    # We can perform a direct API call or use the class if config allows
    sx = SXBetBookmaker(key="sx_bet", config={"use_testnet": False, "currency": "USDC"})
    # We need to run obtain_sports to get leagues
    # But obtain_sports usually maps them. We want raw titles.
    # Let's look at obtain_sports implementation or fetch active leagues directly
    
    try:
        # Re-using the logic from obtain_sports but just getting raw data
        # obtain_sports calls fetch_leagues internally if needed, or /sports
        # Let's verify obtain_sports returns the leagues with titles
        sx_leagues = await sx.obtain_sports() # This returns mapped dicts
        print(f"Fetched {len(sx_leagues)} leagues from SX.Bet")
    except Exception as e:
        print(f"Error fetching SX Bet leagues: {e}")
        return

    async with AsyncSessionLocal() as db:
        # 2. Fetch Internal Leagues
        res = await db.execute(select(League))
        internal_leagues = res.scalars().all()
        print(f"Fetched {len(internal_leagues)} internal leagues from DB")
        
        # Optimize lookup (exclude existing SX Bet leagues to test matching against generic)
        leagues_by_group = {}
        for l in internal_leagues:
            if not l.key.startswith("sx_bet_"):
                leagues_by_group.setdefault(l.group, []).append(l)

        print(f"Internal Groups (Generic): {list(leagues_by_group.keys())}")

        print(f"\n{'SX.Bet Title':<40} | {'Best Match (DB)':<40} | {'Score':<6} | {'Match?':<6} | {'Tokens'}")
        print("-" * 140)

        for item in sx_leagues:
            sx_title = item['title']
            sx_group = item['group']
            sx_key = item['key']
            
            # Filter candidates by Group (Sport)
            candidates = leagues_by_group.get(sx_group, [])
            
            if not candidates:
                # Debug mismatch
                print(f"DEBUG: No candidates for SX Group '{sx_group}'")
                continue

            best_match = None
            best_score = 0.0
            best_method = ""
            
            # Current Logic (Simple Ratio)
            for cand in candidates:
                # 1. Difflib Ratio
                score_simple = simple_ratio(sx_title, cand.title)
                
                # 2. Token Sort Ratio
                score_sort = token_sort_ratio(sx_title, cand.title)
                
                # 3. Token Set Ratio
                score_set = token_set_ratio(sx_title, cand.title)
                
                # Pick best
                current_best = max(score_simple, score_sort, score_set)
                method = "Simple"
                if score_sort > score_simple and score_sort >= score_set:
                    method = "Sort"
                elif score_set > score_simple and score_set > score_sort:
                    method = "Set"
                
                if current_best > best_score:
                    best_score = current_best
                    best_match = cand
                    best_method = method
            
            # Display
            # Threshold checking
            match_status = "YES" if best_score > 0.85 else "NO"
            
            # Print
            match_title = best_match.title if best_match else 'None'
            print(f"{sx_title:<40} | {match_title:<40} | {best_score:.2f} ({best_method}) | {match_status:<6} |")

if __name__ == "__main__":
    asyncio.run(main())
