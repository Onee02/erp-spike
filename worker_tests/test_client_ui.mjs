// Execute the real browser script with a small DOM/fetch test double (no server).
import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const script = fs.readFileSync(new URL('../app/static/client.js', import.meta.url), 'utf8');
const tick = () => new Promise((resolve) => setImmediate(resolve));

async function page(tenants = [{ id: 1, name: 'Whale', plan: 'enterprise' }, { id: 5, name: 'Small', plan: 'small' }]) {
  const elements = new Map();
  const element = (id) => {
    if (!elements.has(id)) elements.set(id, {
      value: '', innerHTML: '', textContent: '', disabled: false, hidden: true, className: '',
      addEventListener() {},
      get selectedOptions() { return [{ dataset: { plan: this.value === '1' ? 'enterprise' : 'small' } }]; },
    });
    return elements.get(id);
  };
  element('product').value = 'SKU-1';
  element('qty').value = '2';
  element('price').value = '9.99';
  const calls = [];
  const state = { failWhale: false };
  const context = vm.createContext({
    document: { getElementById: element }, window: { addEventListener() {} },
    performance, AbortSignal,
    fetch: async (url, options = {}) => {
      calls.push([url, options]);
      const failed = state.failWhale && url.startsWith('/t/1/');
      let data = url === '/api/tenants' ? tenants : [];
      if (options.method === 'POST') data = { id: 99, total: '19.98' };
      if (failed) data = { detail: 'Whale unavailable' };
      return { ok: !failed, status: failed ? 503 : 200, json: async () => data };
    },
  });
  vm.runInContext(script, context);
  await tick();
  return { context, element, calls, state };
}

test('tenant selector includes both groups and still defaults to customer 5', async () => {
  const { element, calls } = await page();
  assert.equal(element('tenant').value, '5');
  assert.match(element('tenant').innerHTML, /value="1"/);
  assert.match(element('tenant').innerHTML, /value="5"/);
  assert.ok(calls.some(([url]) => url === '/t/5/orders?limit=10'));
});

test('a smaller existing dataset does not break the selector when customer 5 is absent', async () => {
  const { element } = await page([{ id: 2, name: 'Small', plan: 'small' }]);
  assert.equal(element('tenant').value, '2');
});

test('whale failure does not block switching back to a small customer', async () => {
  const { context, element, state } = await page();
  state.failWhale = true;
  element('tenant').value = '1';
  await context.loadOrders();
  assert.equal(element('orders-msg').textContent, 'Whale unavailable');
  assert.equal(element('crash').hidden, true);
  element('tenant').value = '5';
  await context.loadOrders();
  assert.equal(element('orders-msg').textContent, 'No orders.');
});

test('order and report buttons recover after whale errors', async () => {
  const { context, element, calls, state } = await page();
  state.failWhale = true;
  element('tenant').value = '1';
  await context.addOrder();
  await context.runReport();
  assert.equal(element('add').disabled, false);
  assert.equal(element('report').disabled, false);
  assert.equal(element('add-msg').textContent, 'Whale unavailable');
  assert.equal(element('report-msg').textContent, 'Whale unavailable');
  element('tenant').value = '5';
  await context.addOrder();
  await context.runReport();
  assert.equal(element('add-msg').textContent, 'Order added.');
  assert.match(element('report-msg').textContent, /^Done in/);
  const [, post] = calls.find(([url, options]) => url === '/t/5/orders' && options.method === 'POST');
  assert.equal(JSON.parse(post.body).lines[0].qty, 2);
});
