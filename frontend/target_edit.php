<?php
require_once __DIR__ . '/auth.php';
require_login();

$id = isset($_GET['id']) ? (int)$_GET['id'] : null;
$target = null;
$groups = [];

try {
    $groups = $pdo->query('SELECT id, name FROM groups ORDER BY name')->fetchAll();
} catch (Exception $e) {
    $groups = [];
}

if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    $name = trim($_POST['name'] ?? '');
    $url = trim($_POST['url'] ?? '');
    $interval = max(10, (int)($_POST['interval'] ?? 60));
    $method = strtoupper(trim($_POST['method'] ?? 'GET'));
    $active = isset($_POST['active']) && $_POST['active'] == '1' ? 1 : 0;
    $group_id = !empty($_POST['group_id']) ? (int)$_POST['group_id'] : null;

    if ($id) {
        $stmt = $pdo->prepare('UPDATE targets SET name = ?, url = ?, check_interval = ?, http_method = ?, active = ?, group_id = ? WHERE id = ?');
        $stmt->execute([$name, $url, $interval, $method, $active, $group_id, $id]);
    } else {
        $stmt = $pdo->prepare('INSERT INTO targets (name, url, check_interval, http_method, active, group_id) VALUES (?, ?, ?, ?, ?, ?)');
        $stmt->execute([$name, $url, $interval, $method, $active, $group_id]);
    }

    header('Location: targets.php');
    exit;
}

if ($id) {
    $stmt = $pdo->prepare('SELECT * FROM targets WHERE id = ?');
    $stmt->execute([$id]);
    $target = $stmt->fetch();
}

require_once __DIR__ . '/header.php';
?>
<div class="page-header">
  <div>
    <h1><?= $id ? 'Edit Target' : 'Create Target' ?></h1>
    <p class="muted">Define a monitored endpoint and polling settings.</p>
  </div>
  <a href="targets.php" class="button button-secondary">Back to Targets</a>
</div>

<section class="card card-form">
  <form method="post">
    <label for="name">Name</label>
    <input id="name" name="name" value="<?= htmlspecialchars($target['name'] ?? '') ?>" required>

    <label for="url">URL</label>
    <input id="url" name="url" type="url" value="<?= htmlspecialchars($target['url'] ?? '') ?>" required>

    <div class="row split">
      <div>
        <label for="interval">Interval (seconds)</label>
        <input id="interval" name="interval" type="number" min="10" value="<?= htmlspecialchars($target['check_interval'] ?? 60) ?>">
      </div>
      <div>
        <label for="method">HTTP Method</label>
        <select id="method" name="method">
          <?php foreach (['GET','POST','PUT','DELETE'] as $method): ?>
            <option value="<?= $method ?>" <?= ($target['http_method'] ?? 'GET') === $method ? 'selected' : '' ?>><?= $method ?></option>
          <?php endforeach; ?>
        </select>
      </div>
    </div>

    <div class="row split">
      <div>
        <label for="group_id">Group</label>
        <select id="group_id" name="group_id">
          <option value="">None</option>
          <?php foreach ($groups as $group): ?>
            <option value="<?= (int)$group['id'] ?>" <?= !empty($target['group_id']) && $target['group_id'] == $group['id'] ? 'selected' : '' ?>><?= htmlspecialchars($group['name']) ?></option>
          <?php endforeach; ?>
        </select>
      </div>
      <div>
        <label for="active">Active</label>
        <select id="active" name="active">
          <option value="1" <?= ($target['active'] ?? 1) ? 'selected' : '' ?>>Yes</option>
          <option value="0" <?= empty($target['active']) ? 'selected' : '' ?>>No</option>
        </select>
      </div>
    </div>

    <button type="submit" class="button button-primary">Save Target</button>
  </form>
</section>

<?php require_once __DIR__ . '/footer.php';
