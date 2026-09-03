// Independent read-only polling: never rebuild the stream configuration forms.
(() => {
  const root = document.querySelector('#diagnostics');
  const names = {app_playwright:'App + Playwright', chromium:'Chromium', ffmpeg:'FFmpeg / H.264', xvfb:'Xvfb display', mediamtx:'MediaMTX'};
  const escape = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const number = value => typeof value === 'number' && Number.isFinite(value);
  const percent = value => number(value) ? `${value.toFixed(1)}%` : '—';
  const memory = value => number(value) ? `${(value / 1048576).toFixed(1)} MiB` : '—';
  function sparkline(values, label) {
    const finite = values.filter(number);
    if (finite.length < 2) return '<span class="sub">Collecting history…</span>';
    const max = Math.max(...finite, 1);
    // Null readings break the line rather than suggesting zero load or continuity.
    const paths = []; let segment = [];
    values.forEach((v, i) => {
      if (!number(v)) { if (segment.length) paths.push(segment.join(' ')); segment = []; return; }
      segment.push(`${i * 240 / Math.max(1, values.length - 1)},${38 - v / max * 34}`);
    });
    if (segment.length) paths.push(segment.join(' '));
    return `<svg viewBox="0 0 240 42" role="img" aria-label="${escape(label)}">${paths.map(points => `<polyline fill="none" stroke="currentColor" stroke-width="2" points="${points}"/>`).join('')}</svg>`;
  }
  function render(data) {
    const latest = data.latest;
    const heading = root.querySelector('.diag-status');
    heading.textContent = `${data.stale ? 'Stale / unavailable' : data.status === 'warming_up' ? 'Warming up' : 'Live'} · every ${data.sample_interval_seconds}s`;
    heading.className = `diag-status badge ${data.stale ? 'error' : 'running'}`;
    if (!latest) { root.querySelector('.diag-content').textContent = 'Waiting for Linux process counters. The streams are unaffected if diagnostics are unavailable.'; return; }
    const container = latest.container;
    const currentMemory = container.memory_working_set_bytes ?? container.memory_current_bytes;
    const memoryLabel = container.memory_working_set_bytes == null ? 'Container memory (includes cache)' : 'Container working memory';
    const cpuHistory = data.history.map(s => s.container.cpu_percent);
    const memHistory = data.history.map(s => s.container.memory_working_set_bytes ?? s.container.memory_current_bytes);
    const components = [...latest.components].sort((a,b) => (b.cpu_percent ?? -1) - (a.cpu_percent ?? -1));
    root.querySelector('.diag-content').innerHTML = `
      <div class="diag-summary">
        <div><span class="sub">Container CPU · one-core scale</span><strong>${percent(container.cpu_percent)}</strong>${sparkline(cpuHistory, 'Container CPU history')}</div>
        <div><span class="sub">${memoryLabel}</span><strong>${memory(currentMemory)}</strong>${sparkline(memHistory, 'Container memory history')}</div>
        <div><span class="sub">CPU capacity used</span><strong>${percent(container.cpu_capacity_percent)}</strong><span class="sub">of ${escape(latest.cpu_capacity_cores)} available CPU cores<br>Sample cost ${escape(latest.sample_cost_ms)} ms · age ${escape(data.sample_age_seconds)}s</span></div>
      </div>
      <div class="diag-table-wrap"><table class="diag-table"><thead><tr><th>Component / stream</th><th>CPU¹</th><th>RAM PSS²</th><th>RAM RSS²</th><th>Processes</th></tr></thead><tbody>${components.map(c => `<tr><td>${escape(names[c.component] || c.component)}${c.stream ? `<small>${escape(c.stream)}</small>` : ''}</td><td>${percent(c.cpu_percent)}</td><td>${memory(c.memory_pss_bytes)}</td><td>${memory(c.memory_rss_bytes)}</td><td>${escape(c.processes)}</td></tr>`).join('')}</tbody></table></div>
      <p class="sub diag-note">¹ 100% = one logical CPU; values can exceed 100%. CPU capacity used divides by the available core/quota count. ² PSS apportions shared pages; adding RSS double-counts shared memory. Container memory uses different accounting. “—” means warming up or unavailable, not zero.</p>
      <div class="diag-streams">${data.streams.map(s => `<div><b>${escape(s.name)}</b> <span class="badge ${escape(s.state)}">${escape(s.state)}</span> <span class="sub">${s.width}×${s.height} · configured ${s.configured_fps} FPS / ${s.configured_bitrate_kbps} kbps · ${s.consecutive_failures} consecutive failures${s.has_error ? ' · error reported' : ''}<br>Browser heartbeat: ${escape(s.last_browser_check || 'not yet available')}</span></div>`).join('')}</div>
      <p class="sub diag-note">FPS above is the configured capture rate, not measured NVR delivery. A browser heartbeat is not a decoded-frame check. History is kept in memory for about five minutes. No URLs, credentials, process arguments, or logs are included in the diagnostic download.</p>`;
  }
  async function poll() {
    if (!document.hidden) {
      try {
        const response = await fetch('api/diagnostics', {cache:'no-store'});
        if (!response.ok) throw new Error('Diagnostics request failed');
        render(await response.json());
      } catch {
        const heading = root.querySelector('.diag-status');
        heading.textContent = 'Diagnostics unavailable · displayed values may be stale';
        heading.className = 'diag-status badge error';
      }
    }
    setTimeout(poll, 5000);
  }
  poll();
})();
