<?php
require_once __DIR__ . '/auth.php';
require_login();
$user = current_user();

$stats = [
    'total_targets' => $pdo->query('SELECT COUNT(*) FROM targets')->fetchColumn(),
    'active_targets' => $pdo->query('SELECT COUNT(*) FROM targets WHERE active = 1')->fetchColumn(),
    'inactive_targets' => $pdo->query('SELECT COUNT(*) FROM targets WHERE active = 0')->fetchColumn(),
];

$targets = $pdo->query('SELECT id, name FROM targets WHERE active = 1 ORDER BY name')->fetchAll();
$defaultTargetId = null;
$latestTarget = $pdo->query(
    'SELECT ml.target_id FROM monitor_logs ml
     JOIN targets t ON t.id = ml.target_id
     WHERE t.active = 1
     ORDER BY ml.polled_at DESC
     LIMIT 1'
)->fetchColumn();
if ($latestTarget) {
    $defaultTargetId = (int)$latestTarget;
} elseif (!empty($targets)) {
    $defaultTargetId = $targets[0]['id'];
}

$recent = $pdo->query(
    'SELECT ml.polled_at, t.name AS target_name, ml.http_code,
            COALESCE(JSON_UNQUOTE(JSON_EXTRACT(ml.curl_info, "$.total_time")), 0) AS total_time
     FROM (
         SELECT id, target_id, polled_at, http_code, curl_info
         FROM monitor_logs
         ORDER BY polled_at DESC
         LIMIT 8
     ) ml
     JOIN targets t ON t.id = ml.target_id
     ORDER BY ml.polled_at DESC'
)->fetchAll();

require_once __DIR__ . '/header.php';
?>
<div class="page-header">
  <div>
    <h1>Dashboard</h1>
    <p class="muted">Welcome back, <?= htmlspecialchars($user['username']) ?>. Monitor recent health and performance at a glance.</p>
  </div>
  <a href="targets.php" class="button button-primary">Manage Targets</a>
</div>

<div class="grid dashboard-grid">
  <section class="card stat-card">
    <p class="stat-label">Total Targets</p>
    <p class="stat-value"><?= number_format($stats['total_targets']) ?></p>
  </section>
  <section class="card stat-card">
    <p class="stat-label">Active Targets</p>
    <p class="stat-value"><?= number_format($stats['active_targets']) ?></p>
  </section>
  <section class="card stat-card">
    <p class="stat-label">Inactive Targets</p>
    <p class="stat-value"><?= number_format($stats['inactive_targets']) ?></p>
  </section>
</div>

<section class="card card-full">
  <div class="section-header">
    <div>
      <h2>Performance Overview</h2>
      <p class="muted">View latency and error trends for the last hour, 6 hours, 24 hours or week.</p>
    </div>
    <div class="button-group" role="group" aria-label="Chart time range">
      <button type="button" class="button button-tertiary active" data-range="1h">1h</button>
      <button type="button" class="button button-tertiary" data-range="6h">6h</button>
      <button type="button" class="button button-tertiary" data-range="24h">24h</button>
      <button type="button" class="button button-tertiary" data-range="7d">Week</button>
    </div>
  </div>
  <div class="section-header">
    <label for="targetSelect">Target</label>
    <select id="targetSelect" class="button-secondary" style="max-width: 360px;">
      <?php foreach ($targets as $target): ?>
        <option value="<?= (int)$target['id'] ?>"<?= $target['id'] === $defaultTargetId ? ' selected' : '' ?>>
          <?= htmlspecialchars($target['name']) ?>
        </option>
      <?php endforeach; ?>
    </select>
  </div>
  <div class="chart-wrap">
    <canvas id="performanceChart"></canvas>
  </div>
  <div id="chartStatus" class="muted" style="margin-top: 14px;">Loading performance data…</div>
  <div class="chart-summary">
    <div class="metric-card">
      <span>Average Latency</span>
      <strong id="avgLatency">— ms</strong>
    </div>
    <div class="metric-card">
      <span>Error Rate</span>
      <strong id="errorRate">— %</strong>
    </div>
    <div class="metric-card">
      <span>Total Checks</span>
      <strong id="totalChecks">—</strong>
    </div>
  </div>
</section>

<section class="card card-full">
  <h2>Recent Checks</h2>
  <?php if (count($recent) === 0): ?>
    <p class="muted">No monitoring results are available yet.</p>
  <?php else: ?>
    <div class="table-wrap">
      <table>
        <thead>
          <tr>
            <th>Checked At</th>
            <th>Target</th>
            <th>Status</th>
            <th>Latency</th>
          </tr>
        </thead>
        <tbody>
          <?php foreach ($recent as $row): ?>
            <tr>
              <td><?= htmlspecialchars($row['polled_at']) ?></td>
              <td><?= htmlspecialchars($row['target_name']) ?></td>
              <td class="status <?= $row['http_code'] === null || $row['http_code'] >= 400 ? 'status-fail' : 'status-ok' ?>">
                <?= $row['http_code'] === null ? 'Error' : htmlspecialchars($row['http_code']) ?>
              </td>
              <td><?= htmlspecialchars(number_format((float)$row['total_time'] * 1000, 0)) ?> ms</td>
            </tr>
          <?php endforeach; ?>
        </tbody>
      </table>
    </div>
  <?php endif; ?>
</section>

<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<script src="assets/dashboard.js"></script>

<?php require_once __DIR__ . '/footer.php';
