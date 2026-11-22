// Static routing-contract tests. These do not replace nginx -t or real HTTP tests.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';

const config = fs.readFileSync(new URL('../nginx/default.conf.template', import.meta.url), 'utf8');
const rules = [...config.matchAll(/^\s+~(\S+)\s+\$\{(\w+)\};$/gm)]
  .map(([, expression, target]) => [new RegExp(expression), target]);
const route = (path) => rules.find(([pattern]) => pattern.test(path))?.[1] ?? 'SMALL_UPSTREAM';

test('the map sends whale paths, including numeric aliases, to its own worker', () => {
  assert.equal(rules.length, 3);
  for (const path of ['/t/1/orders', '/t/1/reports/monthly', '/t/01/orders', '/t/0001/orders',
                      '/admin/whale', '/admin/whale/add', '/admin/whale/remove',
                      '/admin/simulation', '/admin/simulation/start', '/admin/simulation/stop',
                      '/admin/metrics/whale', '/admin/metrics/whale/reset']) {
    assert.equal(route(path), 'WHALE_UPSTREAM', path);
  }
});

test('small tenant IDs and shared UI paths stay on the small worker', () => {
  for (const id of [2, 5, 10, 11, 100, 101, 301]) {
    assert.equal(route(`/t/${id}/orders`), 'SMALL_UPSTREAM');
  }
  for (const path of ['/', '/ops', '/static/client.js', '/api/tenants', '/health',
                      '/admin/metrics', '/admin/metrics/small', '/admin/metrics/small/reset']) {
    assert.equal(route(path), 'SMALL_UPSTREAM', path);
  }
});

test('the proxy preserves request URI and never retries another application group', () => {
  assert.match(config, /map \$uri \$erp_backend/);
  assert.match(config, /proxy_pass http:\/\/\$erp_backend\$request_uri;/);
  assert.match(config, /proxy_next_upstream off;/);
  assert.match(config, /resolver 127\.0\.0\.11/);
});
