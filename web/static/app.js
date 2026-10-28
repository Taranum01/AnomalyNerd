// AnomalyNerd single-page results: async submit, loader, render, clickable category filters.
(function () {
  const form = document.getElementById('form');
  const fileInput = document.getElementById('file');
  const fname = document.getElementById('fname');
  const btn = document.getElementById('analyzeBtn');
  const welcome = document.getElementById('welcome');
  const loading = document.getElementById('loading');
  const results = document.getElementById('results');
  const errorbox = document.getElementById('errorbox');

  fileInput.addEventListener('change', () => {
    fname.textContent = fileInput.files.length ? fileInput.files[0].name : 'No file chosen';
  });

  // category definitions for the clickable summary boxes
  const CATS = [
    { key: 'HIGH', label: 'High priority', cls: 'high' },
    { key: 'MEDIUM', label: 'Medium', cls: 'med' },
    { key: 'LOW', label: 'Low / described', cls: 'low' },
    { key: 'CANNOT', label: 'Could not check', cls: 'cannot' },
  ];
  let selected = new Set();   // active category filters

  form.addEventListener('submit', async (e) => {
    e.preventDefault();
    if (!fileInput.files.length) return;

    // show loader, hide the rest
    welcome.classList.add('hidden');
    results.classList.add('hidden');
    errorbox.classList.add('hidden');
    loading.classList.remove('hidden');
    btn.disabled = true;

    try {
      const fd = new FormData();
      fd.append('file', fileInput.files[0]);
      fd.append('expect_flat', document.getElementById('expect_flat').value || '');
      const resp = await fetch('/analyze', { method: 'POST', body: fd });
      if (!resp.ok) {
        const detail = await resp.json().catch(() => ({ detail: resp.statusText }));
        throw new Error(detail.detail || ('HTTP ' + resp.status));
      }
      const data = await resp.json();
      render(data);
    } catch (err) {
      loading.classList.add('hidden');
      errorbox.textContent = 'Could not analyze this file: ' + err.message;
      errorbox.classList.remove('hidden');
    } finally {
      btn.disabled = false;
    }
  });

  function render(data) {
    loading.classList.add('hidden');
    selected = new Set();

    document.getElementById('resFile').innerHTML =
      '<b>' + esc(data.filename) + '</b> &middot; read as ' + esc(data.source_kind) +
      ' &middot; ' + data.n_tables + ' tables analyzed, ' + data.n_flags + ' flags';

    // clickable category summary boxes
    const summary = document.getElementById('summary');
    summary.innerHTML = '';
    CATS.forEach(c => {
      const n = data.counts[c.key] || 0;
      const box = document.createElement('div');
      box.className = 'box ' + c.cls;
      box.dataset.cat = c.key;
      box.innerHTML = '<div class="n">' + n + '</div><div class="l">' + c.label + '</div>';
      box.addEventListener('click', () => toggleCat(c.key, box));
      summary.appendChild(box);
    });

    // flag cards, each tagged with its priority (category) for filtering
    const flagsArea = document.getElementById('flagsArea');
    flagsArea.innerHTML = '<h2>What we checked and found</h2>';
    const allFlags = [];
    data.tables.forEach(t => t.flags.forEach(f => allFlags.push(f)));
    if (!allFlags.length) {
      flagsArea.innerHTML += '<div class="none">No notable anomalies were flagged.</div>';
    } else {
      allFlags.forEach(f => {
        const d = document.createElement('div');
        d.className = 'flag ' + f.priority;
        d.dataset.cat = f.priority;      // HIGH / MEDIUM / LOW
        d.innerHTML =
          '<span class="badge">' + esc(f.priority) + '</span>' +
          '<span class="type">' + esc(f.type) + (f.axis ? ' along ' + esc(f.axis) : '') +
          ' &middot; ' + esc(f.table) + '</span>' +
          '<div class="sug">' + esc(f.suggestion) + '</div>';
        flagsArea.appendChild(d);
      });
    }

    // reported statistics that were recomputed and agreed (flagged ones are cards above)
    const okArea = document.getElementById('statOkArea');
    okArea.innerHTML = '';
    if (data.stat_ok && data.stat_ok.length) {
      okArea.innerHTML = '<h2>Reported statistics that checked out</h2>';
      const wrap = document.createElement('div');
      wrap.className = 'cnc';
      data.stat_ok.forEach(c => {
        const it = document.createElement('div');
        it.className = 'item'; it.textContent = c; wrap.appendChild(it);
      });
      okArea.appendChild(wrap);
    }

    // could-not-check list, tagged CANNOT so the filter can show it
    const cnc = document.getElementById('cncArea');
    cnc.innerHTML = '';
    if (!data.could_not_check.length) {
      cnc.innerHTML = '<div class="none">Everything detected was analyzable.</div>';
    } else {
      const wrap = document.createElement('div');
      wrap.className = 'cnc';
      wrap.dataset.catgroup = 'CANNOT';
      data.could_not_check.forEach(c => {
        const it = document.createElement('div');
        it.className = 'item'; it.textContent = c; wrap.appendChild(it);
      });
      cnc.appendChild(wrap);
    }

    results.classList.remove('hidden');
    applyFilter();
  }

  // ---- clicking a category box filters what is shown ----
  function toggleCat(key, box) {
    if (selected.has(key)) { selected.delete(key); box.classList.remove('active'); }
    else { selected.add(key); box.classList.add('active'); }
    applyFilter();
  }

  function applyFilter() {
    const showAll = selected.size === 0;
    // flag cards
    document.querySelectorAll('#flagsArea .flag').forEach(el => {
      el.style.display = (showAll || selected.has(el.dataset.cat)) ? '' : 'none';
    });
    // the could-not-check section (its own category)
    const cncSection = document.querySelector('#cncArea .cnc');
    const cncHeader = document.querySelector('#results h2:last-of-type');
    const showCnc = showAll || selected.has('CANNOT');
    if (cncSection) cncSection.style.display = showCnc ? '' : 'none';
    // when ONLY cannot is selected, dim the flags header area is handled by display above
    const hint = document.getElementById('filterHint');
    if (showAll) {
      hint.innerHTML = 'Showing everything. Click a box above to filter.';
    } else {
      const names = [...selected].join(', ');
      hint.innerHTML = 'Filtered to: <b>' + esc(names) + '</b> &middot; <a id="clearF">clear</a>';
      const cl = document.getElementById('clearF');
      if (cl) cl.addEventListener('click', clearFilter);
    }
  }

  function clearFilter() {
    selected = new Set();
    document.querySelectorAll('.box.active').forEach(b => b.classList.remove('active'));
    applyFilter();
  }

  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }
})();
