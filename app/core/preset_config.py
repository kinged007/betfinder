
from typing import List, Dict, Any

PRESET_OTHER_CONFIG_SCHEMA: List[Dict[str, Any]] = [
    {
        "key": "sort_by",
        "label": "Initial Sort Field",
        "type": "select",
        "options": [
            {"value": "edge", "label": "Edge"},
            {"value": "start_time", "label": "Start Time"},
            {"value": "price", "label": "Odds"},
            {"value": "implied_probability", "label": "Probability"},
            {"value": "home", "label": "Market/Event"}
        ],
        "default": "edge"
    },
    {
        "key": "sort_order",
        "label": "Initial Sort Order",
        "type": "select",
        "options": [
            {"value": "desc", "label": "Descending (High to Low)"},
            {"value": "asc", "label": "Ascending (Low to High)"}
        ],
        "default": "desc"
    },
    {
        "key": "group_by",
        "label": "Initial Grouping",
        "type": "select",
        "options": [
            {"value": "none", "label": "None"},
            {"value": "sport", "label": "Sport"},
            {"value": "league", "label": "League"},
            {"value": "event", "label": "Event"},
            {"value": "bookmaker", "label": "Bookmaker"}
        ],
        "default": "none"
    },
    {
        "key": "notification_new_bet",
        "label": "Notification New Bet",
        "type": "select",
        "options": [
            {"value": "true", "label": "Enabled"},
            {"value": "false", "label": "Disabled"}
        ],
        "default": "true"
    }
]
