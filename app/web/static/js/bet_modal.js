
// Bet Modal Logic
console.log("Loading Bet Modal Logic...");

// Helper function to safely get element value or set it
function setElValue(id, value) {
    const el = document.getElementById(id);
    if (el) el.value = value;
}

function setElText(id, text) {
    const el = document.getElementById(id);
    if (el) el.textContent = text;
}

function setElHTML(id, html) {
    const el = document.getElementById(id);
    if (el) el.innerHTML = html;
}

async function copyToClipboard(fieldId) {
    const val = document.getElementById(fieldId).value;
    try {
        await navigator.clipboard.writeText(val);
    } catch (err) {
        // Fallback
        const input = document.getElementById(fieldId);
        if (input) {
            input.select();
            document.execCommand('copy');
        }
    }
}

function recalculateEdge() {
    const priceEl = document.getElementById('modal-odds');
    const trueOddsEl = document.getElementById('modal-true-odds');

    if (!priceEl || !trueOddsEl) return;

    const price = parseFloat(priceEl.value);
    const trueOdds = parseFloat(trueOddsEl.value);

    if (price && trueOdds) {
        const edge = ((price / trueOdds - 1) * 100).toFixed(2);
        const edgeEl = document.getElementById('modal-edge');
        if (edgeEl) edgeEl.textContent = `${edge}%`;
    }
}

function openBetModal(item) {
    // Populate Hidden Fields
    setElValue('modal-event-id', item.event_id);
    setElValue('modal-home', item.home);
    setElValue('modal-away', item.away);
    setElValue('modal-sport', item.sport);
    setElValue('modal-league', item.league);
    setElValue('modal-market', item.market);
    setElValue('modal-selection', item.selection);
    setElValue('modal-bookmaker', item.bookmaker);

    if (item.price) setElValue('modal-odds', item.price.toFixed(2));
    if (item.true_odds) setElValue('modal-true-odds', item.true_odds);
    if (item.point !== undefined) setElValue('modal-point', item.point);
    if (item.url) setElValue('modal-url', item.url);

    // Fix for ID collision: use modal-start-time-val for input if available
    setElValue('modal-start-time-val', item.start_time);

    // Probability
    const trueProbability = item.true_odds ? (1 / item.true_odds * 100).toFixed(2) : (item.price ? (1 / item.price * 100).toFixed(2) : '0');
    setElValue('modal-probability', trueProbability);
    setElText('modal-prob-display', `${trueProbability}%`);

    // Visible Fields
    setElText('modal-home-display', item.home);
    setElText('modal-away-display', item.away);
    setElText('modal-league-short', item.league);

    // Market / Selection Formatting
    // Fallback if selection_name not provided (sometimes missing in raw data)
    const selectionName = item.selection_name || item.selection;
    const selectionNorm = (item.selection || "").charAt(0).toUpperCase() + (item.selection || "").slice(1).toLowerCase();
    const marketKey = (item.market || "").toUpperCase();
    const pointDisplay = item.point !== null && item.point !== undefined ? ` (${item.point > 0 ? '+' : ''}${item.point})` : '';

    setElText('modal-market-selection', `${selectionName} (${selectionNorm})${pointDisplay} - ${marketKey}`);

    setElText('modal-bookmaker-name', item.bookmaker);

    // Link
    const linkBtn = document.getElementById('modal-bookmaker-link');
    if (linkBtn) {
        if (item.url) {
            linkBtn.href = item.url;
            linkBtn.classList.remove('hidden');
        } else {
            linkBtn.classList.add('hidden');
        }
    }

    // Time
    if (window.formatLocalTime) {
        const timeDisplay = window.formatLocalTime(item.start_time);
        setElHTML('modal-time-text', timeDisplay);
    } else {
        setElText('modal-time-text', item.start_time);
    }

    // Calculate stake using staking strategy
    let calculatedStake = 10; // Default fallback
    
    try {
        const strategy = window.currentPresetStakingStrategy || 'fixed';
        const defaultStake = parseFloat(window.currentPresetDefaultStake) || 10;
        const percentRisk = window.currentPresetPercentRisk;
        const kellyMultiplier = window.currentPresetKellyMultiplier;
        const maxStake = window.currentPresetMaxStake;
        
        // Get bankroll for the bookmaker (if available)
        // This would need to be set when opening the modal or retrieved from window/global state
        const bankroll = window.currentBookmakerBalance || 1000; // Fallback to 1000 if not available
        
        // Get probability - use implied_probability if available, otherwise calculate from odds
        const probability = item.implied_probability || (item.price ? 1 / item.price : null);
        const odds = item.price;
        
        // Calculate stake using the stake calculator function
        if (typeof calculateStake === 'function') {
            calculatedStake = calculateStake(
                strategy,
                defaultStake,
                bankroll,
                probability,
                odds,
                percentRisk,
                kellyMultiplier,
                maxStake
            );
        } else {
            console.warn('calculateStake function not found, using default stake');
            calculatedStake = defaultStake;
        }
    } catch (error) {
        console.error('Error calculating stake:', error);
        calculatedStake = parseFloat(window.currentPresetDefaultStake) || 10;
    }

    // Set the calculated stake
    setElValue('modal-stake', calculatedStake || 10);

    recalculateEdge();
    const modal = document.getElementById('bet-modal');
    if (modal) modal.showModal();
}

async function submitBet() {
    const eventId = document.getElementById('modal-event-id').value;
    const bookmaker = document.getElementById('modal-bookmaker').value;
    const market = document.getElementById('modal-market').value;
    const selection = document.getElementById('modal-selection').value;
    const price = parseFloat(document.getElementById('modal-odds').value);
    const stake = parseFloat(document.getElementById('modal-stake').value);
    const trueOdds = parseFloat(document.getElementById('modal-true-odds').value);

    // Get Preset ID
    let currentPresetId = null;
    if (typeof window.currentPresetId !== 'undefined') {
        currentPresetId = window.currentPresetId;
    } else if (typeof window.presetId !== 'undefined') {
        currentPresetId = window.presetId;
    }

    const betData = {
        event_id: eventId,
        bookmaker: bookmaker,
        market: market,
        selection: selection,
        price: price,
        stake: stake,
        true_odds: trueOdds,
        preset_id: parseInt(currentPresetId)
    };

    try {
        const response = await fetch('/trade-feed/bet', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(betData)
        });

        if (response.ok) {
            if (typeof showToast === 'function') {
                showToast('Trade registered successfully!', 'success');
            } else {
                alert('Trade registered successfully!');
            }

            const modal = document.getElementById('bet-modal');
            if (modal) modal.close();

            // Hook for post-trade actions (fire and forget - don't block modal closing)
            handleAfterTrade(betData);


        } else {
            const error = await response.json();
            const msg = `Error: ${error.detail || 'Failed to register trade'}`;
            if (typeof showToast === 'function') showToast(msg, "error");
            else alert(msg);
        }
    } catch (error) {
        const msg = `Error: ${error.message}`;
        if (typeof showToast === 'function') showToast(msg, "error");
        else alert(msg);
    }
}

async function handleAfterTrade(betData) {
    // Check if we have After Trade Action logic available
    // This is specific to trade_feed.html usually
    // We check for global config

    let afterTradeAction = 'none';
    if (typeof window.currentPresetAfterTradeAction !== 'undefined') {
        afterTradeAction = window.currentPresetAfterTradeAction;
    }

    // Dispatch custom event for UI updates
    const event = new CustomEvent('betPlaced', { detail: { betData, action: afterTradeAction } });
    window.dispatchEvent(event);

    // If we are in trade_feed context (checked by function existence), do the heavy lifting
    if (typeof window.renderData === 'function' && typeof window.lastData !== 'undefined') {
        // Trade Feed Logic
        if (afterTradeAction === 'keep') {
            const data = window.lastData;
            const itemIdx = data.findIndex(d => d.event_id === betData.event_id && d.market === betData.market && d.selection === betData.selection);
            if (itemIdx !== -1) {
                data[itemIdx].has_bet = true;
                window.renderData(data);
            }
            return;
        }

        // Hiding logic
        // Verify dependencies
        const startTimeInput = document.getElementById('modal-start-time-val');
        if (!startTimeInput || !startTimeInput.value) return;

        const startTime = new Date(startTimeInput.value);
        const expiryDate = new Date(startTime.getTime() + 24 * 60 * 60 * 1000);

        const presetPayload = {
            event_id: betData.event_id,
            expiry_at: expiryDate.toISOString()
        };

        if (afterTradeAction === 'remove_trade') {
            presetPayload.market_key = betData.market;
            presetPayload.selection_norm = betData.selection;
            window.lastData = window.lastData.filter(d => !(d.event_id === betData.event_id && d.market === betData.market && d.selection === betData.selection));
        } else if (afterTradeAction === 'remove_line') {
            presetPayload.market_key = betData.market;
            window.lastData = window.lastData.filter(d => !(d.event_id === betData.event_id && d.market === betData.market));
        } else if (afterTradeAction === 'remove_match') {
            window.lastData = window.lastData.filter(d => d.event_id !== betData.event_id);
        }

        // Assume presetId is available if we got here
        let pid = window.presetId || window.currentPresetId;

        if (pid && afterTradeAction !== 'none') {
            await fetch(`/api/v1/presets/${pid}/hidden-items`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(presetPayload)
            });
            window.renderData(window.lastData);
        }
    }
}
