(() => {
  const app = document.querySelector('#app');
  const toastEl = document.querySelector('#toast');
  let allItems = [];
  let adminPoll = null;
  let adminContentStatus = 'active';
  let adminContentQuery = '';
  let adminContentTimer = null;
  let searchSequence = 0;
  let topbarLastScrollY = window.scrollY;
  let topbarScrollDirection = 0;
  let topbarScrollDistance = 0;
  let folderPreview = null;
  let hlsInstance = null;
  let playerKeyHandler = null;
  let playerCleanup = null;

  const esc = (value = '') => String(value).replace(/[&<>"']/g, (char) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  })[char]);
  const route = () => location.hash.slice(1) || '/';
  const api = async (url, options = {}) => {
    const response = await fetch(url, {
      credentials: 'same-origin',
      ...options,
      headers: { ...(options.body ? { 'Content-Type': 'application/json' } : {}), ...(options.headers || {}) }
    });
    const type = response.headers.get('content-type') || '';
    const body = type.includes('application/json') ? await response.json() : await response.text();
    if (!response.ok) throw new Error(body?.detail || `درخواست انجام نشد (${response.status})`);
    return body;
  };
  const toast = (message, tone = 'ok') => {
    toastEl.textContent = message;
    toastEl.dataset.tone = tone;
    toastEl.classList.add('show');
    window.clearTimeout(toastEl._timer);
    toastEl._timer = window.setTimeout(() => toastEl.classList.remove('show'), 3400);
  };
  const yearText = (year) => year ? `<span>${esc(year)}</span>` : '';
  const poster = (item, extra = '') => item.poster
    ? `<img class="poster-img ${extra}" src="${esc(item.poster)}" alt="پوستر ${esc(item.name || item.title || '')}" loading="lazy">`
    : `<div class="poster-placeholder ${extra}" aria-hidden="true"><span class="brand-mark"></span></div>`;
  const titleCard = (item) => `
    <a class="title-card" href="#/title/${item.id}" aria-label="${esc(item.name)}">
      <div class="poster-wrap">${poster(item)}
        <span class="card-play" aria-hidden="true"><span>▶</span></span>
        ${item.pending_count > 0 ? `<span class="episode-count">${item.ready_count ? `${item.ready_count} از ${item.episode_count} آماده` : 'در حال آماده‌سازی'}</span>` : item.episode_count > 1 ? `<span class="episode-count">${item.episode_count} قسمت</span>` : ''}
      </div>
      <div class="card-copy"><strong>${esc(item.name)}</strong><div class="card-meta">${item.kind === 'series' ? 'سریال' : 'فیلم'} ${yearText(item.year)}</div></div>
    </a>`;
  const continueCard = (entry) => {
    const { title, episode, percent } = entry;
    const episodeName = episode.name || (title.kind === 'series' ? `قسمت ${episode.number}` : 'فیلم');
    const seasonText = title.kind === 'series'
      ? `${episode.section_name ? `${episode.section_name} · ` : ''}${episode.season_name || `فصل ${episode.season}`} · قسمت ${episode.number}`
      : `${episode.duration ? `${Math.round(episode.duration / 60)} دقیقه` : 'فیلم'}`;
    return `<article class="continue-episode-card"><a class="continue-episode-link" href="#/watch/${episode.id}" aria-label="ادامهٔ ${esc(title.name)}، ${esc(episodeName)}"><span class="continue-episode-art">${episode.poster || title.poster ? `<img src="${esc(episode.poster || title.poster)}" alt="" loading="lazy">` : '<span class="brand-mark"></span>'}<i>▶</i></span><span class="continue-episode-copy"><small>${esc(title.name)}</small><strong>${esc(episodeName)}</strong><span>${esc(seasonText)}</span><em>${Math.round(percent)}٪ تماشا شده</em></span></a><div class="progress-line"><i style="width:${Math.min(100, Math.max(3, Math.round(percent)))}%"></i></div></article>`;
  };
  const shell = (content, active = '') => `
    <header class="topbar">
      <a class="brand" href="#/" aria-label="صفحه اصلی لویت"><span class="brand-mark"><i></i></span><span>لویت</span></a>
      <nav class="main-nav" aria-label="بخش‌های محتوا">
        <a class="${active === 'all' ? 'active' : ''}" href="#/">خانه</a>
        <a class="${active === 'movie' ? 'active' : ''}" href="#/browse/movie">فیلم‌ها</a>
        <a class="${active === 'series' ? 'active' : ''}" href="#/browse/series">سریال‌ها</a>
        <a class="${active === 'continue' ? 'active' : ''}" href="#/continue">ادامه تماشا</a>
      </nav>
      <div class="search-container"><label class="search-box"><span class="search-icon" aria-hidden="true">⌕</span><input id="global-search" type="search" placeholder="جست‌وجوی فیلم، سریال یا قسمت" autocomplete="off" aria-label="جست‌وجو" aria-controls="search-popover" aria-expanded="false"></label><div id="search-popover" class="search-popover" role="listbox" aria-label="نتایج جست‌وجو" hidden></div></div>
      <a class="admin-shortcut" href="#/admin"><span class="admin-dot"></span><span>مدیریت</span></a>
    </header>
    <main>${content}</main>
    <div id="page-scroll-fade" aria-hidden="true"></div>
    <button id="back-to-top" class="back-to-top" type="button" aria-label="رفتن به بالای صفحه" title="رفتن به بالای صفحه"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 4 4.5 11.5l1.4 1.4 5.1-5.1V20h2V7.8l5.1 5.1 1.4-1.4z" fill="currentColor"/></svg></button>
    <footer class="site-footer"><a class="brand brand-small" href="#/"><span class="brand-mark"><i></i></span><span>لویت</span></a><span>سینمای خانه، روی شبکه خودت</span><span class="footer-status"><i></i> شبکه داخلی</span></footer>`;

  function progressRecord(id) {
    try {
      const stored = JSON.parse(localStorage.getItem(`loyt.progress.${id}`) || 'null');
      return stored && typeof stored === 'object' ? stored : { percent: Number(stored) || 0 };
    } catch { return { percent: 0 }; }
  }
  function progressForEpisode(titleId, episodeId) {
    try {
      const episodeProgress = JSON.parse(localStorage.getItem(`loyt.progress.${titleId}.${episodeId}`) || 'null');
      if (episodeProgress && Number.isFinite(Number(episodeProgress.percent))) return Number(episodeProgress.percent);
      const legacy = JSON.parse(localStorage.getItem(`loyt.progress.${titleId}`) || 'null');
      return legacy && Number(legacy.episodeId) === Number(episodeId) ? Number(legacy.percent) || 0 : 0;
    } catch { return 0; }
  }
  function renderHome(items, kind = 'all', query = '') {
    const visible = items.filter((item) => kind === 'all' || item.kind === kind);
    const first = visible[0];
    const hero = first ? `
      <section class="hero" style="--hero-image:url('${esc(first.poster || '')}')">
        <div class="hero-image"></div><div class="hero-shade"></div>
        <div class="hero-copy"><span class="hero-kicker"><i></i>${first.kind === 'series' ? 'پیشنهاد سریال' : 'پیشنهاد تماشا'}</span>
          <h1>${esc(first.name)}</h1><p>${esc(first.synopsis || 'یک انتخاب تازه برای تماشای امشب.')}</p>
          <div class="hero-meta"><span>${first.kind === 'series' ? 'سریال' : 'فیلم'}</span>${yearText(first.year)}<span>${first.pending_count ? `${first.ready_count} از ${first.episode_count} قسمت آماده` : `${first.episode_count} قسمت آماده`}</span></div>
          <a class="button button-primary" href="#/title/${first.id}"><span class="button-play">▶</span> ${first.kind === 'series' ? 'دیدن سریال' : 'تماشای فیلم'}</a>
        </div><div class="hero-corner" aria-hidden="true"></div>
      </section>` : `
      <section class="hero hero-empty"><div class="hero-empty-art"><span class="empty-frame"><i></i></span><span class="orbit orbit-one"></span><span class="orbit orbit-two"></span></div>
        <div class="hero-copy"><span class="hero-kicker"><i></i> سینمای شبکه تو</span><h1>قصه‌ها، همین‌جا<br>روی شبکه خودت.</h1>
          <p>${query ? 'چیزی با این عبارت پیدا نکردیم. املای عنوان را بررسی کن.' : 'فیلم‌ها و سریال‌های مجموعه‌ات را در یک کتابخانه مرتب و خوش‌تماشا جمع کن.'}</p>
          <a class="button button-primary" href="#/admin"><span class="button-play">＋</span> ${query ? 'رفتن به مدیریت' : 'افزودن محتوا'}</a>
        </div></section>`;
    const movies = visible.filter((item) => item.kind === 'movie');
    const series = visible.filter((item) => item.kind === 'series');
    const all = visible.map(titleCard).join('');
    const sections = query ? `<section class="shelf"><div class="section-heading"><div><span class="section-rule"></span><h2>نتایج جست‌وجو</h2></div><span class="section-note">${visible.length} عنوان</span></div>${visible.length ? `<div class="title-grid">${all}</div>` : `<div class="empty-inline">نتیجه‌ای برای «${esc(query)}» پیدا نشد.</div>`}</section>` : kind !== 'all' ? `<section class="shelf"><div class="section-heading"><div><span class="section-rule"></span><h2>${kind === 'movie' ? 'فیلم‌ها' : 'سریال‌ها'}</h2></div><span class="section-note">${visible.length} عنوان</span></div>${visible.length ? `<div class="title-grid">${all}</div>` : `<div class="empty-inline">هنوز محتوایی در این بخش نیست.</div>`}</section>` : `
      ${movies.length ? `<section class="shelf"><div class="section-heading"><div><span class="section-rule"></span><h2>فیلم‌ها</h2></div><a class="section-link" href="#/browse/movie">همه فیلم‌ها <span>‹</span></a></div><div class="title-grid">${movies.slice(0, 6).map(titleCard).join('')}</div></section>` : ''}
      ${series.length ? `<section class="shelf"><div class="section-heading"><div><span class="section-rule"></span><h2>سریال‌ها</h2></div><a class="section-link" href="#/browse/series">همه سریال‌ها <span>‹</span></a></div><div class="title-grid">${series.slice(0, 6).map(titleCard).join('')}</div></section>` : ''}
      ${!movies.length && !series.length && first ? `<section class="shelf"><div class="section-heading"><div><span class="section-rule"></span><h2>تازه در کتابخانه</h2></div></div><div class="title-grid">${all}</div></section>` : ''}
      ${!visible.length && !query ? `<section class="first-step"><span class="first-step-mark">✳</span><div><h2>کتابخانه هنوز خالی است</h2><p>از پنل مدیریت یک فیلم اضافه کن یا یک فصل را از روی پوشه وارد کن.</p></div><a class="button button-quiet" href="#/admin">رفتن به مدیریت <span>‹</span></a></section>` : ''}`;
    app.innerHTML = shell(`<div class="home-wrap">${hero}${sections}</div>`, kind);
    const searchInput = document.querySelector('#global-search');
    if (searchInput) searchInput.value = query;
    bindSearch();
  }

  async function loadBrowse(kind = 'all', query = '') {
    try {
      const data = await api(`/api/catalog?kind=${encodeURIComponent(kind)}&q=${encodeURIComponent(query)}`);
      allItems = data.items || [];
      renderHome(allItems, kind, query);
    } catch (error) {
      app.innerHTML = shell(`<section class="state-panel"><span class="state-symbol">!</span><h1>کتابخانه در دسترس نیست</h1><p>${esc(error.message)}</p><button class="button button-quiet" id="retry-home">تلاش دوباره</button></section>`);
      document.querySelector('#retry-home')?.addEventListener('click', () => loadBrowse(kind, query));
    }
  }

  function readContinueCandidates(catalog) {
    const availableTitles = new Map(catalog.map((item) => [Number(item.id), item]));
    const byEpisode = new Map();
    try {
      for (let index = 0; index < localStorage.length; index += 1) {
        const key = localStorage.key(index) || '';
        const match = key.match(/^loyt\.progress\.(\d+)\.(\d+)$/);
        if (!match) continue;
        const titleId = Number(match[1]);
        if (!availableTitles.has(titleId)) continue;
        let raw;
        try { raw = JSON.parse(localStorage.getItem(key) || 'null'); } catch { continue; }
        const record = raw && typeof raw === 'object' ? raw : { percent: Number(raw) || 0 };
        const percent = Number(record.percent);
        if (!(percent > 0 && percent < 100)) continue;
        byEpisode.set(`${titleId}.${Number(match[2])}`, {
          titleId, episodeId: Number(match[2]), percent,
          updatedAt: Number(record.updatedAt) || 0,
        });
      }
      for (const titleId of availableTitles.keys()) {
        const legacy = progressRecord(titleId);
        const episodeId = Number(legacy.episodeId);
        const percent = Number(legacy.percent) || 0;
        if (!episodeId || !(percent > 0 && percent < 100)) continue;
        const key = `${titleId}.${episodeId}`;
        const existing = byEpisode.get(key);
        if (!existing || (Number(legacy.updatedAt) || 0) > existing.updatedAt) {
          byEpisode.set(key, { titleId, episodeId, percent, updatedAt: Number(legacy.updatedAt) || 0 });
        }
      }
    } catch { return []; }
    return [...byEpisode.values()].sort((a, b) => b.updatedAt - a.updatedAt);
  }

  async function loadContinue() {
    try {
      const data = await api('/api/catalog?kind=all');
      allItems = data.items || [];
      const candidates = readContinueCandidates(allItems);
      const titleById = new Map(allItems.map((item) => [Number(item.id), item]));
      const byTitle = new Map();
      candidates.forEach((item) => {
        if (!byTitle.has(item.titleId)) byTitle.set(item.titleId, []);
        byTitle.get(item.titleId).push(item);
      });
      const titleGroups = [...byTitle.entries()];
      const episodeMaps = [];
      for (let index = 0; index < titleGroups.length; index += 8) {
        const batch = await Promise.all(titleGroups.slice(index, index + 8).map(async ([titleId]) => {
          try {
            const details = await api(`/api/title/${titleId}`);
            return [titleId, new Map(details.episodes.map((episode) => [Number(episode.id), episode]))];
          } catch { return [titleId, new Map()]; }
        }));
        episodeMaps.push(...batch);
      }
      const episodesByTitle = new Map(episodeMaps);
      const items = candidates.flatMap((record) => {
        const title = titleById.get(record.titleId);
        const episode = episodesByTitle.get(record.titleId)?.get(record.episodeId);
        return title && episode ? [{ title, episode, percent: record.percent, updatedAt: record.updatedAt }] : [];
      }).sort((a, b) => b.updatedAt - a.updatedAt);
      const body = `<div class="home-wrap continue-page"><section class="continue-heading"><span class="hero-kicker"><i></i> کتابخانهٔ شخصی تو</span><h1>ادامهٔ تماشا</h1><p>هر قسمت را از همان جایی که رها کرده‌ای ادامه بده.</p></section>
        <section class="shelf"><div class="section-heading"><div><span class="section-rule"></span><h2>ویدیوهای نیمه‌تمام</h2></div><span class="section-note">${items.length} قسمت</span></div>
        ${items.length ? `<div class="continue-episode-grid">${items.map(continueCard).join('')}</div>` : `<div class="empty-inline">هنوز قسمتی را شروع نکرده‌ای؛ بعد از کمی تماشا، همان قسمت را اینجا پیدا می‌کنی.</div>`}</section></div>`;
      app.innerHTML = shell(body, 'continue');
      bindSearch();
    } catch (error) {
      app.innerHTML = shell(`<section class="state-panel"><span class="state-symbol">!</span><h1>ادامهٔ تماشا در دسترس نیست</h1><p>${esc(error.message)}</p></section>`, 'continue');
      bindSearch();
    }
  }

  let searchTimer;
  const searchEpisodeMeta = (item) => item.kind === 'series'
    ? `${item.section_name ? `${item.section_name} · ` : ''}${item.season_name || `فصل ${item.season}`} · قسمت ${item.number}`
    : 'فیلم تک‌قسمتی';

  function searchHit(item, type) {
    const episode = type === 'episode';
    const href = episode ? `#/watch/${item.id}` : `#/title/${item.id}`;
    const image = item.poster ? `<img src="${esc(item.poster)}" alt="" loading="lazy">` : '<span class="brand-mark"></span>';
    const title = episode ? `${item.title_name} · ${item.name || `قسمت ${item.number}`}` : item.name;
    const subtitle = episode
      ? searchEpisodeMeta(item)
      : `${item.kind === 'series' ? 'سریال' : 'فیلم'}${item.year ? ` · ${item.year}` : ''}`;
    return `<a class="search-hit" role="option" href="${href}"><span class="search-hit-art">${image}</span><span class="search-hit-copy"><strong>${esc(title)}</strong><small>${esc(subtitle)}</small></span><span class="search-hit-arrow">‹</span></a>`;
  }

  function searchPopoverMarkup(data, query) {
    const titles = data.titles || [];
    const episodes = data.episodes || [];
    const total = titles.length + episodes.length;
    return `<div class="search-popover-head"><span>نتایج جست‌وجو</span><small>${total} مورد</small></div>
      ${titles.length ? `<div class="search-group-label">فیلم‌ها و سریال‌ها</div>${titles.map((item) => searchHit(item, 'title')).join('')}` : ''}
      ${episodes.length ? `<div class="search-group-label">قسمت‌ها</div>${episodes.slice(0, 6).map((item) => searchHit(item, 'episode')).join('')}` : ''}
      ${total ? `<a class="search-more" href="#/search/${encodeURIComponent(query)}">دیدن همهٔ نتایج «${esc(query)}» <span>‹</span></a>` : `<div class="search-empty">چیزی با این عبارت پیدا نشد.</div>`}`;
  }

  function bindSearch() {
    const input = document.querySelector('#global-search');
    const panel = document.querySelector('#search-popover');
    if (!input || !panel) return;
    const closePanel = () => { panel.hidden = true; input.setAttribute('aria-expanded', 'false'); };
    const search = async () => {
      const query = input.value.trim();
      const sequence = ++searchSequence;
      if (query.length < 2) { closePanel(); return; }
      panel.hidden = false;
      input.setAttribute('aria-expanded', 'true');
      panel.innerHTML = '<div class="search-empty">در حال جست‌وجو…</div>';
      try {
        const data = await api(`/api/search?q=${encodeURIComponent(query)}`);
        if (sequence !== searchSequence || !input.isConnected) return;
        panel.innerHTML = searchPopoverMarkup(data, query);
      } catch (error) {
        if (sequence !== searchSequence || !input.isConnected) return;
        panel.innerHTML = `<div class="search-empty">${esc(error.message)}</div>`;
      }
    };
    input.addEventListener('input', () => {
      searchSequence += 1;
      clearTimeout(searchTimer);
      searchTimer = setTimeout(search, 180);
    });
    input.addEventListener('keydown', (event) => {
      if (event.key === 'Escape') { closePanel(); return; }
      if (event.key === 'Enter') {
        const query = input.value.trim();
        if (query.length >= 2) { closePanel(); location.hash = `/search/${encodeURIComponent(query)}`; }
        return;
      }
      if (event.key === 'ArrowDown') {
        const first = panel.querySelector('.search-hit, .search-more');
        if (first) { event.preventDefault(); first.focus(); }
      }
    });
    panel.addEventListener('click', (event) => { if (event.target.closest('a')) closePanel(); });
    panel.addEventListener('keydown', (event) => {
      if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return;
      const links = [...panel.querySelectorAll('.search-hit, .search-more')];
      const index = links.indexOf(document.activeElement);
      const next = links[index + (event.key === 'ArrowDown' ? 1 : -1)];
      if (next) { event.preventDefault(); next.focus(); }
      else if (event.key === 'ArrowUp') { event.preventDefault(); input.focus(); }
    });
  }

  async function loadSearch(query) {
    try {
      const data = await api(`/api/search?q=${encodeURIComponent(query)}`);
      const titleResults = data.titles || [];
      const episodeResults = data.episodes || [];
      const content = `<div class="home-wrap search-page"><section class="continue-heading"><span class="hero-kicker"><i></i> جست‌وجوی کتابخانه</span><h1>نتایج «${esc(query)}»</h1><p>${titleResults.length + episodeResults.length} نتیجه در فیلم‌ها، سریال‌ها و قسمت‌ها</p></section>
        ${titleResults.length ? `<section class="shelf"><div class="section-heading"><div><span class="section-rule"></span><h2>فیلم‌ها و سریال‌ها</h2></div><span class="section-note">${titleResults.length} عنوان</span></div><div class="title-grid">${titleResults.map(titleCard).join('')}</div></section>` : ''}
        ${episodeResults.length ? `<section class="shelf"><div class="section-heading"><div><span class="section-rule"></span><h2>قسمت‌های پیدا‌شده</h2></div><span class="section-note">${episodeResults.length} مورد</span></div><div class="search-episode-grid">${episodeResults.map((item) => `<a class="search-episode-card" href="#/watch/${item.id}"><span class="search-episode-art">${item.poster ? `<img src="${esc(item.poster)}" alt="" loading="lazy">` : '<span class="brand-mark"></span>'}<i>▶</i></span><span class="search-episode-copy"><small>${esc(item.title_name)}</small><strong>${esc(item.name || `قسمت ${item.number}`)}</strong><span>${esc(searchEpisodeMeta(item))}</span></span></a>`).join('')}</div></section>` : ''}
        ${!titleResults.length && !episodeResults.length ? `<section class="empty-search"><span class="search-empty-symbol">⌕</span><h2>نتیجه‌ای پیدا نشد</h2><p>عنوان یا نام قسمت دیگری را امتحان کن.</p><a class="button button-quiet" href="#/">بازگشت به خانه</a></section>` : ''}</div>`;
      app.innerHTML = shell(content);
      const input = document.querySelector('#global-search');
      if (input) input.value = query;
      bindSearch();
    } catch (error) {
      app.innerHTML = shell(`<section class="state-panel"><span class="state-symbol">!</span><h1>جست‌وجو در دسترس نیست</h1><p>${esc(error.message)}</p><a class="button button-quiet" href="#/">بازگشت به خانه</a></section>`);
      bindSearch();
    }
  }

  async function renderTitle(id) {
    try {
      const data = await api(`/api/title/${id}`);
      const title = data.title;
      const groups = data.seasons || [];
      const selectedGroup = groups[0]?.id;
      const groupsBySection = new Map();
      groups.forEach((group) => {
        const key = String(group.section || 1);
        if (!groupsBySection.has(key)) groupsBySection.set(key, []);
        groupsBySection.get(key).push(group);
      });
      const sectionEntries = [...groupsBySection.entries()];
      const defaultSection = String(groups[0]?.section || 1);
      const groupSummary = sectionEntries.length > 1
        ? groups.length > sectionEntries.length
          ? `${groups.length} فصل یا موضوع در ${sectionEntries.length} بخش`
          : `${sectionEntries.length} بخش`
        : `${groups.length} فصل`;
      const groupMarkup = groups.length > 1 ? `<div class="course-switcher">
        ${sectionEntries.length > 1 ? `<div class="course-switcher-field"><label for="content-section-select">بخش</label><select id="content-section-select" aria-label="انتخاب بخش">${sectionEntries.map(([section, items]) => `<option value="${section}" ${section === defaultSection ? 'selected' : ''}>${esc(items[0]?.section_name || `بخش ${section}`)}</option>`).join('')}</select></div>` : ''}
        <div class="course-switcher-field" id="season-switcher-field"><label for="content-season-select">فصل یا موضوع</label><select id="content-season-select" aria-label="انتخاب فصل">${(groupsBySection.get(defaultSection) || []).map((group) => `<option value="${group.id}" ${group.id === selectedGroup ? 'selected' : ''}>${esc(group.name || `فصل ${group.number}`)}</option>`).join('')}</select></div>
        </div>` : '';
      const episodesHtml = (seasonId) => data.episodes.filter((ep) => ep.season_id === seasonId).map((ep) => `
        <a class="episode-row" href="#/watch/${ep.id}"><span class="episode-thumb">${ep.poster ? `<img src="${esc(ep.poster)}" alt="" loading="lazy">` : '<span class="brand-mark"></span>'}<i>▶</i></span>
          <span class="episode-info"><strong>${title.kind === 'series' ? esc(ep.name || `قسمت ${ep.number}`) : `تماشای ${esc(title.name)}`}</strong><small>${title.kind === 'series' ? `${esc(ep.section_name || `بخش ${ep.section || 1}`)} · ${esc(ep.season_name || `فصل ${ep.season}`)} · قسمت ${ep.number} · ` : ''}${ep.duration ? `${Math.round(ep.duration / 60)} دقیقه` : 'آماده پخش'}</small></span><span class="episode-action">تماشا</span></a>`).join('');
      const activeEpisodes = selectedGroup ? episodesHtml(selectedGroup) : data.episodes.map((ep) => `
        <a class="episode-row" href="#/watch/${ep.id}"><span class="episode-thumb">${ep.poster ? `<img src="${esc(ep.poster)}" alt="" loading="lazy">` : '<span class="brand-mark"></span>'}<i>▶</i></span>
          <span class="episode-info"><strong>تماشای ${esc(title.name)}</strong><small>${ep.duration ? `${Math.round(ep.duration / 60)} دقیقه` : 'آماده پخش'}</small></span><span class="episode-action">تماشا</span></a>`).join('');
      const backTarget = `#/browse/${title.kind}`;
      const content = `
        <div class="detail-toolbar"><a class="back-link" href="${backTarget}">‹ بازگشت به ${title.kind === 'series' ? 'سریال‌ها' : 'فیلم‌ها'}</a><span>${title.kind === 'series' ? 'کتابخانهٔ سریال‌ها' : 'کتابخانهٔ فیلم‌ها'}</span></div>
        <section class="detail-hero" style="--hero-image:url('${esc(title.poster || '')}')"><div class="detail-backdrop"></div><div class="detail-copy">
          <span class="hero-kicker"><i></i>${title.kind === 'series' ? 'مجموعه سریالی' : 'فیلم'}</span>
          <h1>${esc(title.name)}</h1><div class="hero-meta"><span>${title.kind === 'series' ? 'سریال' : 'فیلم'}</span>${yearText(title.year)}<span>${title.pending_count ? `${title.ready_count} از ${title.episode_count} قسمت آماده` : `${title.episode_count} قسمت آماده`}</span></div>
          <p>${esc(title.synopsis || (title.ready_count ? 'برای تماشا آماده است.' : 'محتوای این عنوان در حال آماده‌سازی برای پخش است.'))}</p>${data.episodes[0] ? `<a class="button button-primary" href="#/watch/${data.episodes[0].id}"><span class="button-play">▶</span> ${title.kind === 'series' ? 'شروع تماشا' : 'تماشای فیلم'}</a>` : title.pending_count ? '<span class="button button-quiet">در حال آماده‌سازی پخش</span>' : ''}
        </div><div class="hero-corner" aria-hidden="true"></div></section>
        <section class="episode-section"><div class="section-heading"><div><span class="section-rule"></span><h2>${title.kind === 'series' ? 'قسمت‌ها' : 'پخش'}</h2></div><span class="section-note">${title.pending_count ? `${title.ready_count} از ${title.episode_count} قسمت آماده` : title.kind === 'series' ? groupSummary : 'کیفیت سورس'}</span></div>
          ${groupMarkup}<div id="episodes-list" class="episode-list">${activeEpisodes || `<div class="empty-inline">${title.pending_count ? 'قسمت‌های این بخش در صف آماده‌سازی پخش هستند.' : 'هنوز قسمتی آماده پخش نیست.'}</div>`}</div>
        </section>`;
      app.innerHTML = shell(`<div class="detail-wrap">${content}</div>`);
      bindSearch();
      const seasonSelect = document.querySelector('#content-season-select');
      const seasonField = document.querySelector('#season-switcher-field');
      const renderGroup = (seasonId) => {
        const list = document.querySelector('#episodes-list');
        list.innerHTML = episodesHtml(Number(seasonId)) || `<div class="empty-inline">قسمت‌های این بخش در صف آماده‌سازی پخش هستند.</div>`;
      };
      const setSection = (sectionKey) => {
        const items = groupsBySection.get(String(sectionKey)) || [];
        if (seasonSelect) {
          seasonSelect.innerHTML = items.map((group) => `<option value="${group.id}">${esc(group.name || `فصل ${group.number}`)}</option>`).join('');
          seasonSelect.value = String(items[0]?.id ?? '');
          seasonField.hidden = items.length <= 1;
        }
        if (items[0]) renderGroup(items[0].id);
      };
      document.querySelector('#content-section-select')?.addEventListener('change', (event) => setSection(event.currentTarget.value));
      seasonSelect?.addEventListener('change', (event) => renderGroup(event.currentTarget.value));
      if (seasonField && (groupsBySection.get(defaultSection) || []).length <= 1) seasonField.hidden = true;
    } catch (error) { renderError(error.message); }
  }

  function renderError(message) {
    app.innerHTML = shell(`<section class="state-panel"><span class="state-symbol">!</span><h1>این صفحه باز نشد</h1><p>${esc(message)}</p><a class="button button-quiet" href="#/">بازگشت به کتابخانه</a></section>`);
  }

  async function renderWatch(id) {
    try {
      const [playData, details] = await Promise.all([api(`/api/play/${id}`), api(`/api/episode/${id}`)]);
      const episode = details.episode;
      const owner = details.title;
      const next = details.next;
      const progressKey = `loyt.progress.${owner.id}`;
      const episodeProgressKey = `${progressKey}.${Number(id)}`;
      app.innerHTML = shell(`<section class="watch-page"><div class="watch-top"><a class="back-link" href="#/title/${owner.id}">‹ بازگشت به ${esc(owner.name)}</a><div><span class="watch-title">${esc(owner.name)}</span>${owner.kind === 'series' ? `<span class="watch-episode">${episode.section_name ? `${esc(episode.section_name)} · ` : ''}${esc(episode.name || `قسمت ${episode.number}`)}</span>` : ''}</div></div>
        <div class="player-frame" id="player-frame" tabindex="0" aria-label="پخش‌کننده ویدیو">
          <video id="video-player" playsinline preload="metadata" aria-label="پخش ${esc(owner.name)}"></video>
          <div class="player-action-feedback" id="player-action-feedback" aria-hidden="true"></div>
          <div class="player-gesture-feedback" id="player-gesture-feedback" aria-hidden="true"></div>
          <output id="timeline-tooltip" class="timeline-tooltip player-frame-preview" hidden><img id="timeline-preview-image" alt="" /><span id="timeline-preview-time">0:00</span></output>
          <div class="player-loader"><span class="spinner"></span><span>در حال آماده‌کردن پخش…</span></div>
          <div class="player-error" hidden></div>
          ${next ? `<aside class="next-up-card" id="next-up-card" hidden aria-label="قسمت بعد">
            <div class="next-up-head"><span>قسمت بعد</span><button type="button" id="dismiss-next-up" aria-label="بستن پیشنهاد قسمت بعد">×</button></div>
            <div class="next-up-body">${next.poster ? `<img src="${esc(next.poster)}" alt="">` : '<span class="next-up-placeholder"><i class="brand-mark"></i></span>'}<div class="next-up-copy"><strong>${esc(next.name || `قسمت ${next.number}`)}</strong><small>${next.section_name ? `${esc(next.section_name)} · ` : ''}${next.season_name ? esc(next.season_name) : `فصل ${next.season}`} · قسمت ${next.number}</small><button type="button" id="play-next">▶ پخش قسمت بعد</button></div></div>
          </aside>` : ''}
          <div class="player-controls" id="player-controls" aria-label="کنترل‌های پخش">
            <div class="player-timeline-wrap" dir="ltr"><div id="player-timeline" class="player-timeline" role="slider" tabindex="0" aria-label="موقعیت پخش" aria-valuemin="0" aria-valuemax="0" aria-valuenow="0" aria-valuetext="0:00"><div class="timeline-track"><span id="timeline-buffer" class="timeline-buffer"></span><span id="timeline-progress" class="timeline-progress"></span><span class="timeline-thumb"></span></div></div></div>
            <div class="player-control-row" dir="ltr">
              <div class="player-control-group player-main-controls">
                <button class="player-icon-button" id="player-play" type="button" aria-label="پخش"><svg id="player-play-icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M8 5.5v13l11-6.5z" fill="currentColor"/></svg></button>
                <button class="player-icon-button" id="player-next" type="button" aria-label="قسمت بعد" ${next ? '' : 'disabled'}><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 6v12l9-6zm10 0v12h3V6z" fill="currentColor"/></svg></button>
                <button class="player-icon-button" id="player-mute" type="button" aria-label="بی‌صدا"><svg class="icon-volume" viewBox="0 0 24 24" aria-hidden="true"><path d="M4 9v6h4l5 4V5L8 9zm12.5 3a4.5 4.5 0 0 0-2.5-4.03v8.05A4.5 4.5 0 0 0 16.5 12zm-2.5-8v2.06a6.5 6.5 0 0 1 0 11.88V20a8.5 8.5 0 0 0 0-16z" fill="currentColor"/></svg><svg class="icon-muted" viewBox="0 0 24 24" aria-hidden="true" hidden><path d="M4 9v6h4l5 4V5L8 9zm12.6 3 2.7-2.7-1.4-1.4-2.7 2.7-2.7-2.7-1.4 1.4 2.7 2.7-2.7 2.7 1.4 1.4 2.7-2.7 2.7 2.7 1.4-1.4z" fill="currentColor"/></svg></button>
                <input id="player-volume" class="player-volume" type="range" min="0" max="1" step="0.01" value="1" aria-label="بلندی صدا">
                <span class="player-time"><span id="player-current-time">0:00</span><i>/</i><span id="player-duration">0:00</span></span>
              </div>
              <div class="player-control-group player-extra-controls">
                <select id="player-speed" class="player-select" aria-label="سرعت پخش"><option value="0.5">۰٫۵×</option><option value="0.75">۰٫۷۵×</option><option value="1" selected>۱×</option><option value="1.25">۱٫۲۵×</option><option value="1.5">۱٫۵×</option><option value="2">۲×</option></select>
                <select id="player-quality" class="player-select player-quality" aria-label="کیفیت پخش" disabled><option value="-1">کیفیت اصلی</option></select>
                <button class="player-icon-button player-pip" id="player-pip" type="button" aria-label="پنجره شناور"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 5h16v14H4zm2 2v10h12V7zm6 5h5v3h-5z" fill="currentColor"/></svg></button>
                <button class="player-icon-button" id="player-fullscreen" type="button" aria-label="تمام‌صفحه"><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 4h6v2H6v4H4zm10 0h6v6h-2V6h-4zM4 14h2v4h4v2H4zm14 0h2v6h-6v-2h4z" fill="currentColor"/></svg></button>
              </div>
            </div>
          </div>
        </div>
        <div class="watch-after"><div><span class="source-badge"><i></i> کیفیت اصلی سورس</span><p>پخش مستقیم، بدون فشرده‌سازی دوباره</p></div><span class="watch-hint">میان‌برها: فاصله برای پخش · ← و → جابه‌جایی · F تمام‌صفحه</span></div></section>`);
      const video = document.querySelector('#video-player');
      const frame = document.querySelector('#player-frame');
      if (document.body.classList.contains('player-pseudo-fullscreen')) frame.classList.add('is-pseudo-fullscreen');
      const playButton = document.querySelector('#player-play');
      const loader = document.querySelector('.player-loader');
      const errorBox = document.querySelector('.player-error');
      const seek = document.querySelector('#player-timeline');
      const timelineProgress = document.querySelector('#timeline-progress');
      const timelineBuffer = document.querySelector('#timeline-buffer');
      const timelineTooltip = document.querySelector('#timeline-tooltip');
      const timelinePreviewImage = document.querySelector('#timeline-preview-image');
      const timelinePreviewTime = document.querySelector('#timeline-preview-time');
      const actionFeedback = document.querySelector('#player-action-feedback');
      const gestureFeedback = document.querySelector('#player-gesture-feedback');
      const volume = document.querySelector('#player-volume');
      const fullscreenButton = document.querySelector('#player-fullscreen');
      const currentTime = document.querySelector('#player-current-time');
      const durationTime = document.querySelector('#player-duration');
      const nextOverlay = document.querySelector('#next-up-card');
      const sourceUrl = new URL(playData.url, location.origin).href;
      const playToken = new URL(sourceUrl).searchParams.get('token') || '';
      const saved = progressForEpisode(owner.id, Number(id));
      let lastSavedAt = 0;
      let lastVolume = 1;
      let draggingSeek = false;
      let seekWasPlaying = false;
      let dismissedNext = false;
      let playbackEnded = false;
      let controlsTimer = 0;
      let actionTimer = 0;
      let gestureTimer = 0;
      let previewTimer = 0;
      let previewController = null;
      let previewObjectUrl = '';
      let previewSequence = 0;
      let previewBucket = -1;
      let lastPreviewRequestAt = 0;
      let touchGesture = null;
      let pendingTap = null;
      const touchTapDelay = 270;
      const formatTime = (seconds) => {
        if (!Number.isFinite(seconds) || seconds < 0) return '0:00';
        const value = Math.floor(seconds);
        const hours = Math.floor(value / 3600);
        const minutes = Math.floor((value % 3600) / 60);
        const rest = String(value % 60).padStart(2, '0');
        return hours ? `${hours}:${String(minutes).padStart(2, '0')}:${rest}` : `${minutes}:${rest}`;
      };
      const showActionFeedback = (state) => {
        actionFeedback.innerHTML = state === 'play'
          ? '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 5.5v13l11-6.5z" fill="currentColor"/></svg>'
          : '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 5h4v14H7zm7 0h4v14h-4z" fill="currentColor"/></svg>';
        actionFeedback.classList.remove('is-visible');
        void actionFeedback.offsetWidth;
        actionFeedback.classList.add('is-visible');
        window.clearTimeout(actionTimer);
        actionTimer = window.setTimeout(() => actionFeedback.classList.remove('is-visible'), 720);
      };
      const showGestureFeedback = (message, side = 'center') => {
        const alreadyVisible = gestureFeedback.classList.contains('is-visible') && gestureFeedback.dataset.side === side;
        gestureFeedback.textContent = message;
        gestureFeedback.dataset.side = side;
        if (alreadyVisible) {
          window.clearTimeout(gestureTimer);
          gestureTimer = window.setTimeout(() => gestureFeedback.classList.remove('is-visible'), 760);
          return;
        }
        gestureFeedback.classList.remove('is-visible');
        void gestureFeedback.offsetWidth;
        gestureFeedback.classList.add('is-visible');
        window.clearTimeout(gestureTimer);
        gestureTimer = window.setTimeout(() => gestureFeedback.classList.remove('is-visible'), 760);
      };
      const updatePlayButton = () => {
        const paused = video.paused;
        playButton.setAttribute('aria-label', paused ? 'پخش' : 'مکث');
        playButton.title = paused ? 'پخش (فاصله)' : 'مکث (فاصله)';
        playButton.querySelector('#player-play-icon').innerHTML = paused
          ? '<path d="M8 5.5v13l11-6.5z" fill="currentColor"/>'
          : '<path d="M7 5h4v14H7zm7 0h4v14h-4z" fill="currentColor"/>';
        frame.classList.toggle('is-playing', !paused);
      };
      const updateVolumeButton = () => {
        const muted = video.muted || video.volume === 0;
        document.querySelector('#player-mute').setAttribute('aria-label', muted ? 'فعال‌کردن صدا' : 'بی‌صدا');
        document.querySelector('.icon-volume').hidden = muted;
        document.querySelector('.icon-muted').hidden = !muted;
        if (!draggingSeek) volume.value = muted ? '0' : String(video.volume);
      };
      const updateTimeline = () => {
        const duration = video.duration;
        currentTime.textContent = formatTime(video.currentTime);
        durationTime.textContent = formatTime(duration);
        if (Number.isFinite(duration) && duration > 0) {
          const progress = video.currentTime / duration;
          timelineProgress.style.width = `${progress * 100}%`;
          seek.style.setProperty('--played', `${progress * 100}%`);
          seek.setAttribute('aria-valuemax', String(Math.round(duration)));
          seek.setAttribute('aria-valuenow', String(Math.round(video.currentTime)));
          seek.setAttribute('aria-valuetext', `${formatTime(video.currentTime)} از ${formatTime(duration)}`);
        }
        if (nextOverlay && Number.isFinite(duration) && duration > 0) {
          const nearEnd = duration - video.currentTime <= 24;
          nextOverlay.hidden = dismissedNext || !nearEnd;
          if (duration - video.currentTime > 45) dismissedNext = false;
        }
      };
      const showControls = () => {
        frame.classList.remove('controls-hidden');
        window.clearTimeout(controlsTimer);
        if (!video.paused) controlsTimer = window.setTimeout(() => frame.classList.add('controls-hidden'), 2800);
      };
      const togglePlayback = () => video.paused ? video.play().catch(() => {}) : video.pause();
      const syncFullscreenButton = () => {
        const isFullscreen = document.fullscreenElement === frame || frame.matches(':fullscreen') || frame.classList.contains('is-pseudo-fullscreen');
        fullscreenButton.setAttribute('aria-label', isFullscreen ? 'خروج از تمام‌صفحه' : 'تمام‌صفحه');
        fullscreenButton.title = isFullscreen ? 'خروج از تمام‌صفحه' : 'تمام‌صفحه';
        fullscreenButton.querySelector('svg').innerHTML = isFullscreen
          ? '<path d="M9 4v2H6v3H4V4zm11 0v5h-2V6h-3V4zM4 15h2v3h3v2H4zm14 0h2v5h-5v-2h3z" fill="currentColor"/>'
          : '<path d="M4 4h6v2H6v4H4zm10 0h6v6h-2V6h-4zM4 14h2v4h4v2H4zm14 0h2v6h-6v-2h4z" fill="currentColor"/>';
      };
      const onFullscreenChange = () => { syncFullscreenButton(); showControls(); };
      syncFullscreenButton();
      const goNext = () => { if (next) location.hash = `/watch/${next.id}`; };
      const showError = (heading, message) => {
        loader.hidden = true;
        errorBox.hidden = false;
        errorBox.innerHTML = `<span class="state-symbol">!</span><strong>${esc(heading)}</strong><p>${esc(message)}</p>`;
      };
      const save = () => {
        if (!playbackEnded && video.duration && Number.isFinite(video.duration)) {
          const percent = Math.round((video.currentTime / video.duration) * 100);
          const updatedAt = Date.now();
          localStorage.setItem(episodeProgressKey, JSON.stringify({ percent, updatedAt }));
          localStorage.setItem(progressKey, JSON.stringify({ episodeId: Number(id), percent, updatedAt }));
          lastSavedAt = Date.now();
        }
      };
      const ratioAt = (clientX) => {
        const bounds = seek.getBoundingClientRect();
        return Math.max(0, Math.min(1, (clientX - bounds.left) / Math.max(1, bounds.width)));
      };
      const beginScrubbing = () => {
        if (draggingSeek) return;
        seekWasPlaying = !video.paused;
        draggingSeek = true;
        seek.classList.add('is-scrubbing');
        frame.classList.add('is-seeking');
        timelineTooltip.hidden = false;
        previewBucket = -1;
        window.clearTimeout(previewTimer);
        previewTimer = 0;
        previewSequence += 1;
        previewController?.abort();
        if (previewObjectUrl) URL.revokeObjectURL(previewObjectUrl);
        previewObjectUrl = '';
        timelinePreviewImage.removeAttribute('src');
        window.clearTimeout(actionTimer);
        actionFeedback.classList.remove('is-visible');
        window.clearTimeout(gestureTimer);
        gestureFeedback.classList.remove('is-visible', 'is-scrubbing');
        if (seekWasPlaying) video.pause();
      };
      const showTimelinePreview = (clientX) => {
        if (!Number.isFinite(video.duration) || video.duration <= 0) return 0;
        const ratio = ratioAt(clientX);
        const time = video.duration * ratio;
        timelinePreviewTime.textContent = formatTime(time);
        if (!draggingSeek) return ratio;
        seek.style.setProperty('--preview', `${ratio * 100}%`);
        seek.setAttribute('aria-valuenow', String(Math.round(time)));
        seek.setAttribute('aria-valuetext', `${formatTime(time)} از ${formatTime(video.duration)}`);
        const bucket = Math.floor(time / 3);
        if (bucket === previewBucket) return ratio;
        previewBucket = bucket;
        previewSequence += 1;
        previewController?.abort();
        if (!previewTimer) {
          const delay = Math.max(0, 120 - (Date.now() - lastPreviewRequestAt));
          previewTimer = window.setTimeout(async () => {
            previewTimer = 0;
            if (!playToken || !draggingSeek) return;
            const requestSequence = previewSequence;
            const previewAt = previewBucket * 3 + 1;
            previewController = new AbortController();
            lastPreviewRequestAt = Date.now();
            try {
              const previewUrl = `/api/preview/${encodeURIComponent(id)}?at=${encodeURIComponent(previewAt.toFixed(2))}&token=${encodeURIComponent(playToken)}`;
              const response = await fetch(previewUrl, { credentials: 'same-origin', signal: previewController.signal });
              if (!response.ok || requestSequence !== previewSequence) return;
              const blob = await response.blob();
              if (requestSequence !== previewSequence) return;
              const nextObjectUrl = URL.createObjectURL(blob);
              if (previewObjectUrl) URL.revokeObjectURL(previewObjectUrl);
              previewObjectUrl = nextObjectUrl;
              timelinePreviewImage.src = previewObjectUrl;
            } catch (error) {
              if (error.name !== 'AbortError') timelinePreviewImage.removeAttribute('src');
            }
          }, delay);
        }
        return ratio;
      };
      const finishScrubbing = (commit, ratio = 0) => {
        if (!draggingSeek) return;
        if (commit) seekTo(ratio);
        const resumePlayback = seekWasPlaying;
        draggingSeek = false;
        seekWasPlaying = false;
        seek.classList.remove('is-scrubbing');
        seek.style.removeProperty('--preview');
        frame.classList.remove('is-seeking');
        timelineTooltip.hidden = true;
        window.clearTimeout(previewTimer);
        previewTimer = 0;
        previewSequence += 1;
        previewBucket = -1;
        previewController?.abort();
        updateTimeline();
        if (resumePlayback) video.play().catch(() => {});
      };
      const seekTo = (ratio) => {
        if (!Number.isFinite(video.duration) || video.duration <= 0) return;
        const bounded = Math.max(0, Math.min(1, ratio));
        video.currentTime = video.duration * bounded;
        timelineProgress.style.width = `${bounded * 100}%`;
        seek.style.setProperty('--played', `${bounded * 100}%`);
        currentTime.textContent = formatTime(video.currentTime);
        seek.setAttribute('aria-valuenow', String(Math.round(video.currentTime)));
        seek.setAttribute('aria-valuetext', `${formatTime(video.currentTime)} از ${formatTime(video.duration)}`);
      };
      const updateBuffered = () => {
        if (!Number.isFinite(video.duration) || video.duration <= 0 || !video.buffered.length) {
          timelineBuffer.style.width = '0%';
          return;
        }
        let end = 0;
        for (let index = 0; index < video.buffered.length; index += 1) {
          end = Math.max(end, video.buffered.end(index));
        }
        timelineBuffer.style.width = `${Math.max(0, Math.min(100, (end / video.duration) * 100))}%`;
      };
      video.addEventListener('timeupdate', () => {
        updateTimeline();
        updateBuffered();
        if (Date.now() - lastSavedAt > 7000) save();
      });
      video.addEventListener('progress', updateBuffered);
      video.addEventListener('durationchange', updateTimeline);
      video.addEventListener('loadedmetadata', () => {
        loader.hidden = true;
        updateTimeline();
        updateBuffered();
        frame.classList.toggle('portrait-video', video.videoHeight > video.videoWidth);
        if (video.videoWidth && video.videoHeight) frame.style.setProperty('--video-ratio', `${video.videoWidth} / ${video.videoHeight}`);
        if (saved > 5 && saved < 94) video.currentTime = video.duration * saved / 100;
      });
      video.addEventListener('play', () => { updatePlayButton(); showActionFeedback('play'); });
      video.addEventListener('pause', () => { updatePlayButton(); if (!draggingSeek) showActionFeedback('pause'); save(); showControls(); });
      video.addEventListener('playing', () => { loader.hidden = true; updatePlayButton(); showControls(); });
      video.addEventListener('waiting', () => { if (!video.paused) loader.hidden = false; });
      video.addEventListener('volumechange', updateVolumeButton);
      video.addEventListener('ended', () => {
        playbackEnded = true;
        localStorage.removeItem(episodeProgressKey);
        try {
          const lastProgress = JSON.parse(localStorage.getItem(progressKey) || 'null');
          if (Number(lastProgress?.episodeId) === Number(id)) localStorage.removeItem(progressKey);
        } catch { /* Keep the last-title pointer if its value is unreadable. */ }
        if (nextOverlay && !dismissedNext) nextOverlay.hidden = false;
        showControls();
      });
      video.addEventListener('error', () => {
        showError('مرورگر این قالب یا کدک را پخش نمی‌کند.', episode.name || owner.name);
      });
      playButton.addEventListener('click', togglePlayback);
      document.querySelector('#player-next').addEventListener('click', goNext);
      document.querySelector('#player-mute').addEventListener('click', () => {
        if (video.muted || video.volume === 0) { video.muted = false; video.volume = lastVolume || 1; }
        else { lastVolume = video.volume; video.muted = true; }
      });
      volume.addEventListener('input', () => { video.volume = Number(volume.value); video.muted = video.volume === 0; if (video.volume > 0) lastVolume = video.volume; });
      seek.addEventListener('pointerdown', (event) => {
        if (event.button !== 0) return;
        beginScrubbing();
        seek.setPointerCapture(event.pointerId);
        showTimelinePreview(event.clientX);
        showControls();
        event.preventDefault();
      });
      seek.addEventListener('pointermove', (event) => {
        showTimelinePreview(event.clientX);
        showControls();
      });
      const finishSeek = (event) => {
        if (draggingSeek) {
          finishScrubbing(event.type === 'pointerup', ratioAt(event.clientX));
          if (seek.hasPointerCapture(event.pointerId)) seek.releasePointerCapture(event.pointerId);
        }
      };
      seek.addEventListener('pointerup', finishSeek);
      seek.addEventListener('pointercancel', finishSeek);
      seek.addEventListener('pointerleave', () => { if (!draggingSeek) timelineTooltip.hidden = true; });
      seek.addEventListener('pointerleave', () => {
        if (draggingSeek) return;
        window.clearTimeout(previewTimer);
        previewSequence += 1;
        previewController?.abort();
      });
      seek.addEventListener('keydown', (event) => {
        if (!Number.isFinite(video.duration) || video.duration <= 0) return;
        const step = event.shiftKey ? 30 : 5;
        let time = video.currentTime;
        if (event.key === 'ArrowLeft' || event.key === 'ArrowDown') time -= step;
        else if (event.key === 'ArrowRight' || event.key === 'ArrowUp') time += step;
        else if (event.key === 'Home') time = 0;
        else if (event.key === 'End') time = video.duration;
        else return;
        event.preventDefault();
        event.stopPropagation();
        seekTo(time / video.duration);
      });
      document.querySelector('#player-speed').addEventListener('change', (event) => { video.playbackRate = Number(event.currentTarget.value); });
      document.querySelector('#player-pip').addEventListener('click', async () => {
        try {
          if (document.pictureInPictureElement) await document.exitPictureInPicture();
          else if (document.pictureInPictureEnabled && video.requestPictureInPicture) await video.requestPictureInPicture();
          else toast('پنجره شناور در این مرورگر پشتیبانی نمی‌شود.', 'error');
        } catch { toast('پنجره شناور در دسترس نیست.', 'error'); }
      });
      fullscreenButton.addEventListener('click', async () => {
        try {
          if (document.fullscreenElement === frame || frame.matches(':fullscreen')) await document.exitFullscreen();
          else if (frame.classList.contains('is-pseudo-fullscreen')) {
            frame.classList.remove('is-pseudo-fullscreen');
            document.body.classList.remove('player-pseudo-fullscreen');
            syncFullscreenButton();
          }
          else if (frame.requestFullscreen) {
            try { await frame.requestFullscreen(); }
            catch {
              frame.classList.add('is-pseudo-fullscreen');
              document.body.classList.add('player-pseudo-fullscreen');
              syncFullscreenButton();
            }
          }
          else {
            frame.classList.add('is-pseudo-fullscreen');
            document.body.classList.add('player-pseudo-fullscreen');
            syncFullscreenButton();
          }
        } catch { toast('تمام‌صفحه در دسترس نیست.', 'error'); }
      });
      document.addEventListener('fullscreenchange', onFullscreenChange);
      const runPendingTap = () => {
        if (!pendingTap) return;
        window.clearTimeout(pendingTap.timer);
        if (pendingTap.zone === 'center') togglePlayback();
        pendingTap = null;
      };
      const handleTouchTap = (zone) => {
        const now = Date.now();
        if (pendingTap && pendingTap.zone === zone && now - pendingTap.at <= touchTapDelay) {
          window.clearTimeout(pendingTap.timer);
          pendingTap = null;
          if (zone === 'center') {
            togglePlayback();
          } else if (Number.isFinite(video.duration) && video.duration > 0) {
            const delta = zone === 'left' ? -10 : 10;
            video.currentTime = Math.max(0, Math.min(video.duration, video.currentTime + delta));
            showGestureFeedback(`${delta < 0 ? '−' : '+'}۱۰ ثانیه`, zone);
          }
          showControls();
          return;
        }
        runPendingTap();
        pendingTap = { zone, at: now, timer: window.setTimeout(() => {
          if (pendingTap?.zone === zone && pendingTap.at === now) runPendingTap();
        }, touchTapDelay) };
        showControls();
      };
      video.addEventListener('pointerdown', (event) => {
        if (event.pointerType !== 'touch') return;
        const bounds = video.getBoundingClientRect();
        const ratio = Math.max(0, Math.min(1, (event.clientX - bounds.left) / Math.max(1, bounds.width)));
        const zone = ratio < 0.34 ? 'left' : ratio > 0.66 ? 'right' : 'center';
        touchGesture = {
          pointerId: event.pointerId, zone, startX: event.clientX, startY: event.clientY,
          startTime: video.currentTime, seekTime: video.currentTime,
          startVolume: video.muted ? 0 : video.volume, bounds, moved: false, volume: false,
        };
        try { video.setPointerCapture(event.pointerId); } catch { /* Pointer may already be captured by the browser. */ }
      });
      video.addEventListener('pointermove', (event) => {
        if (!touchGesture || event.pointerId !== touchGesture.pointerId) return;
        const deltaX = event.clientX - touchGesture.startX;
        const deltaY = event.clientY - touchGesture.startY;
        if (!touchGesture.seeking && touchGesture.zone === 'right' && Math.abs(deltaY) > 12 && Math.abs(deltaY) > Math.abs(deltaX) * 1.2) {
          touchGesture.volume = true;
          touchGesture.moved = true;
          video.muted = false;
          video.volume = Math.max(0, Math.min(1, touchGesture.startVolume - deltaY / Math.max(150, touchGesture.bounds.height * 0.55)));
          showGestureFeedback(`صدا ${Math.round(video.volume * 100)}٪`, 'right');
          showControls();
        } else if ((touchGesture.seeking || (!touchGesture.volume && Math.abs(deltaX) > 12 && Math.abs(deltaX) > Math.abs(deltaY) * 1.2)) && Number.isFinite(video.duration) && video.duration > 0) {
          if (!touchGesture.seeking) {
            touchGesture.seeking = true;
            touchGesture.moved = true;
            beginScrubbing();
            if (pendingTap) {
              window.clearTimeout(pendingTap.timer);
              pendingTap = null;
            }
          }
          const targetTime = Math.max(0, Math.min(video.duration, touchGesture.startTime + (deltaX / Math.max(1, touchGesture.bounds.width)) * video.duration));
          touchGesture.seekTime = targetTime;
          const timelineBounds = seek.getBoundingClientRect();
          showTimelinePreview(timelineBounds.left + (targetTime / video.duration) * timelineBounds.width);
          showControls();
        } else if (Math.abs(deltaX) > 15 || Math.abs(deltaY) > 15) {
          touchGesture.moved = true;
        }
      });
      const finishTouchGesture = (event) => {
        if (!touchGesture || event.pointerId !== touchGesture.pointerId) return;
        const gesture = touchGesture;
        touchGesture = null;
        if (gesture.seeking) {
          const canCommit = event.type === 'pointerup' && Number.isFinite(video.duration) && video.duration > 0;
          finishScrubbing(canCommit, canCommit ? gesture.seekTime / video.duration : 0);
        }
        if (event.type === 'pointerup' && !gesture.moved) handleTouchTap(gesture.zone);
        if (video.hasPointerCapture(event.pointerId)) video.releasePointerCapture(event.pointerId);
      };
      video.addEventListener('pointerup', finishTouchGesture);
      video.addEventListener('pointercancel', finishTouchGesture);
      let lastTouchPointerAt = 0;
      video.addEventListener('pointerdown', (event) => { if (event.pointerType === 'touch') lastTouchPointerAt = Date.now(); }, { passive: true });
      video.addEventListener('click', () => {
        if (Date.now() - lastTouchPointerAt > 700) togglePlayback();
      });
      video.addEventListener('dblclick', (event) => {
        event.preventDefault();
        event.stopImmediatePropagation();
      }, true);
      frame.addEventListener('pointermove', showControls);
      frame.addEventListener('pointerenter', showControls);
      frame.addEventListener('pointerleave', () => { if (!video.paused) controlsTimer = window.setTimeout(() => frame.classList.add('controls-hidden'), 900); });
      document.querySelector('#play-next')?.addEventListener('click', goNext);
      document.querySelector('#dismiss-next-up')?.addEventListener('click', () => { dismissedNext = true; nextOverlay.hidden = true; });
      playerKeyHandler = (event) => {
        if (!route().startsWith('/watch/') || event.altKey || event.ctrlKey || event.metaKey) return;
        if (event.target instanceof HTMLElement && event.target.closest('button, input, select, textarea, [role="slider"], [contenteditable="true"]')) return;
        switch (event.code) {
          case 'Space': event.preventDefault(); togglePlayback(); break;
          case 'ArrowLeft': event.preventDefault(); video.currentTime = Math.max(0, video.currentTime - 5); break;
          case 'ArrowRight': event.preventDefault(); video.currentTime = Math.min(video.duration || Infinity, video.currentTime + 5); break;
          case 'ArrowUp': event.preventDefault(); video.volume = Math.min(1, video.volume + 0.05); video.muted = false; break;
          case 'ArrowDown': event.preventDefault(); video.volume = Math.max(0, video.volume - 0.05); break;
          case 'KeyM': video.muted = !video.muted; break;
          case 'KeyF': document.querySelector('#player-fullscreen').click(); break;
          case 'KeyN': goNext(); break;
        }
      };
      window.addEventListener('keydown', playerKeyHandler);
      window.addEventListener('beforeunload', save, { once: true });
      playerCleanup = () => {
        save();
        window.removeEventListener('beforeunload', save);
        document.removeEventListener('fullscreenchange', onFullscreenChange);
        if (frame.classList.contains('is-pseudo-fullscreen') && !route().startsWith('/watch/')) {
          frame.classList.remove('is-pseudo-fullscreen');
          document.body.classList.remove('player-pseudo-fullscreen');
        }
        window.clearTimeout(actionTimer);
        window.clearTimeout(gestureTimer);
        window.clearTimeout(previewTimer);
        if (pendingTap) window.clearTimeout(pendingTap.timer);
        previewController?.abort();
        if (previewObjectUrl) URL.revokeObjectURL(previewObjectUrl);
        playerCleanup = null;
      };
      updatePlayButton();
      updateVolumeButton();
      if (playData.mode === 'file') {
        video.src = sourceUrl;
        video.load();
      } else if (window.Hls && Hls.isSupported()) {
        hlsInstance = new Hls({ enableWorker: true, capLevelToPlayerSize: true });
        hlsInstance.loadSource(sourceUrl);
        hlsInstance.attachMedia(video);
        hlsInstance.on(Hls.Events.MANIFEST_PARSED, (_, data) => {
          loader.hidden = true;
          const quality = document.querySelector('#player-quality');
          if (hlsInstance.levels.length > 1) {
            quality.disabled = false;
            quality.innerHTML = '<option value="-1">خودکار</option>' + hlsInstance.levels.map((level, index) => `<option value="${index}">${level.height ? `${level.height}p` : `${Math.round(level.bitrate / 1000)} kbps`}</option>`).join('');
            quality.addEventListener('change', () => { hlsInstance.currentLevel = Number(quality.value); });
          }
        });
        hlsInstance.on(Hls.Events.ERROR, (_, data) => {
          if (data.fatal) showError('پخش این سورس انجام نشد.', `${episode.name || owner.name} — ${data.details || 'کدک پشتیبانی نمی‌شود.'}`);
        });
      } else if (video.canPlayType('application/vnd.apple.mpegurl')) {
        video.src = sourceUrl;
      } else {
        showError('این مرورگر پخش HLS را پشتیبانی نمی‌کند.', 'یک مرورگر به‌روزتر را امتحان کن.');
      }
      bindSearch();
    } catch (error) { renderError(error.message); }
  }

  async function renderAdmin() {
    clearInterval(adminPoll);
    let auth = false;
    try { auth = (await api('/api/admin/session')).authenticated; }
    catch { auth = false; }
    if (!auth) { renderLogin(); return; }
    renderAdminShell(route().split('/')[2] || 'overview');
    await refreshAdmin();
    adminPoll = setInterval(() => { if (route().startsWith('/admin')) refreshAdmin(true); }, 6000);
  }

  function renderLogin() {
    app.innerHTML = `<main class="admin-login"><a class="brand" href="#/"><span class="brand-mark"><i></i></span><span>لویت</span></a><section class="login-panel"><span class="login-emblem"><i></i></span><h1>ورود مدیر</h1><p>با رمز این سرور وارد پنل مدیریت شو.</p><form id="login-form"><label>رمز مدیر<input type="password" name="password" required autocomplete="current-password" placeholder="رمز ورود"></label><button class="button button-primary" type="submit">ورود به پنل</button><div class="form-error" id="login-error" hidden></div></form><a class="back-link" href="#/">‹ بازگشت به کتابخانه</a></section></main>`;
    document.querySelector('#login-form').addEventListener('submit', async (event) => {
      event.preventDefault();
      const password = new FormData(event.currentTarget).get('password');
      try { await api('/api/admin/login', { method: 'POST', body: JSON.stringify({ password }) }); toast('وارد پنل شدی'); renderAdmin(); }
      catch (error) { const node = document.querySelector('#login-error'); node.hidden = false; node.textContent = error.message; }
    });
  }

  function renderAdminShell(section) {
    const nav = [['overview', 'نمای کلی', '◫'], ['content', 'محتوا', '▧'], ['add', 'افزودن محتوا', '＋'], ['jobs', 'کارهای رسانه', '◷']];
    const activeSection = section === 'edit' ? 'content' : section;
    const heading = section === 'edit' ? 'ویرایش محتوا' : nav.find((n) => n[0] === section)?.[1] || 'پنل مدیریت';
    app.innerHTML = `<div class="admin-layout"><aside class="admin-sidebar"><a class="brand" href="#/"><span class="brand-mark"><i></i></span><span>لویت</span></a><span class="admin-label">مدیریت کتابخانه</span><nav>${nav.map(([id, label, icon]) => `<a href="#/admin/${id}" class="${activeSection === id ? 'active' : ''}"><span>${icon}</span>${label}</a>`).join('')}</nav><div class="sidebar-bottom"><span class="footer-status"><i></i> شبکه داخلی</span><button class="sidebar-logout" id="logout">خروج از پنل</button><a class="sidebar-back" href="#/">‹ برگشت به سایت</a></div></aside><main class="admin-main"><header class="admin-header"><div><span class="admin-kicker">لویت / مدیریت</span><h1>${heading}</h1></div><span class="admin-avatar">م</span></header><div id="admin-content" class="admin-content"></div></main></div>`;
    document.querySelector('#logout').addEventListener('click', async () => { await api('/api/admin/logout', { method: 'POST' }); location.hash = '/'; toast('از پنل خارج شدی'); });
    if (section === 'overview') showOverview();
    if (section === 'add') showAdd();
    if (section === 'jobs') showJobs();
  }

  async function refreshAdmin(quiet = false) {
    if (!route().startsWith('/admin')) return;
    const section = route().split('/')[2] || 'overview';
    if (!quiet || !document.querySelector('#admin-content')) renderAdminShell(section);
    if (section === 'content' && quiet) return;
    if (section === 'edit') {
      if (!quiet) await showTitleEditor(route().split('/')[3]);
      return;
    }
    if (section === 'overview') await showOverview(true);
    if (section === 'content') await showContent(true);
    if (section === 'jobs') await showJobs(true);
  }

  async function showOverview(keep = false) {
    const target = document.querySelector('#admin-content'); if (!target) return;
    try {
      const [stats, jobs] = await Promise.all([api('/api/admin/overview'), api('/api/admin/jobs')]);
      target.innerHTML = `<div class="stats-grid"><article class="stat-tile"><span>عنوان‌ها</span><strong>${stats.titles}</strong><i>فیلم و سریال</i></article><article class="stat-tile"><span>قسمت‌ها</span><strong>${stats.episodes}</strong><i>سورس‌های ثبت‌شده</i></article><article class="stat-tile stat-accent"><span>در صف پردازش</span><strong>${stats.queued}</strong><i>استخراج تصویر و ساخت HLS</i></article><article class="stat-tile stat-danger"><span>نیازمند بررسی</span><strong>${stats.failed}</strong><i>کارهای ناموفق</i></article></div>
        <section class="admin-panel"><div class="panel-head"><div><h2>آخرین کارهای رسانه</h2><p>وضعیت ساخت فریم و قطعه‌های پخش</p></div><a class="section-link" href="#/admin/jobs">همه کارها ‹</a></div>${jobs.items.slice(0, 5).map(jobRow).join('') || '<div class="empty-inline">هنوز کاری ثبت نشده است.</div>'}</section>
        <section class="admin-callout"><span class="callout-mark">✳</span><div><strong>فایل‌های اصلی محفوظ می‌مانند</strong><p>لویت فقط از مسیر /media می‌خواند و فایل اصلی را تغییر نمی‌دهد.</p></div></section>`;
    } catch (error) { target.innerHTML = `<div class="form-error">${esc(error.message)}</div>`; }
  }

  function jobRow(job) {
    const stateLabel = { queued: 'در صف', running: 'در حال پردازش', done: 'آماده', error: 'خطا', superseded: 'نسخه قدیمی' }[job.state] || job.state;
    return `<div class="job-row"><span class="job-state ${esc(job.state)}"><i></i>${stateLabel}</span><div class="job-info"><strong>${esc(job.title_name)}${job.episode_name ? ` — ${esc(job.episode_name)}` : ''}</strong><small>${esc(job.source_path)}</small>${job.error ? `<em>${esc(job.error)}</em>` : ''}</div>${job.state === 'error' ? `<button class="retry-button" data-retry="${job.id}">تلاش دوباره</button>` : ''}</div>`;
  }

  function contentStatusLabel(item) {
    if (item.deleted_at) return 'در زباله‌دان';
    if (item.hidden) return 'مخفی';
    return item.ready_count === item.episode_count ? 'آماده' : 'در حال آماده‌سازی';
  }

  function contentActions(kind, item) {
    const id = item.id;
    if (item.deleted_at) return `<button type="button" class="state-button" data-content-action="restore" data-content-kind="${kind}" data-content-id="${id}">بازیابی</button><button type="button" class="danger-button" data-content-action="delete" data-content-kind="${kind}" data-content-id="${id}">حذف دائمی</button>`;
    return `${item.hidden ? `<button type="button" class="state-button" data-content-action="show" data-content-kind="${kind}" data-content-id="${id}">نمایش</button>` : `<button type="button" class="state-button" data-content-action="hide" data-content-kind="${kind}" data-content-id="${id}">مخفی‌کردن</button>`}<button type="button" class="danger-button" data-content-action="trash" data-content-kind="${kind}" data-content-id="${id}">زباله‌دان</button>`;
  }

  function bindContentActions(container, onComplete) {
    container.querySelectorAll('[data-content-action]').forEach((button) => button.addEventListener('click', async () => {
      const { contentAction: action, contentKind: kind, contentId: id } = button.dataset;
      const labels = { hide: 'محتوا مخفی شد.', show: 'محتوا دوباره نمایش داده می‌شود.', trash: 'محتوا به زباله‌دان منتقل شد.', restore: 'محتوا بازیابی شد.', delete: 'محتوا برای همیشه حذف شد.' };
      if (action === 'trash') {
        const subject = kind === 'title' ? 'عنوان و همهٔ قسمت‌های وابسته' : 'این قسمت';
        if (!window.confirm(`${subject} از دسترس کاربران خارج و به زباله‌دان منتقل شود؟ بعداً قابل بازیابی است.`)) return;
      }
      if (action === 'delete' && !window.confirm('حذف دائمی انجام شود؟ فایل اصلی ویدیو در پوشه رسانه باقی می‌ماند؛ فقط اطلاعات و فایل‌های ساخته‌شده لویت حذف می‌شوند.')) return;
      const url = `/api/admin/${kind}/${id}${action === 'delete' ? '' : `/${action}`}`;
      button.disabled = true;
      try {
        await api(url, { method: action === 'delete' ? 'DELETE' : 'POST' });
        toast(labels[action]);
        await onComplete(action, kind, Number(id));
      } catch (error) { button.disabled = false; toast(error.message, 'error'); }
    }));
  }

  async function showContent() {
    const target = document.querySelector('#admin-content'); if (!target) return;
    try {
      const params = new URLSearchParams({ status: adminContentStatus, q: adminContentQuery });
      const data = await api(`/api/admin/titles?${params}`);
      const emptyText = adminContentStatus === 'deleted' ? 'زباله‌دان خالی است.' : adminContentQuery ? 'عنوانی با این عبارت پیدا نشد.' : 'هنوز محتوایی وارد نکردی. از دکمه افزودن شروع کن.';
      target.innerHTML = `<section class="admin-panel"><div class="panel-head"><div><h2>کتابخانه محتوا</h2><p>عنوان‌ها و قسمت‌ها را مخفی کن، بازیابی کن یا به زباله‌دان بفرست.</p></div><a class="button button-primary button-small" href="#/admin/add">افزودن محتوا ＋</a></div><div class="content-filters"><label>وضعیت<select id="content-status"><option value="active" ${adminContentStatus === 'active' ? 'selected' : ''}>نمایش‌داده‌شده</option><option value="hidden" ${adminContentStatus === 'hidden' ? 'selected' : ''}>مخفی</option><option value="deleted" ${adminContentStatus === 'deleted' ? 'selected' : ''}>زباله‌دان</option><option value="all" ${adminContentStatus === 'all' ? 'selected' : ''}>همه</option></select></label><label>جست‌وجو<input id="content-search" type="search" value="${esc(adminContentQuery)}" placeholder="نام سریال یا فیلم"></label></div><div class="content-table"><div class="table-head"><span>عنوان</span><span>نوع</span><span>قسمت‌ها</span><span>وضعیت</span><span>عملیات</span></div>${data.items.map((item) => `<div class="table-row"><div class="table-title"><span class="table-poster">${item.poster_path ? `<img src="/api/poster/title/${item.id}" alt="">` : '<span class="brand-mark"></span>'}</span><strong>${esc(item.name)}</strong></div><span>${item.kind === 'series' ? 'سریال' : 'فیلم'}</span><span>${item.ready_count || 0} از ${item.episode_count}</span><span class="job-state ${item.deleted_at ? 'error' : item.hidden ? 'queued' : item.ready_count === item.episode_count ? 'done' : 'queued'}"><i></i>${contentStatusLabel(item)}</span><div class="content-actions"><a class="outline-button edit-content-link" href="#/admin/edit/${item.id}">ویرایش</a>${contentActions('title', item)}</div></div>`).join('') || `<div class="empty-inline">${emptyText}</div>`}</div></section>`;
      target.querySelector('#content-status').addEventListener('change', (event) => { adminContentStatus = event.currentTarget.value; showContent(); });
      target.querySelector('#content-search').addEventListener('input', (event) => {
        adminContentQuery = event.currentTarget.value.trim();
        clearTimeout(adminContentTimer);
        adminContentTimer = setTimeout(showContent, 220);
      });
      bindContentActions(target, (action) => {
        if (action === 'delete' && adminContentStatus !== 'deleted') adminContentStatus = 'deleted';
        return showContent();
      });
    } catch (error) { target.innerHTML = `<div class="form-error">${esc(error.message)}</div>`; }
  }

  async function showTitleEditor(id) {
    const target = document.querySelector('#admin-content'); if (!target) return;
    try {
      const data = await api(`/api/admin/title/${encodeURIComponent(id)}`);
      const title = data.title;
      const seasons = data.seasons || [];
      const episodes = data.episodes || [];
      const titleStateHint = title.deleted_at ? 'این عنوان در زباله‌دان است و برای کاربران نمایش داده نمی‌شود.' : title.hidden ? 'این عنوان مخفی است و برای کاربران نمایش داده نمی‌شود.' : '';
      const coverOptions = episodes.map((episode) => `<option value="${episode.id}" ${Number(title.poster_episode_id) === Number(episode.id) ? 'selected' : ''}>${esc(episode.name || `قسمت ${episode.number}`)} · ${episode.poster_path ? 'فریم آماده' : 'فریم در صف'}</option>`).join('');
      const statusText = { ready: 'آماده', queued: 'در صف آماده‌سازی', processing: 'در حال آماده‌سازی', error: 'دارای خطا' };
      target.innerHTML = `<div class="editor-breadcrumb"><a href="#/admin/content">‹ کتابخانه محتوا</a><span>${esc(title.name)}</span></div>
        <section class="admin-panel form-panel"><div class="panel-head"><div><h2>مشخصات عنوان</h2><p>نام نمایشی، نوع، توضیح، سال ساخت و فریم اصلی صفحه را تنظیم کن.</p>${titleStateHint ? `<p class="content-state-hint">${titleStateHint}</p>` : ''}</div><div class="content-actions editor-title-actions">${!title.hidden && !title.deleted_at ? `<a class="button button-quiet button-small" href="#/title/${title.id}">مشاهده در سایت ↗</a>` : ''}${contentActions('title', title)}</div></div>
          <form id="title-edit-form" class="form-grid"><label>نام محتوا<input name="name" required maxlength="200" value="${esc(title.name)}"></label>
          <label>نوع محتوا<select name="kind"><option value="series" ${title.kind === 'series' ? 'selected' : ''}>سریال یا دوره</option><option value="movie" ${title.kind === 'movie' ? 'selected' : ''}>فیلم یا تک‌قسمتی</option></select></label>
          <label>سال ساخت <span class="optional">اختیاری</span><input name="year" type="number" min="1880" max="2200" value="${title.year || ''}" placeholder="2024"></label>
          <label class="field-wide">توضیح کوتاه <span class="optional">اختیاری</span><textarea name="synopsis" rows="3" maxlength="3000">${esc(title.synopsis || '')}</textarea></label>
          <label class="field-wide">فریم بنر عنوان<select name="poster_episode_id"><option value="">خودکار · فریم اولین قسمت</option>${coverOptions}</select><small class="field-hint">فریم انتخابی از سورس همان قسمت ساخته می‌شود؛ تصویر جداگانه بارگذاری نمی‌شود.</small></label>
          <div class="form-actions field-wide"><button class="button button-primary" type="submit">ذخیره مشخصات</button><span>${episodes.length} قسمت ثبت‌شده</span></div></form></section>
        ${seasons.length ? `<section class="admin-panel"><div class="panel-head"><div><h2>فصل‌ها و بخش‌ها</h2><p>ترتیب نمایش، عنوان فصل و نام بخش دوره را ویرایش کن.</p></div></div><div class="season-edit-list">${seasons.map((season) => `<form class="season-edit-card" data-season-id="${season.id}"><strong>${esc(season.display_name || `فصل ${season.display_number}`)}</strong><div class="form-grid"><label>ترتیب فصل<input name="display_number" type="number" min="1" max="1000" value="${season.display_number}" required></label><label>عنوان فصل یا موضوع<input name="display_name" maxlength="200" value="${esc(season.display_name || '')}" placeholder="فصل ${season.display_number}"></label><label>شماره بخش<input name="section_number" type="number" min="1" max="1000" value="${season.section_number}" required></label><label>نام بخش<input name="section_name" maxlength="200" value="${esc(season.section_name || '')}" placeholder="بخش ${season.section_number}"></label></div><button class="outline-button" type="submit">ذخیره فصل</button></form>`).join('')}</div></section>` : ''}
        <section class="admin-panel"><div class="panel-head"><div><h2>قسمت‌ها و فایل‌های سورس</h2><p>شماره، نام، فصل و آدرس فایل هر قسمت را تغییر بده. مسیر باید در دسترس /media باشد.</p></div><span class="step-pill">${episodes.length} قسمت</span></div>
          <label class="episode-filter">جست‌وجوی قسمت<input id="episode-edit-filter" type="search" placeholder="نام، شماره یا مسیر فایل"></label><div class="episode-edit-list" id="episode-edit-list"></div></section>`;

      document.querySelector('#title-edit-form').addEventListener('submit', async (event) => {
        event.preventDefault();
        const form = new FormData(event.currentTarget);
        const payload = { name: form.get('name'), kind: form.get('kind'), synopsis: form.get('synopsis'),
          year: form.get('year') || null, poster_episode_id: form.get('poster_episode_id') || null };
        try { await api(`/api/admin/title/${title.id}`, { method: 'PATCH', body: JSON.stringify(payload) }); toast('مشخصات عنوان ذخیره شد.'); await showTitleEditor(title.id); }
        catch (error) { toast(error.message, 'error'); }
      });
      bindContentActions(target, async (action, kind) => {
        if (kind === 'title' && action === 'delete') { location.hash = '/admin/content'; return; }
        await showTitleEditor(title.id);
      });

      document.querySelectorAll('.season-edit-card').forEach((formElement) => formElement.addEventListener('submit', async (event) => {
        event.preventDefault();
        const form = new FormData(formElement);
        const payload = { display_number: Number(form.get('display_number')), display_name: form.get('display_name'),
          section_number: Number(form.get('section_number')), section_name: form.get('section_name') };
        try { await api(`/api/admin/season/${formElement.dataset.seasonId}`, { method: 'PATCH', body: JSON.stringify(payload) }); toast('فصل ذخیره شد و فهرست YAML آن به‌روز شد.'); await showTitleEditor(title.id); }
        catch (error) { toast(error.message, 'error'); }
      }));

      const episodeBox = document.querySelector('#episode-edit-list');
      const renderEpisodeRows = (query = '') => {
        const needle = query.trim().toLocaleLowerCase();
        const filtered = episodes.filter((episode) => !needle || `${episode.number} ${episode.name} ${episode.source_path}`.toLocaleLowerCase().includes(needle));
        episodeBox.innerHTML = filtered.length ? `${filtered.slice(0, 100).map((episode, index) => `<details class="episode-edit-row" ${index === 0 && !needle ? 'open' : ''}>
          <summary><b>${String(episode.number).padStart(2, '0')}</b><span>${esc(episode.name || `قسمت ${episode.number}`)}</span><small>${episode.deleted_at ? 'در زباله‌دان' : episode.hidden ? 'مخفی' : esc(statusText[episode.status] || episode.status)}</small><code>${esc(episode.source_path)}</code></summary>
          <form class="episode-edit-form" data-episode-id="${episode.id}"><div class="form-grid"><label>شماره قسمت<input name="number" type="number" min="1" max="10000" value="${episode.number}" required></label><label>نام نمایشی<input name="name" maxlength="200" value="${esc(episode.name || '')}" placeholder="قسمت ${episode.number}"></label>
          <label>فصل یا موضوع<select name="season_id"><option value="">بدون فصل</option>${seasons.map((season) => `<option value="${season.id}" ${Number(episode.season_id) === Number(season.id) ? 'selected' : ''}>${esc(season.section_name || `بخش ${season.section_number}`)} · ${esc(season.display_name || `فصل ${season.display_number}`)}</option>`).join('')}</select></label>
          <label class="field-wide">آدرس فایل سورس<div class="input-action"><input name="source" required dir="ltr" value="/media/${esc(episode.source_path)}"><button type="button" class="outline-button episode-source-check">بررسی فایل</button></div><small class="field-hint">وضعیت فعلی: ${esc(statusText[episode.status] || episode.status)}${episode.duration ? ` · ${Math.round(episode.duration / 60)} دقیقه` : ''}</small><span class="source-check"></span></label></div><div class="form-actions episode-form-actions"><button class="button button-primary" type="submit">ذخیره قسمت</button><button class="outline-button episode-rebuild" type="button">بازسازی فریم و پخش</button><span>با تغییر سورس، تصویر و پخش دوباره آماده می‌شود.</span><div class="content-actions episode-state-actions">${contentActions('episode', episode)}</div></div></form></details>`).join('')}${filtered.length > 100 ? `<div class="empty-inline">۱۰۰ مورد اول نمایش داده شده؛ جست‌وجو را دقیق‌تر کن.</div>` : ''}` : '<div class="empty-inline">قسمتی با این عبارت پیدا نشد.</div>';
        episodeBox.querySelectorAll('.episode-edit-form').forEach((formElement) => formElement.addEventListener('submit', async (event) => {
          event.preventDefault();
          const form = new FormData(formElement);
          const payload = { number: Number(form.get('number')), name: form.get('name'), source: form.get('source'), season_id: form.get('season_id') || null };
          try {
            const result = await api(`/api/admin/episode/${formElement.dataset.episodeId}`, { method: 'PATCH', body: JSON.stringify(payload) });
            toast(result.source_changed ? 'سورس ذخیره شد؛ آماده‌سازی دوباره شروع شد.' : 'مشخصات قسمت ذخیره شد.');
            await showTitleEditor(title.id);
          } catch (error) { toast(error.message, 'error'); }
        }));
        episodeBox.querySelectorAll('.episode-source-check').forEach((button) => button.addEventListener('click', async () => {
          const form = button.closest('form');
          const output = form.querySelector('.source-check');
          output.dataset.tone = 'pending'; output.textContent = 'در حال بررسی سورس…';
          try {
            const result = await api('/api/admin/source/check', { method: 'POST', body: JSON.stringify({ source: form.querySelector('[name=source]').value.trim() }) });
            output.dataset.tone = 'ok';
            output.innerHTML = `<span class="source-inspect"><img src="${result.preview}" alt="فریم نمونه"><span>قابل خواندن · ${esc(result.resolution)} · ${Math.round(result.duration / 60)} دقیقه</span></span>`;
          } catch (error) { output.dataset.tone = 'error'; output.textContent = error.message; }
        }));
        episodeBox.querySelectorAll('.episode-rebuild').forEach((button) => button.addEventListener('click', async () => {
          const form = button.closest('form');
          try {
            await api(`/api/admin/episode/${form.dataset.episodeId}/refresh`, { method: 'POST' });
            toast('قسمت دوباره به صف آماده‌سازی رفت.');
            await showTitleEditor(title.id);
          } catch (error) { toast(error.message, 'error'); }
        }));
        bindContentActions(episodeBox, async () => { await showTitleEditor(title.id); });
      };
      renderEpisodeRows();
      document.querySelector('#episode-edit-filter').addEventListener('input', (event) => renderEpisodeRows(event.currentTarget.value));
    } catch (error) {
      target.innerHTML = `<section class="state-panel"><span class="state-symbol">!</span><h1>محتوا پیدا نشد</h1><p>${esc(error.message)}</p><a class="button button-quiet" href="#/admin/content">بازگشت به کتابخانه</a></section>`;
    }
  }

  function showAdd() {
    const target = document.querySelector('#admin-content'); if (!target) return;
    target.innerHTML = `<div class="add-tabs" role="tablist"><button class="add-tab active" data-panel="movie" role="tab"><span>▧</span><strong>فیلم یا تک‌قسمتی</strong><small>یک فایل و یک عنوان</small></button><button class="add-tab" data-panel="series" role="tab"><span>▤</span><strong>سریال با YAML</strong><small>فصل و فهرست قسمت‌ها</small></button><button class="add-tab" data-panel="folder" role="tab"><span>▦</span><strong>ورود از پوشه</strong><small>شماره‌گذاری خودکار</small></button></div><div id="add-form-panel"></div>`;
    document.querySelectorAll('.add-tab').forEach((button) => button.addEventListener('click', () => {
      document.querySelectorAll('.add-tab').forEach((tab) => tab.classList.toggle('active', tab === button));
      if (button.dataset.panel === 'movie') movieForm();
      if (button.dataset.panel === 'series') seriesForm();
      if (button.dataset.panel === 'folder') folderForm();
    }));
    movieForm();
  }

  function commonTitleFields() {
    return `<label>نام محتوا<input name="name" required maxlength="200" placeholder="مثلاً: میان‌ستاره‌ای"></label><label>سال ساخت <span class="optional">اختیاری</span><input name="year" type="number" min="1880" max="2200" placeholder="2024"></label><label class="field-wide">توضیح کوتاه <span class="optional">اختیاری</span><textarea name="synopsis" rows="3" maxlength="3000" placeholder="یک معرفی کوتاه برای صفحه عنوان"></textarea></label>`;
  }

  function movieForm() {
    const target = document.querySelector('#add-form-panel'); if (!target) return;
    target.innerHTML = `<section class="admin-panel form-panel"><div class="panel-head"><div><h2>افزودن فیلم یا محتوای تک‌قسمتی</h2><p>مسیر فایل باید درون /media باشد و فایل اصلی جابه‌جا نمی‌شود.</p></div><span class="step-pill">۱ / ۱</span></div><form id="movie-form" class="form-grid">${commonTitleFields()}<label class="field-wide">مسیر سورس ویدیو<div class="input-action"><input name="source" required placeholder="/media/Movies/film.mkv" dir="ltr"><button type="button" class="outline-button" id="check-movie-source">بررسی فایل</button></div><small class="field-hint">نمونه: /media/Movies/film.mkv</small><span class="source-check" id="movie-source-result"></span></label><div class="form-actions field-wide"><button class="button button-primary" type="submit">ثبت و آماده‌سازی</button><span>فریم کاور از همین سورس گرفته می‌شود.</span></div></form></section>`;
    document.querySelector('#check-movie-source').addEventListener('click', async () => {
      const value = document.querySelector('#movie-form [name=source]').value.trim();
      const result = document.querySelector('#movie-source-result');
      result.innerHTML = '<span class="spinner"></span> در حال بررسی فایل و استخراج فریم…';
      result.dataset.tone = 'pending';
      try {
        const info = await api('/api/admin/source/check', { method: 'POST', body: JSON.stringify({ source: value }) });
        const minutes = Math.max(1, Math.round(info.duration / 60));
        result.dataset.tone = 'ok';
        result.innerHTML = `<div class="source-inspect"><img src="${info.preview}" alt="فریم نمونه از ویدیو"><div><strong>فایل قابل خواندن است</strong><span>${esc(info.resolution)} · ${esc(info.codec)} · ${minutes} دقیقه</span><small>فریم از خود سورس گرفته شده؛ کیفیت اصلی حفظ می‌شود.</small></div></div>`;
      } catch (error) { result.dataset.tone = 'error'; result.textContent = error.message; }
    });
    document.querySelector('#movie-form').addEventListener('submit', async (event) => {
      event.preventDefault(); const form = new FormData(event.currentTarget);
      const payload = { name: form.get('name'), synopsis: form.get('synopsis'), year: form.get('year') || null, source: form.get('source') };
      try { await api('/api/admin/movie', { method: 'POST', body: JSON.stringify(payload) }); toast('عنوان ثبت شد؛ آماده‌سازی در پس‌زمینه شروع شد.'); location.hash = '/admin/jobs'; }
      catch (error) { toast(error.message, 'error'); }
    });
  }

  function seriesForm() {
    const target = document.querySelector('#add-form-panel'); if (!target) return;
    target.innerHTML = `<section class="admin-panel form-panel"><div class="panel-head"><div><h2>ساخت سریال و فایل YAML</h2><p>نام و فصل را یک‌بار وارد کن؛ فایل فهرست قسمت‌ها خودکار ساخته می‌شود.</p></div><span class="step-pill">فرم سریال</span></div><form id="series-form" class="form-grid">${commonTitleFields()}<label>شماره فصل<input name="season" type="number" min="1" value="1" required></label><div class="field-wide episode-editor"><div class="episode-editor-head"><div><strong>قسمت‌های این فصل</strong><small>برای هر قسمت شماره و مسیر سورس را بنویس.</small></div><button type="button" class="outline-button" id="add-series-episode">＋ افزودن قسمت</button></div><div id="episode-rows"></div></div><details class="yaml-preview field-wide"><summary>مشاهده YAML نهایی</summary><pre id="series-yaml-preview" dir="ltr"></pre></details><div class="form-actions field-wide"><button class="button button-primary" type="submit">ساخت YAML و افزودن قسمت‌ها</button><span>فایل YAML در پوشه داده لویت ساخته می‌شود.</span></div></form></section>`;
    const rows = document.querySelector('#episode-rows');
    const addRow = (number = rows.children.length + 1) => {
      const row = document.createElement('div');
      row.className = 'episode-editor-row';
      row.innerHTML = `<label>قسمت<input class="episode-number" type="number" min="1" value="${number}" required></label><label>نام <span class="optional">اختیاری</span><input class="episode-name" maxlength="200" placeholder="قسمت اول"></label><label class="episode-source-label">مسیر سورس<input class="episode-source" required placeholder="/media/Series/show/s01e01.mkv" dir="ltr"></label><button class="remove-episode" type="button" aria-label="حذف قسمت">−</button>`;
      rows.append(row);
      row.querySelectorAll('input').forEach((input) => input.addEventListener('input', updateSeriesYaml));
      row.querySelector('.remove-episode').addEventListener('click', () => { row.remove(); renumberRows(); updateSeriesYaml(); });
      updateSeriesYaml();
    };
    const renumberRows = () => rows.querySelectorAll('.episode-editor-row').forEach((row, index) => {
      row.querySelector('.episode-number').value = String(index + 1);
    });
    const quoteYaml = (value) => JSON.stringify(String(value));
    const updateSeriesYaml = () => {
      const name = document.querySelector('#series-form [name=name]').value.trim() || 'نام سریال';
      const season = Number(document.querySelector('#series-form [name=season]').value) || 1;
      const episodes = [...rows.querySelectorAll('.episode-editor-row')].map((row) => ({
        number: Number(row.querySelector('.episode-number').value) || 1,
        name: row.querySelector('.episode-name').value.trim(),
        source: row.querySelector('.episode-source').value.trim()
      }));
      const lines = [`series: ${quoteYaml(name)}`, `season: ${season}`, 'episodes:'];
      episodes.forEach((episode) => {
        lines.push(`  - number: ${episode.number}`);
        if (episode.name) lines.push(`    title: ${quoteYaml(episode.name)}`);
        lines.push(`    source: ${quoteYaml(episode.source)}`);
      });
      document.querySelector('#series-yaml-preview').textContent = lines.join('\n');
    };
    addRow(1);
    document.querySelector('#series-form [name=name]').addEventListener('input', updateSeriesYaml);
    document.querySelector('#series-form [name=season]').addEventListener('input', updateSeriesYaml);
    document.querySelector('#add-series-episode').addEventListener('click', () => addRow());
    document.querySelector('#series-form').addEventListener('submit', async (event) => {
      event.preventDefault(); const form = new FormData(event.currentTarget);
      const payload = { name: form.get('name'), synopsis: form.get('synopsis'), year: form.get('year') || null, season: Number(form.get('season')), manifest: document.querySelector('#series-yaml-preview').textContent };
      try {
        const result = await api('/api/admin/series', { method: 'POST', body: JSON.stringify(payload) });
        toast(`${result.episode_ids.length} قسمت ثبت شد؛ فایل YAML: ${result.host_manifest || result.manifest}`); location.hash = '/admin/jobs';
      } catch (error) { toast(error.message, 'error'); }
    });
  }

  function folderForm() {
    const target = document.querySelector('#add-form-panel'); if (!target) return;
    target.innerHTML = `<section class="admin-panel form-panel"><div class="panel-head"><div><h2>افزودن فصل از روی پوشه</h2><p>فایل‌ها فقط فهرست می‌شوند؛ هیچ‌چیز کپی یا جابه‌جا نمی‌شود.</p></div><span class="step-pill">پیش‌نمایش لازم است</span></div><form id="folder-form" class="form-grid">${commonTitleFields()}<label>شماره فصل<input name="season" type="number" min="1" value="1" required></label><label class="field-wide">مسیر پوشه<div class="input-action"><input name="folder" required placeholder="/media/Series/show/season-1" dir="ltr"><button type="button" class="outline-button" id="choose-folder">انتخاب پوشه</button><button type="button" class="outline-button" id="preview-folder">پیش‌نمایش فایل‌ها</button></div><small class="field-hint">نام‌های S01E02 برای شماره قسمت خوانده می‌شوند؛ بقیه فایل‌ها طبیعی مرتب می‌شوند.</small></label><div id="folder-preview" class="folder-preview field-wide" hidden></div><div class="form-actions field-wide"><button id="commit-folder" class="button button-primary" type="submit" disabled>ثبت فصل و ساخت YAML</button><span>فقط ویدیوهای داخل همین پوشه بررسی می‌شوند.</span></div></form></section>`;
    document.querySelector('#choose-folder').addEventListener('click', () => openFolderPicker());
    document.querySelector('#preview-folder').addEventListener('click', async () => {
      const folder = document.querySelector('#folder-form [name=folder]').value.trim();
      const box = document.querySelector('#folder-preview'); box.hidden = false; box.innerHTML = '<span class="spinner"></span> در حال خواندن پوشه…';
      try {
        const season = Number(document.querySelector('#folder-form [name=season]').value) || 1;
        folderPreview = await api('/api/admin/import/preview', { method: 'POST', body: JSON.stringify({ folder, season }) });
        box.innerHTML = `<div class="preview-head"><strong>${folderPreview.episodes.length} فایل پیدا شد</strong><span>${esc(folderPreview.folder)}</span></div><div class="preview-episodes">${folderPreview.episodes.map((ep) => `<div><b>${String(ep.number).padStart(2, '0')}</b><span>${esc(ep.name || 'بدون عنوان')}</span><code>${esc(ep.source_path)}</code></div>`).join('')}</div>`;
        document.querySelector('#commit-folder').disabled = false;
      } catch (error) { folderPreview = null; box.innerHTML = `<div class="form-error">${esc(error.message)}</div>`; document.querySelector('#commit-folder').disabled = true; }
    });
    document.querySelector('#folder-form').addEventListener('submit', async (event) => {
      event.preventDefault(); if (!folderPreview) return;
      const form = new FormData(event.currentTarget);
      const payload = { name: form.get('name'), synopsis: form.get('synopsis'), year: form.get('year') || null, season: Number(form.get('season')), folder: form.get('folder') };
      try { const result = await api('/api/admin/import/commit', { method: 'POST', body: JSON.stringify(payload) }); toast(`${result.episode_ids.length} قسمت ثبت شد؛ فایل YAML: ${result.host_manifest || result.manifest}`); location.hash = '/admin/jobs'; }
      catch (error) { toast(error.message, 'error'); }
    });
  }

  async function openFolderPicker(startPath = '/media') {
    let overlay = document.querySelector('.folder-picker-overlay');
    if (!overlay) {
      overlay = document.createElement('div');
      overlay.className = 'folder-picker-overlay';
      overlay.innerHTML = '<section class="folder-picker" role="dialog" aria-modal="true" aria-labelledby="folder-picker-title"></section>';
      document.body.append(overlay);
      overlay.addEventListener('click', (event) => { if (event.target === overlay) overlay.remove(); });
    }
    const pane = overlay.querySelector('.folder-picker');
    pane.innerHTML = '<div class="picker-loading"><span class="spinner"></span> در حال خواندن پوشه‌ها…</div>';
    try {
      const data = await api(`/api/admin/browse?path=${encodeURIComponent(startPath)}`);
      const crumbs = data.path.split('/').filter(Boolean);
      pane.innerHTML = `<header class="picker-head"><div><span class="admin-kicker">انتخاب از کتابخانه رسانه</span><h2 id="folder-picker-title">انتخاب پوشه فصل</h2></div><button class="picker-close" aria-label="بستن">×</button></header>
        <div class="picker-path"><span>مسیر داخل کانتینر</span><code>${esc(data.path)}</code></div>
        <div class="picker-crumbs">${crumbs.map((part, index) => `<button data-crumb="${index}">${esc(part)}</button>`).join('<i>‹</i>')}</div>
        <div class="picker-list">${data.parent ? `<button class="picker-dir picker-parent" data-dir="${esc(data.parent)}"><span class="folder-icon">↰</span><span>پوشه بالاتر</span><small>بازگشت</small></button>` : ''}
          ${data.directories.map((dir) => `<button class="picker-dir" data-dir="${esc(dir.path)}"><span class="folder-icon">▰</span><span>${esc(dir.name)}</span><small>بازکردن <b>‹</b></small></button>`).join('') || '<div class="picker-empty">زیرپوشه‌ای در این مسیر نیست.</div>'}</div>
        <footer class="picker-foot"><span>${data.video_count} فایل ویدیویی در این پوشه</span><button class="button button-primary" id="use-folder">انتخاب این پوشه</button></footer>`;
      pane.querySelector('.picker-close').addEventListener('click', () => overlay.remove());
      pane.querySelectorAll('[data-dir]').forEach((button) => button.addEventListener('click', () => openFolderPicker(button.dataset.dir)));
      pane.querySelectorAll('[data-crumb]').forEach((button) => button.addEventListener('click', () => {
        const count = Number(button.dataset.crumb) + 1;
        openFolderPicker(`/${crumbs.slice(0, count).join('/')}`);
      }));
      pane.querySelector('#use-folder').addEventListener('click', () => {
        const input = document.querySelector('#folder-form [name=folder]');
        input.value = data.path;
        overlay.remove();
        folderPreview = null;
        document.querySelector('#folder-preview').hidden = true;
        document.querySelector('#commit-folder').disabled = true;
      });
    } catch (error) {
      pane.innerHTML = `<header class="picker-head"><div><h2 id="folder-picker-title">پوشه باز نشد</h2></div><button class="picker-close" aria-label="بستن">×</button></header><div class="form-error">${esc(error.message)}</div>`;
      pane.querySelector('.picker-close').addEventListener('click', () => overlay.remove());
    }
  }

  async function showJobs() {
    const target = document.querySelector('#admin-content'); if (!target) return;
    try {
      const data = await api('/api/admin/jobs');
      target.innerHTML = `<section class="admin-panel"><div class="panel-head"><div><h2>وضعیت آماده‌سازی محتوا</h2><p>این صفحه هر چند ثانیه تازه می‌شود.</p></div><button class="outline-button" id="refresh-jobs">تازه‌سازی</button></div><div class="jobs-list">${data.items.map(jobRow).join('') || '<div class="empty-inline">هنوز کاری در صف نیست.</div>'}</div></section>`;
      document.querySelector('#refresh-jobs').addEventListener('click', () => showJobs());
      document.querySelectorAll('[data-retry]').forEach((button) => button.addEventListener('click', async () => {
        try { await api(`/api/admin/jobs/${button.dataset.retry}/retry`, { method: 'POST' }); toast('کار دوباره به صف رفت.'); showJobs(); }
        catch (error) { toast(error.message, 'error'); }
      }));
    } catch (error) { target.innerHTML = `<div class="form-error">${esc(error.message)}</div>`; }
  }

  function dispatch() {
    if (playerCleanup) playerCleanup();
    if (hlsInstance) { hlsInstance.destroy(); hlsInstance = null; }
    if (playerKeyHandler) { window.removeEventListener('keydown', playerKeyHandler); playerKeyHandler = null; }
    clearInterval(adminPoll);
    clearTimeout(searchTimer);
    searchSequence += 1;
    document.body.classList.remove('topbar-hidden');
    topbarLastScrollY = window.scrollY;
    topbarScrollDirection = 0;
    topbarScrollDistance = 0;
    const current = route().split('/').filter(Boolean);
    if (!current.length) { loadBrowse(); return; }
    if (current[0] === 'continue') { loadContinue(); return; }
    if (current[0] === 'search' && current[1]) {
      try { loadSearch(decodeURIComponent(current.slice(1).join('/'))); }
      catch { loadSearch(current.slice(1).join('/')); }
      return;
    }
    if (current[0] === 'browse') { loadBrowse(current[1] === 'movie' || current[1] === 'series' ? current[1] : 'all'); return; }
    if (current[0] === 'title' && current[1]) { renderTitle(current[1]); return; }
    if (current[0] === 'watch' && current[1]) { renderWatch(current[1]); return; }
    if (current[0] === 'admin') { renderAdmin(); return; }
    loadBrowse();
  }

  let scrollFadeFrame = 0;
  const syncPageScrollFade = () => {
    const fade = document.querySelector('#page-scroll-fade');
    const root = document.documentElement;
    const canScroll = Boolean(fade && !document.querySelector('.watch-page') && root.scrollHeight > window.innerHeight + 12);
    document.body.classList.toggle('page-scroll-active', canScroll);
    document.body.classList.toggle('page-scroll-end', window.scrollY + window.innerHeight >= root.scrollHeight - 12);
    const backTop = document.querySelector('#back-to-top');
    if (backTop) {
      const visible = window.scrollY > 420;
      backTop.classList.toggle('is-visible', visible);
      backTop.setAttribute('aria-hidden', String(!visible));
      backTop.tabIndex = visible ? 0 : -1;
      const footer = document.querySelector('.site-footer');
      const style = getComputedStyle(backTop);
      const baseBottom = Number.parseFloat(style.getPropertyValue('--back-top-base-bottom')) || 25;
      const footerGap = Number.parseFloat(style.getPropertyValue('--back-top-footer-gap')) || 12;
      const lift = footer
        ? Math.max(0, window.innerHeight - footer.getBoundingClientRect().top + footerGap - baseBottom)
        : 0;
      backTop.style.setProperty('--footer-lift', `${lift}px`);
    }
    const header = document.querySelector('.topbar');
    const currentY = window.scrollY;
    const delta = currentY - topbarLastScrollY;
    topbarLastScrollY = currentY;
    if (!header || currentY < 24) {
      document.body.classList.remove('topbar-hidden');
      topbarScrollDirection = 0;
      topbarScrollDistance = 0;
      return;
    }
    if (!delta) return;
    const direction = Math.sign(delta);
    if (direction !== topbarScrollDirection) {
      topbarScrollDirection = direction;
      topbarScrollDistance = Math.abs(delta);
    } else {
      topbarScrollDistance += Math.abs(delta);
    }
    if (topbarScrollDistance >= 100) {
      document.body.classList.toggle('topbar-hidden', direction > 0);
      topbarScrollDistance = 0;
    }
  };
  const schedulePageScrollFade = () => {
    window.cancelAnimationFrame(scrollFadeFrame);
    scrollFadeFrame = window.requestAnimationFrame(syncPageScrollFade);
  };
  new MutationObserver(schedulePageScrollFade).observe(app, { childList: true, subtree: true, characterData: true });
  window.addEventListener('scroll', schedulePageScrollFade, { passive: true });
  window.addEventListener('resize', schedulePageScrollFade);
  document.addEventListener('click', (event) => {
    if (!event.target.closest('.search-container')) {
      const panel = document.querySelector('#search-popover');
      const input = document.querySelector('#global-search');
      if (panel && input) { panel.hidden = true; input.setAttribute('aria-expanded', 'false'); }
    }
    if (event.target.closest('#back-to-top')) window.scrollTo({ top: 0, behavior: 'smooth' });
  });

  window.addEventListener('hashchange', dispatch);
  dispatch();
})();
