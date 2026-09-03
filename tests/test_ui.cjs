const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('web2rtsp/static/diagnostics.js', 'utf8');
const tick = () => new Promise(resolve => setImmediate(resolve));

function fixture() {
  const sample = {container:{cpu_percent:110, cpu_capacity_percent:27.5, memory_working_set_bytes:104857600},
    cpu_capacity_cores:4, sample_cost_ms:2, components:[{component:'chromium', stream:'<img onerror=bad>', cpu_percent:100, memory_pss_bytes:null, memory_rss_bytes:20971520, processes:3}]};
  return {latest:sample, history:[sample,sample], status:'ok', stale:false, sample_age_seconds:1, sample_interval_seconds:5,
    streams:[{name:'<script>bad</script>',state:'running',width:1280,height:720,configured_fps:10,configured_bitrate_kbps:1500,consecutive_failures:0}]};
}

function mount(fetch) {
  const heading = {}, content = {}, timers = [];
  const context = {fetch, setTimeout:fn => timers.push(fn), document:{hidden:false,
    querySelector:selector => { assert.equal(selector, '#diagnostics'); return {querySelector:key => key === '.diag-status' ? heading : content}; }}};
  vm.runInNewContext(source, context);
  return {heading, content, timers};
}

test('diagnostics show scaled metrics, escape labels, and isolate DOM updates', async () => {
  const view = mount(async () => ({ok:true,json:async () => fixture()}));
  await tick();
  assert.match(view.heading.textContent, /Live/);
  assert.match(view.content.innerHTML, /110.0%/);
  assert.match(view.content.innerHTML, /27.5%/);
  assert.match(view.content.innerHTML, /100.0 MiB/);
  assert.match(view.content.innerHTML, /&lt;img onerror=bad&gt;/);
  assert.doesNotMatch(view.content.innerHTML, /<script>bad|<img onerror/);
  assert.match(view.content.innerHTML, /<td>—<\/td>/);
  assert.equal(view.timers.length, 1);
});

test('missing counters are not presented as zero', async () => {
  const data = fixture(); data.latest = null; data.status = 'unavailable'; data.stale = true;
  const view = mount(async () => ({ok:true,json:async () => data}));
  await tick();
  assert.match(view.heading.textContent, /unavailable/);
  assert.match(view.content.textContent, /Waiting for Linux process counters/);
});

test('failed polling marks any retained values stale', async () => {
  const view = mount(async () => ({ok:false}));
  await tick();
  assert.match(view.heading.textContent, /stale/);
  assert.match(view.heading.className, /error/);
  assert.equal(view.timers.length, 1);
});

test('status polling does not rebuild configuration forms', async () => {
  const html = fs.readFileSync('web2rtsp/static/index.html','utf8');
  const fn = html.slice(html.indexOf('async function refreshStatus()'), html.indexOf('async function init()'));
  let renders=0;
  const badge={}, media={}, field={value:'unsaved-user-edit'};
  const context = {status:{}, render:()=>renders++, $:()=>media,
    document:{querySelectorAll:()=>[{_stream:{name:'test'},querySelector:()=>badge}]},
    fetch:async()=>({json:async()=>({mediamtx:{running:true},streams:[{name:'test',state:'running'}]})})};
  vm.createContext(context);
  vm.runInContext(fn,context);
  await vm.runInContext('refreshStatus()',context);
  assert.equal(renders,0);
  assert.equal(badge.textContent,'running');
  assert.equal(field.value,'unsaved-user-edit');
});
