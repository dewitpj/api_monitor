<?php
require_once __DIR__ . '/auth.php';
require_login();

$targets = $pdo->query(
    'SELECT t.*, g.name AS group_name
     FROM targets t
     LEFT JOIN groups g ON g.id = t.group_id
     ORDER BY t.name'
)->fetchAll();

require_once __DIR__ . '/header.php';
?>
<div class="page-header">
  <div>
    <h1>Targets</h1>
    <p class="muted">Review and edit monitored endpoints.</p>
  </div>
  <a href="target_edit.php" class="button button-primary">Create Target</a>
</div>

<section class="card card-full">
  <table>
    <thead>
      <tr>
        <th>Name</th>
        <th>URL</th>
        <th>Group</th>
        <th>Interval</th>
        <th>Status</th>
        <th>Actions</th>
      </tr>
    </thead>
    <tbody>
      <?php foreach ($targets as $target): ?>
      <tr>
        <td><?= htmlspecialchars($target['name']) ?></td>
        <td class="break-word"><?= htmlspecialchars($target['url']) ?></td>
        <td><?= htmlspecialchars($target['group_name'] ?: '—') ?></td>
        <td><?= number_format((int)$target['check_interval']) ?>s</td>
        <td class="status <?= $target['active'] ? 'status-ok' : 'status-fail' ?>">
          <?= $target['active'] ? 'Active' : 'Inactive' ?>
        </td>
        <td>
          <a class="link-action" href="target_edit.php?id=<?= (int)$target['id'] ?>">Edit</a>
        </td>
      </tr>
      <?php endforeach; ?>
    </tbody>
  </table>
</section>

<?php require_once __DIR__ . '/footer.php';
