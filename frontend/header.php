<?php
require_once __DIR__ . '/auth.php';
$user = current_user();
?>
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>API Monitor</title>
  <link rel="stylesheet" href="assets/style.css">
</head>
<body>
<header class="app-header">
  <div class="brand">API Monitor</div>
  <?php if ($user): ?>
  <nav class="app-nav">
    <a href="index.php">Dashboard</a>
    <a href="targets.php">Targets</a>
    <a href="logout.php">Logout</a>
  </nav>
  <?php endif; ?>
</header>
<main class="app-shell">
