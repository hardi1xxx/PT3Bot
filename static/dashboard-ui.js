/* Shared dashboard presentation; data and update rules remain with each page. */
(function () {
  'use strict';
  const escape = value => String(value ?? '').replace(/[&<>"']/g, char => ({
    '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;',
  }[char]));
  const number = value => new Intl.NumberFormat('id-ID').format(value || 0);

  window.renderIssueAnalysis = function (target, items, onSelect) {
    const metric = target.dataset.issueMetric || 'lop';
    const sorted = items.slice().sort((a, b) =>
      (metric === 'port' ? b.port - a.port : b.count - a.count) || a.label.localeCompare(b.label, 'id'));
    const maximum = Math.max(1, ...sorted.map(item => metric === 'port' ? item.port : item.count));
    const dominant = sorted[0];
    const unit = metric === 'port' ? 'Port' : 'LOP';
    let advice = ['Verifikasi penyebab bersama branch dan mitra', 'Lengkapi keterangan serta dokumen pendukung sebelum tindak lanjut.'];
    if (dominant && /izin|lingkungan/i.test(dominant.label)) advice = ['Prioritaskan koordinasi perizinan', 'Konfirmasi hambatan dan jadwal tindak lanjut bersama pihak terkait.'];
    if (dominant && /duplik/i.test(dominant.label)) advice = ['Tinjau duplikasi sebelum tindak lanjut', 'Cocokkan IHLD dan lokasi untuk memastikan order yang perlu diproses.'];
    target.classList.remove('bh-grid');
    target.innerHTML = `<div class="issue-analysis-layout">
      <section class="issue-chart-panel" aria-label="Grafik kategori Drop dan Kendala">
        <div class="issue-panel-heading"><strong>Jumlah ${unit} per Kategori</strong>
          <div class="issue-metric" role="group" aria-label="Satuan grafik">
            ${['lop','port'].map(key => `<button type="button" data-issue-metric="${key}" class="${metric === key ? 'active' : ''}" aria-pressed="${metric === key}">${key === 'lop' ? 'LOP' : 'Port'}</button>`).join('')}
          </div>
        </div><p class="issue-help">Klik batang untuk melihat daftar lokasi.</p>
        <div class="issue-bars">${sorted.length ? sorted.map((item, index) => {
          const value = metric === 'port' ? item.port : item.count;
          return `<button type="button" class="issue-bar-row" data-issue-index="${index}" aria-label="${escape(item.label)}: ${number(value)} ${unit}">
            <span class="issue-bar-label">${escape(item.label)}</span>
            <span class="issue-bar-track"><span class="issue-bar-fill" style="width:${100 * value / maximum}%"></span></span>
            <strong>${number(value)}</strong></button>`;
        }).join('') : '<div class="empty-state">Tidak ada data Drop atau Kendala pada filter ini.</div>'}</div>
        <div class="issue-axis">Jumlah ${unit}</div>
      </section>
      <aside class="issue-insights"><div class="issue-panel-heading"><strong>Analisa &amp; Tindak Lanjut</strong><span class="issue-data-badge">Data aktual</span></div>
        ${dominant ? `<div class="issue-highlight"><span aria-hidden="true">◉</span><div>Kategori terbesar pada filter ini:<strong>${escape(dominant.label)}</strong><b>${number(metric === 'port' ? dominant.port : dominant.count)} ${unit}</b></div></div>
        <ol class="issue-actions"><li><strong>${advice[0]}</strong><span>${advice[1]}</span></li><li><strong>Tetapkan tindak lanjut per lokasi</strong><span>Periksa daftar LOP, koordinasikan penanggung jawab, dan catat perkembangan terbaru.</span></li></ol>
        <button type="button" class="issue-open-btn">Lihat daftar LOP <span aria-hidden="true">→</span></button>` : '<p class="issue-help">Analisa akan muncul ketika ada data pada kategori ini.</p>'}
      </aside></div>`;
    target.onclick = event => {
      const toggle = event.target.closest('button[data-issue-metric]');
      if (toggle) {
        target.dataset.issueMetric = toggle.dataset.issueMetric;
        window.renderIssueAnalysis(target, items, onSelect);
        return;
      }
      const bar = event.target.closest('[data-issue-index]');
      if (bar) onSelect(sorted[Number(bar.dataset.issueIndex)]);
      if (event.target.closest('.issue-open-btn') && dominant) onSelect(dominant);
    };
  };

  window.layoutProgressPanel = function (panel) {
    if (panel.querySelector('.progress-editor-grid')) return;
    panel.classList.add('progress-editor');
    const heading = document.createElement('h3');
    heading.className = 'progress-editor-title';
    heading.textContent = 'Informasi & Pembaruan Progress';
    const overview = document.createElement('div');
    overview.className = 'progress-overview';
    const grid = document.createElement('div');
    grid.className = 'progress-editor-grid';
    const history = document.createElement('section');
    history.className = 'progress-history-column';
    const fields = document.createElement('section');
    fields.className = 'progress-fields-column';
    const actions = document.createElement('div');
    actions.className = 'progress-editor-actions';
    Array.from(panel.children).forEach(child => {
      if (child.tagName === 'STYLE' || child.tagName === 'SCRIPT') return;
      if (child.classList.contains('update-history')) { history.append(child); return; }
      if (child.classList.contains('field')) {
        if (child.querySelector('#status_z, #status_aa')) child.classList.add('progress-half-field');
        const documentField = child.querySelector('#documentsContainer, #kmlContainer, #boqContainer, #standaloneKml, #standaloneBoq, input[type="file"]') || /Dokumen LOP/.test(child.querySelector('label')?.textContent || '');
        (documentField ? history : fields).append(child);
        return;
      }
      if (child.id === 'extraFieldsContainer' || child.id === 'updateMsg') { fields.append(child); return; }
      if (child.tagName === 'BUTTON' || child.tagName === 'A') { actions.append(child); return; }
      if (child.classList.contains('card') || child.classList.contains('badge') || child.classList.contains('sub')) overview.append(child);
    });
    if (!history.children.length) history.innerHTML = '<div class="empty-state">Belum ada riwayat keterangan atau dokumen pada LOP ini.</div>';
    grid.append(history, fields);
    panel.prepend(heading, overview, grid);
    panel.append(actions);
    panel.addEventListener('dragover', event => {
      if (event.target.closest('.progress-history-column')) event.preventDefault();
    });
    panel.addEventListener('drop', event => {
      const zone = event.target.closest('.progress-history-column .card, .progress-history-column .field');
      const input = zone?.querySelector('input[type="file"]:not(:disabled)');
      if (!input || !event.dataTransfer?.files.length) return;
      event.preventDefault();
      input.files = event.dataTransfer.files;
      input.dispatchEvent(new Event('change', { bubbles:true }));
    });
  };

  window.renderLopPicker = function (target, rows, selectedRow, onSelect) {
    target.innerHTML = rows.length ? rows.map(row => `<button type="button" class="lop-choice ${row.row === selectedRow ? 'selected' : ''}" data-lop-row="${row.row}">
      <span class="lop-file-icon" aria-hidden="true">▤</span><span class="lop-choice-info">
      <span class="lop-choice-id">${escape(row.ihld || '(tanpa IHLD)')}${row.priority_bx ? `<span class="lop-priority">▼ ${escape(row.priority_bx)}</span>` : ''}</span>
      <span class="lop-choice-location">${escape(row.lokasi || '-')}</span>
      <span class="lop-choice-meta">${escape(row.batch || '-')} · ${escape(row.regional || row.branch || '-')}</span></span>
      <span class="lop-choice-status">${escape(row.status_raw || row.status || '-')}</span><span aria-hidden="true">›</span></button>`).join('') : '<div class="empty-state">Tidak ada LOP sesuai pencarian dan filter.</div>';
    target.onclick = event => {
      const choice = event.target.closest('[data-lop-row]');
      if (choice) onSelect(Number(choice.dataset.lopRow));
    };
  };

  window.initStandaloneAttachments = function (row) {
    ['kml', 'boq'].forEach(kind => {
      const target = document.getElementById(kind === 'kml' ? 'standaloneKml' : 'standaloneBoq');
      if (!target) return;
      target.innerHTML = `<div class="attachment-upload"><input type="file" aria-label="Pilih file ${kind.toUpperCase()}" accept="${kind === 'kml' ? '.kml,.kmz' : '.pdf,.xlsx,.xls'}"><button type="button" class="btn-ghost btn-sm">Upload ${kind.toUpperCase()}</button></div><div class="attachment-message" role="status"></div><div class="attachment-list"></div>`;
      const input = target.querySelector('input');
      const button = target.querySelector('button');
      const message = target.querySelector('.attachment-message');
      const list = target.querySelector('.attachment-list');
      async function load() {
        list.innerHTML = '<div class="skeleton">Memuat dokumen...</div>';
        try {
          const response = await fetch(`/api/row/${row}/${kind}`);
          const data = await response.json();
          if (!response.ok || !data.ok) throw new Error(data.error || 'Gagal memuat dokumen.');
          list.innerHTML = data.files.length ? data.files.map(file => `<p class="sub"><a href="${escape(file.url)}" target="_blank" rel="noopener">${escape(file.name)}</a></p>`).join('') : '<p class="sub">Belum ada dokumen yang diupload.</p>';
        } catch (error) { list.innerHTML = `<p class="flash error">${escape(error.message)}</p>`; }
      }
      button.addEventListener('click', async () => {
        if (!input.files.length) { message.textContent = 'Pilih file terlebih dahulu.'; return; }
        button.disabled = true;
        message.textContent = 'Mengupload...';
        const body = new FormData();
        body.append('file', input.files[0]);
        try {
          const response = await fetch(`/api/row/${row}/${kind}`, { method:'POST', body });
          const data = await response.json();
          if (!response.ok || !data.ok) throw new Error(data.error || 'Gagal mengupload.');
          message.textContent = 'Upload berhasil.';
          input.value = '';
          await load();
        } catch (error) { message.textContent = error.message; }
        finally { button.disabled = false; }
      });
      if (kind === 'kml') {
        const status = document.getElementById('status_z');
        const sync = () => { target.querySelector('.attachment-upload').hidden = !JSON.parse(target.dataset.visibleStatuses || '[]').includes(status.value); };
        status.addEventListener('change', sync);
        status.form?.addEventListener('reset', () => requestAnimationFrame(sync));
        sync();
      }
      load();
    });
  };

  window.initStandaloneLopPicker = async function (selectedRow) {
    const target = document.getElementById('standaloneLopList');
    const search = document.getElementById('standaloneLopSearch');
    const tabs = document.getElementById('standaloneLopTabs');
    if (!target) return;
    let rows = [], stage = '';
    function render() {
      const query = search.value.trim().toLowerCase();
      const filtered = rows.filter(row => (!stage || (row.status_raw || '').toUpperCase().includes(stage))
        && (!query || `${row.ihld} ${row.lokasi}`.toLowerCase().includes(query)));
      window.renderLopPicker(target, filtered, selectedRow, row => {
        if (row === selectedRow) return;
        if (document.querySelector('.update-form')?.dataset.dirty === '1' && !window.confirm('Ada perubahan yang belum disimpan. Pindah ke LOP lain?')) return;
        window.location.href = `/update/${row}`;
      });
    }
    search.addEventListener('input', render);
    tabs.addEventListener('click', event => {
      const button = event.target.closest('[data-lop-stage]');
      if (!button) return;
      stage = button.dataset.lopStage;
      tabs.querySelectorAll('button').forEach(tab => { tab.classList.toggle('active', tab === button); tab.setAttribute('aria-pressed', String(tab === button)); });
      render();
    });
    document.querySelector('.update-form')?.addEventListener('input', event => { event.currentTarget.dataset.dirty = '1'; });
    try {
      const response = await fetch('/api/dashboard');
      const data = await response.json();
      if (!response.ok || !data.ok) throw new Error(data.error || 'Gagal memuat pilihan LOP.');
      rows = data.data.rows || [];
      render();
    } catch (error) { target.innerHTML = `<div class="flash error">${escape(error.message)}</div>`; }
  };
})();
