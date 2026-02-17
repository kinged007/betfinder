
// Dashboard JavaScript Logic

let widgetChartInstance = null;
let websocket = null;
let reconnectTimeout = null;
let previousOdds = {}; // Track odds for flash animations

// Initialize Config
let dashboardConfig = {
    currentPresetId: null,
    currentDefaultStake: 10,
    currentPresetAfterTradeAction: 'none',
    sortCol: 'edge',
    sortDir: 'desc'
};

// Parse config from HTML
try {
    const configEl = document.getElementById('dashboard-config');
    if (configEl) {
        const rawConfig = JSON.parse(configEl.textContent);
        dashboardConfig.currentPresetId = rawConfig.currentPresetId;
        dashboardConfig.currentDefaultStake = rawConfig.currentDefaultStake;
        dashboardConfig.currentPresetAfterTradeAction = rawConfig.currentPresetAfterTradeAction;
        
        if (rawConfig.initialConfig) {
            dashboardConfig.sortCol = rawConfig.initialConfig.sort_by || 'edge';
            dashboardConfig.sortDir = rawConfig.initialConfig.sort_order || 'desc';

            // Map configuration to window globals for bet_modal.js
            window.currentPresetStakingStrategy = rawConfig.initialConfig.staking_strategy;
            window.currentPresetPercentRisk = rawConfig.initialConfig.percent_risk;
            window.currentPresetKellyMultiplier = rawConfig.initialConfig.kelly_multiplier;
            window.currentPresetMaxStake = rawConfig.initialConfig.max_stake;
            console.log("Global staking config set:", {
                strategy: window.currentPresetStakingStrategy,
                defaultStake: rawConfig.currentDefaultStake,
                risk: window.currentPresetPercentRisk
            });
        }
        window.currentPresetDefaultStake = rawConfig.currentDefaultStake;
    }
} catch (e) {
    console.error('Failed to parse dashboard config:', e);
}

// Global sort getters for internal use if needed, but we used local vars in functions before.
// We should update functions to use dashboardConfig directly.

document.addEventListener('DOMContentLoaded', () => {
    initWidgetChart();
    loadBetStats();
    loadUpcomingFixtures();

    if (dashboardConfig.currentPresetId) {
        connectWebSocket(dashboardConfig.currentPresetId);
    }
});

function initWidgetChart() {
    const canvas = document.getElementById('widgetBankrollChart');
    if (!canvas) return;
    
    const ctx = canvas.getContext('2d');
    widgetChartInstance = new Chart(ctx, {
        type: 'line',
        data: {
            datasets: [{
                label: 'Bankroll',
                data: [],
                borderColor: '#22c55e',
                backgroundColor: 'rgba(34, 197, 94, 0.1)',
                borderWidth: 2,
                pointRadius: 1,
                tension: 0.1,
                fill: true
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            scales: {
                x: {
                    type: 'time',
                    time: {
                        unit: 'day',
                        tooltipFormat: 'PP'
                    },
                    grid: { color: 'rgba(255, 255, 255, 0.05)' },
                    ticks: { maxTicksLimit: 5 }
                },
                y: {
                    grid: { color: 'rgba(255, 255, 255, 0.05)' },
                    ticks: { maxTicksLimit: 5 }
                }
            },
            plugins: {
                legend: { display: false },
                tooltip: {
                    callbacks: {
                        label: function (context) {
                            return 'Balance: ' + context.parsed.y.toFixed(2);
                        }
                    }
                }
            }
        }
    });
}

async function loadBetStats() {
    try {
        const response = await fetch('/api/dashboard/bet-stats');
        if (!response.ok) return;

        const data = await response.json();

        const totalBetsEl = document.getElementById('widget-total-bets');
        if (totalBetsEl) totalBetsEl.textContent = data.total_bets;

        const bankrollEl = document.getElementById('widget-bankroll');
        if (bankrollEl) bankrollEl.textContent = data.bankroll.toFixed(2);

        const npEl = document.getElementById('widget-net-profit');
        if (npEl) {
            npEl.textContent = data.net_profit.toFixed(2);
            npEl.classList.remove('text-success', 'text-error');
            npEl.classList.add(data.net_profit >= 0 ? 'text-success' : 'text-error');
        }

        const roiEl = document.getElementById('widget-roi');
        if (roiEl) {
            roiEl.textContent = data.roi.toFixed(1) + '%';
            roiEl.classList.remove('text-success', 'text-error');
            roiEl.classList.add(data.roi >= 0 ? 'text-success' : 'text-error');
        }

        const winRateEl = document.getElementById('widget-win-rate');
        if (winRateEl) winRateEl.textContent = data.win_rate.toFixed(1) + '%';

        if (widgetChartInstance && data.chart_data) {
            widgetChartInstance.data.datasets[0].data = data.chart_data;
            widgetChartInstance.update();
        }
    } catch (e) {
        console.error('Failed to load bet stats:', e);
    }
}

async function loadUpcomingFixtures() {
    try {
        const response = await fetch('/api/dashboard/upcoming-fixtures');
        if (!response.ok) return;

        const fixtures = await response.json();
        const container = document.getElementById('fixtures-list');
        if (!container) return;

        if (fixtures.length === 0) {
            container.innerHTML = '<div class="text-center py-4 text-sm opacity-70">No upcoming fixtures</div>';
            return;
        }

        // Helper function to escape HTML
        function escapeHtml(text) {
            const div = document.createElement('div');
            div.textContent = text;
            return div.innerHTML;
        }

        container.innerHTML = fixtures.map(fixture => {
            const date = new Date(fixture.commence_time);
            const isLive = fixture.is_live;
            const timeStr = isLive ? 'LIVE' : date.toLocaleString('en-US', {
                month: 'short',
                day: 'numeric',
                hour: '2-digit',
                minute: '2-digit'
            });
            const gmtTime = fixture.commence_time_utc || '';

            return `
                <div class="p-2 bg-base-200 rounded-lg hover:bg-base-300 transition-colors">
                    <div class="flex justify-between items-start text-xs">
                        <div class="flex-1">
                            <div class="font-semibold">${escapeHtml(fixture.home_team)} vs ${escapeHtml(fixture.away_team)}</div>
                            <div class="opacity-70 text-[10px]">${escapeHtml(fixture.league_title)}</div>
                        </div>
                        <div class="text-right">
                            ${isLive ? '<span class="badge badge-error badge-xs">LIVE</span>' : ''}
                            <div class="opacity-70 mt-1 tooltip tooltip-left" data-tip="${escapeHtml(gmtTime)}">
                                ${escapeHtml(timeStr)}
                            </div>
                        </div>
                    </div>
                </div>
            `;
        }).join('');
    } catch (e) {
        console.error('Failed to load fixtures:', e);
    }
}

function changePreset(presetId, presetName, newSortCol, newSortDir, newDefaultStake, newAfterTradeAction) {
    dashboardConfig.currentPresetId = presetId;
    dashboardConfig.currentDefaultStake = parseFloat(newDefaultStake);
    dashboardConfig.currentPresetAfterTradeAction = newAfterTradeAction;
    
    const presetNameEl = document.getElementById('selected-preset-name');
    if (presetNameEl) presetNameEl.textContent = presetName;

    if (newSortCol) dashboardConfig.sortCol = newSortCol;
    if (newSortDir) dashboardConfig.sortDir = newSortDir;

    // Close the dropdown
    if (document.activeElement) document.activeElement.blur();

    // Reconnect websocket
    if (websocket) {
        websocket.close();
    }
    connectWebSocket(presetId);
}

function connectWebSocket(presetId) {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/ws/tradefeed/${presetId}`;

    websocket = new WebSocket(wsUrl);

    websocket.onopen = () => {
        console.log('WebSocket connected');
        const indicator = document.getElementById('ws-indicator');
        if (indicator) {
            indicator.classList.remove('status-disconnected');
            indicator.classList.add('status-connected');
            indicator.setAttribute('data-tip', 'Connected');
        }

        clearTimeout(reconnectTimeout);
    };

    websocket.onmessage = (event) => {
        try {
            const data = JSON.parse(event.data);
            updateTradeFeed(data);
        } catch (e) {
            console.error('Failed to parse WebSocket message:', e);
        }
    };

    websocket.onerror = (error) => {
        console.error('WebSocket error:', error);
    };

    websocket.onclose = () => {
        console.log('WebSocket disconnected');
        const indicator = document.getElementById('ws-indicator');
        if (indicator) {
            indicator.classList.remove('status-connected');
            indicator.classList.add('status-disconnected');
            indicator.setAttribute('data-tip', 'Disconnected - Reconnecting...');
        }

        // Attempt to reconnect after 3 seconds
        reconnectTimeout = setTimeout(() => {
            if (dashboardConfig.currentPresetId) {
                connectWebSocket(dashboardConfig.currentPresetId);
            }
        }, 3000);
    };
}

function updateTradeFeed(data) {
    const tbody = document.getElementById('trade-feed-body');
    if (!tbody) return;

    // WebSocket sends 'opportunities', not 'odds'
    const opportunities = data.opportunities || [];

    // Sync globals from the first opportunity to ensure modal is up to date
    if (opportunities.length > 0) {
        const first = opportunities[0];
        if (first.staking_strategy) window.currentPresetStakingStrategy = first.staking_strategy;

        if (first.calculation_details) {
            const details = first.calculation_details;
            if (details.risk_pct !== undefined) window.currentPresetPercentRisk = details.risk_pct;
            if (details.kelly_mult !== undefined) window.currentPresetKellyMultiplier = details.kelly_mult;
            if (details.bankroll !== undefined) window.currentBookmakerBalance = details.bankroll;
        }
    }

    if (opportunities.length === 0) {
        tbody.innerHTML = `
            <tr>
                <td colspan="6" class="text-center py-6 text-sm opacity-70">
                    No trades available for this preset
                </td>
            </tr>
        `;
        return;
    }

    // Helper function to escape HTML
    function escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    // Dynamic sorting based on preset config
    const sortedOdds = [...opportunities].sort((a, b) => {
        let valA = a[dashboardConfig.sortCol];
        let valB = b[dashboardConfig.sortCol];

        // Handle case-insensitive string compare
        if (typeof valA === 'string') valA = valA.toLowerCase();
        if (typeof valB === 'string') valB = valB.toLowerCase();

        // Handle nulls
        if (valA === null && valB === null) return 0;
        if (valA === null) return 1;
        if (valB === null) return -1;

        if (valA < valB) return dashboardConfig.sortDir === 'asc' ? -1 : 1;
        if (valA > valB) return dashboardConfig.sortDir === 'asc' ? 1 : -1;
        return 0;
    });

    // Take top 20 for dashboard
    const displayOdds = sortedOdds.slice(0, 20);

    tbody.innerHTML = displayOdds.map(odd => {
        // Use start_time instead of event_commence_time
        const eventTime = new Date(odd.start_time);
        const dateStr = eventTime.toLocaleString('en-US', {
            month: 'short',
            day: 'numeric'
        });
        const timeStr = eventTime.toLocaleString('en-US', {
            hour: '2-digit',
            minute: '2-digit'
        });
        const gmtTime = eventTime.toISOString().replace('T', ' ').substring(0, 16) + ' UTC';

        // Check for odds change
        const oddKey = `${odd.event_id}_${odd.bookmaker_key}_${odd.market}_${odd.selection}`;
        const prevPrice = previousOdds[oddKey];
        let flashClass = '';

        if (prevPrice !== undefined && prevPrice != odd.price) {
            flashClass = odd.price > prevPrice ? 'flash-increase' : 'flash-decrease';
        }
        previousOdds[oddKey] = odd.price;

        // Calculate edge display
        const edgePercentage = odd.edge !== null ? (odd.edge * 100) : null;
        const edgeColor = (edgePercentage !== null && edgePercentage > 0) ? 'text-success' : 'text-error';
        const edgeDisplay = edgePercentage !== null ? ((edgePercentage > 0 ? '+' : '') + edgePercentage.toFixed(1) + '%') : '-';

        // Calculate probability (round to whole number)
        const probability = odd.implied_probability ? Math.round(odd.implied_probability * 100) : Math.round((1 / odd.price) * 100);

        // Format selection with capitalization and point
        const selectionDisplay = odd.selection.charAt(0).toUpperCase() + odd.selection.slice(1).toLowerCase();
        const pointDisplay = odd.point !== null && odd.point !== undefined ? ` (${odd.point > 0 ? '+' : ''}${odd.point})` : '';
        const fullSelection = `${selectionDisplay}${pointDisplay}`;

        // Market key uppercase
        const marketUpper = odd.market.toUpperCase();

        // Prepare item data for modal
        const itemJson = JSON.stringify(odd).replace(/"/g, '&quot;').replace(/'/g, '&#39;');

        return `
            <tr class="text-xs hover:bg-base-200 cursor-pointer" onclick='openBetModal(${itemJson})'>
                <td class="px-2 whitespace-nowrap">
                    <div class="tooltip tooltip-right" data-tip="${escapeHtml(gmtTime)}">
                        <div class="text-[10px] md:text-sm">${escapeHtml(dateStr)}</div>
                        <div class="opacity-70 text-[9px] md:text-xs">${escapeHtml(timeStr)}</div>
                    </div>
                </td>
                <td class="px-2">
                    <div class="font-semibold">${escapeHtml(odd.home)} vs ${escapeHtml(odd.away)}</div>
                    <div class="opacity-70 text-[10px]">${escapeHtml(marketUpper)} - ${escapeHtml(fullSelection)}</div>
                </td>
                <td class="px-2 text-center font-mono text-info ${flashClass}">${Number(odd.price).toFixed(2)}</td>
                <td class="px-2 text-center font-mono opacity-70">${probability}%</td>
                <td class="px-2 text-center font-mono ${edgeColor}">${edgeDisplay}</td>
                <td class="px-2 text-center">
                    <button class="btn btn-xs btn-primary" onclick="event.stopPropagation(); openBetModal(${itemJson})">Bet</button>
                </td>
            </tr>
        `;
    }).join('');
}

// Refresh data periodically
setInterval(loadBetStats, 60000); // Every minute
setInterval(loadUpcomingFixtures, 60000); // Every minute
