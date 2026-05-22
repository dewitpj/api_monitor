<?php
require_once __DIR__ . '/auth.php';
header('Content-Type: application/json; charset=utf-8');

if (empty($_SESSION['user_id'])) {
    http_response_code(401);
    echo json_encode(['error' => 'Unauthorized']);
    exit;
}

$allowedRanges = [
    '1h' => ['seconds' => 3600, 'bucket' => 300],
    '6h' => ['seconds' => 21600, 'bucket' => 900],
    '24h' => ['seconds' => 86400, 'bucket' => 3600],
    '7d' => ['seconds' => 604800, 'bucket' => 21600],
];

$range = $_GET['range'] ?? '1h';
if (!isset($allowedRanges[$range])) {
    $range = '1h';
}

$interval = $allowedRanges[$range];
$windowSeconds = $interval['seconds'];
$bucketSeconds = $interval['bucket'];
$minTime = date('Y-m-d H:i:s', time() - $windowSeconds);

$targetId = isset($_GET['target_id']) ? (int)$_GET['target_id'] : 0;
if ($targetId <= 0) {
    $stmt = $pdo->prepare('SELECT id FROM targets WHERE active = 1 ORDER BY name LIMIT 1');
    $stmt->execute();
    $default = $stmt->fetch();
    $targetId = $default ? (int)$default['id'] : 0;
}

$stmt = $pdo->prepare(
    'SELECT
         FROM_UNIXTIME(FLOOR(UNIX_TIMESTAMP(polled_at) / ?) * ?) AS bucket,
         AVG(COALESCE(JSON_UNQUOTE(JSON_EXTRACT(curl_info, "$.total_time")), 0)) * 1000 AS avg_latency_ms,
         SUM(CASE WHEN http_code IS NULL OR http_code >= 400 THEN 1 ELSE 0 END) / COUNT(*) * 100 AS error_rate,
         COUNT(*) AS checks
     FROM monitor_logs
     WHERE polled_at >= ?
       AND target_id = ?
     GROUP BY bucket
     ORDER BY bucket ASC'
);
$stmt->execute([$bucketSeconds, $bucketSeconds, $minTime, $targetId]);
$rows = $stmt->fetchAll();

$buckets = [];
$cursor = strtotime($minTime);
$end = time();
while ($cursor <= $end) {
    $buckets[date('Y-m-d H:i:s', $cursor)] = [
        'avg_latency_ms' => null,
        'error_rate' => null,
        'checks' => 0,
    ];
    $cursor += $bucketSeconds;
}

foreach ($rows as $row) {
    $bucketKey = date('Y-m-d H:i:s', strtotime($row['bucket']));
    if (isset($buckets[$bucketKey])) {
        $buckets[$bucketKey] = [
            'avg_latency_ms' => (float)$row['avg_latency_ms'],
            'error_rate' => (float)$row['error_rate'],
            'checks' => (int)$row['checks'],
        ];
    }
}

$labels = [];
$latency = [];
$errorRate = [];
$summaryLatency = [];
$summaryErrors = [];
$totalChecks = 0;

foreach ($buckets as $bucketTime => $bucketData) {
    $labels[] = date('H:i', strtotime($bucketTime));
    $latency[] = $bucketData['avg_latency_ms'];
    $errorRate[] = $bucketData['error_rate'];
    if ($bucketData['checks'] > 0) {
        $summaryLatency[] = $bucketData['avg_latency_ms'];
        $summaryErrors[] = $bucketData['error_rate'];
        $totalChecks += $bucketData['checks'];
    }
}

$summary = [
    'avg_latency_ms' => count($summaryLatency) ? array_sum($summaryLatency) / count($summaryLatency) : 0,
    'error_rate' => count($summaryErrors) ? array_sum($summaryErrors) / count($summaryErrors) : 0,
    'total_checks' => $totalChecks,
];

echo json_encode([
    'labels' => $labels,
    'latency' => $latency,
    'error_rate' => $errorRate,
    'summary' => $summary,
]);
