# Bet History & Lifecycle

The **Bet History** page tracks all your trades. Every bet follows a specific lifecycle defined by its status.

## Bet Status Definitions

### 1. Initial States
*   **Manual**: A trade registered manually from the Trade Feed. It is recorded but requires confirmation of details.
*   **Pending**: An automated trade request sent to a bookmaker, awaiting a confirmation response.

### 2. Active State
*   **Open**: The bet is confirmed and active. The trade is "in play," and you are waiting for the event to conclude.

### 3. Final States
*   **Won**: Match concluded; the bet was successful.
*   **Lost**: Match concluded; the bet was unsuccessful.
*   **Void**: The bet was cancelled (e.g., match postponed, player withdrawal) and the stake was returned.

## Settle & Edit
You can manually update a bet's status and final payout using the **Settle** button on the Bet History page. Updating a *Manual* or *Pending* bet without a result will move it to the **Open** state.

## Bulk Actions
You can update multiple bets at once by using the checkboxes on the left side of the table. Once one or more bets are selected, a **Bulk Actions** bar will appear at the top, allowing you to update the status of all selected bets in a single click. 

## Odds Explorer (Dev Tool)
For development and testing, you can use the **Odds Explorer** (`/dev/odds`) to view all raw odds in the system. This page allows you to bypass presets and place "Quick Bets" (fixed 10 EUR) directly into the history for testing purposes.

