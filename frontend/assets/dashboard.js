document.addEventListener('DOMContentLoaded', () => {
  const chartElement = document.getElementById('performanceChart');
  const avgLatencyEl = document.getElementById('avgLatency');
  const errorRateEl = document.getElementById('errorRate');
  const totalChecksEl = document.getElementById('totalChecks');
  const chartStatusEl = document.getElementById('chartStatus');
  const buttons = document.querySelectorAll('[data-range]');
  const targetSelect = document.getElementById('targetSelect');
  let currentRange = '1h';

  const chart = new Chart(chartElement.getContext('2d'), {
    type: 'line',
    data: {
      labels: [],
      datasets: [
        {
          label: 'Average latency',
          data: [],
          borderColor: '#2563eb',
          backgroundColor: '#2563eb',
          yAxisID: 'latency',
          tension: 0.35,
          pointRadius: 3,
          pointHoverRadius: 6,
          fill: false,
        },
        {
          label: 'Error rate',
          data: [],
          borderColor: '#f97316',
          backgroundColor: 'rgba(249,115,22,0.18)',
          yAxisID: 'error',
          tension: 0.35,
          pointRadius: 3,
          pointHoverRadius: 6,
          fill: true,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      interaction: {
        mode: 'index',
        intersect: false,
      },
      scales: {
        latency: {
          type: 'linear',
          position: 'left',
          title: {
            display: true,
            text: 'Latency (ms)',
          },
          grid: {
            drawBorder: false,
          },
        },
        error: {
          type: 'linear',
          position: 'right',
          min: 0,
          max: 100,
          title: {
            display: true,
            text: 'Error rate (%)',
          },
          grid: {
            drawOnChartArea: false,
          },
        },
      },
      plugins: {
        legend: {
          labels: {
            usePointStyle: true,
          },
        },
      },
    },
  });

  async function loadRange(range) {
    currentRange = range;
    buttons.forEach((button) => {
      button.classList.toggle('active', button.dataset.range === range);
    });

    const targetId = targetSelect ? targetSelect.value : null;
    const params = new URLSearchParams({ range });
    if (targetId) {
      params.set('target_id', targetId);
    }

    const response = await fetch(`graph_data.php?${params.toString()}`);
    if (!response.ok) {
      avgLatencyEl.textContent = '—';
      errorRateEl.textContent = '—';
      totalChecksEl.textContent = '—';
      chartStatusEl.textContent = 'Unable to load performance data.';
      console.error('Unable to load graph data', response.statusText);
      return;
    }

    const data = await response.json();
    chart.data.labels = data.labels;
    chart.data.datasets[0].data = data.latency;
    chart.data.datasets[1].data = data.error_rate;
    chart.update();

    const noData = data.latency.every((value) => value === null) && data.error_rate.every((value) => value === null);
    chartStatusEl.textContent = noData ? 'No performance data is available for this target and range.' : '';

    avgLatencyEl.textContent = `${data.summary.avg_latency_ms.toFixed(0)} ms`;
    errorRateEl.textContent = `${data.summary.error_rate.toFixed(1)} %`;
    totalChecksEl.textContent = data.summary.total_checks.toLocaleString();
  }

  buttons.forEach((button) => {
    button.addEventListener('click', () => loadRange(button.dataset.range));
  });

  if (targetSelect) {
    targetSelect.addEventListener('change', () => loadRange(currentRange));
  }

  loadRange('1h');
});
