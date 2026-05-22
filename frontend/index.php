<?php
require_once __DIR__ . '/auth.php';
require_login();
$user = current_user();

$stats = [
    'total_targets' => $pdo->query('SELECT COUNT(*) FROM targets')->fetchColumn(),
    'active_targets' => $pdo->query('SELECT COUNT(*) FROM targets WHERE active = 1')->fetchColumn(),
    'inactive_targets' => $pdo->query('SELECT COUNT(*) FROM targets WHERE active = 0')->fetchColumn(),
];

$recent = $pdo->query(
    'SELECT ml.polled_at, t.name AS target_name, ml.http_code,
            COALESCE(JSON_UNQUOTE(JSON_EXTRACT(ml.curl_info, "$.total_time")), 0) AS total_time
     FROM monitor_logs ml
     JOIN targets t ON t.id = ml.target_id
     ORDER BY ml.polled_at DESC
     LIMIT 8'
)->fetchAll();

require_once __DIR__ . '/header.php';
?>
<div class="page-header">
  <div>
    <h1>Dashboard</h1>
    <p class="muted">Welcome back, <?= htmlspecialchars($user['username']) ?>.</p>
  </div>
  <a href="targets.php" class="button button-secondary">Manage Targets</a>
</div>

<div class="grid dashboard-grid">
  <section class="card">
    <h2>Total Targets</h2>
    <p class="stat-value"><?= number_format($stats['total_targets']) ?></p>
  </section>
  <section class="card">
    <h2>Active Targets</h2>
    <p class="stat-value"><?= number_format($stats['active_targets']) ?></p>
  </section>
  <section class="card">
    <h2>Inactive Targets</h2>
    <p class="stat-value"><?= number_format($stats['inactive_targets']) ?></p>
  </section>
</div>

<section class="card card-full">
  <h2>Recent Checks</h2>
  <?php if (count($recent) === 0): ?>
    <p class="muted">No monitoring results are available yet.</p>
  <?php else: ?>
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
  <?php endif; ?>
</section>

<?php require_once __DIR__ . '/footer.php';
