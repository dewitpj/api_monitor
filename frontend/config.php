<?php

// Frontend config reads from environment variables where available.
$DB_HOST = getenv('API_MONITOR_DB_HOST') ?: '127.0.0.1';
$DB_PORT = getenv('API_MONITOR_DB_PORT') ?: '3306';
$DB_NAME = getenv('API_MONITOR_DB_NAME') ?: 'api_monitor';
$DB_USER = getenv('API_MONITOR_DB_USER') ?: 'api_monitor';
$DB_PASS = getenv('API_MONITOR_DB_PASS') ?: '9LL3nT6GpZrqxZJY';

$options = [
    PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
    PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
    PDO::ATTR_EMULATE_PREPARES => false,
];

try {
    $pdo = new PDO(
        "mysql:host=$DB_HOST;port=$DB_PORT;dbname=$DB_NAME;charset=utf8mb4",
        $DB_USER,
        $DB_PASS,
        $options
    );
} catch (Exception $e) {
    http_response_code(500);
    echo '<!doctype html><html><head><meta charset="utf-8"><title>API Monitor</title></head><body><h1>Database connection failed</h1><p>Please check configuration.</p></body></html>';
    exit;
}

session_start();
