<?php
require_once __DIR__ . '/config.php';

$error = '';
if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    $username = trim($_POST['username'] ?? '');
    $password = $_POST['password'] ?? '';

    if ($username === '' || $password === '') {
        $error = 'Please enter both username and password.';
    } else {
        $stmt = $pdo->prepare('SELECT id, username, password_hash FROM users WHERE username = ?');
        $stmt->execute([$username]);
        $user = $stmt->fetch();
        if ($user && password_verify($password, $user['password_hash'])) {
            $_SESSION['user_id'] = $user['id'];
            header('Location: index.php');
            exit;
        }
        $error = 'Invalid username or password.';
    }
}
?>
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Login | API Monitor</title>
  <link rel="stylesheet" href="assets/style.css">
</head>
<body class="login-page">
<div class="login-panel">
  <h1>API Monitor</h1>
  <p class="muted">Sign in to manage targets and view monitoring status.</p>
  <?php if ($error): ?>
      <div class="alert alert-error"><?= htmlspecialchars($error) ?></div>
  <?php endif; ?>
  <form method="post">
    <label>Username</label>
    <input type="text" name="username" value="<?= htmlspecialchars($_POST['username'] ?? '') ?>" required>

    <label>Password</label>
    <input type="password" name="password" required>

    <button type="submit" class="button button-primary">Sign In</button>
  </form>
</div>
</body>
</html>
