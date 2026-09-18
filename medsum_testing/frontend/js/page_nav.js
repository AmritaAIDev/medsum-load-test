/**
 * Collapsible sidebar + hash-based page routing.
 * Pages: #dashboard, #runs, #load-testing, #detail/{testId}
 */
(function (root) {
  const LIST_PAGES = ['dashboard', 'runs', 'load-testing'];
  const STORAGE_KEY = 'medsum.sidebarCollapsed';

  let onChange = null;
  let openDetail = null;
  let lastListPage = 'dashboard';
  let bound = false;

  function persistApi() {
    return root.MedsumSessionPersist || {};
  }

  function parseHash(hash) {
    const raw = String(hash == null ? '' : hash).replace(/^#/, '').trim();
    if (!raw) return { page: 'dashboard' };
    const parts = raw.split('/').filter(Boolean);
    const page = String(parts[0] || '').toLowerCase();
    if (page === 'detail') {
      let testId = parts.slice(1).join('/');
      try { testId = decodeURIComponent(testId); } catch (err) { /* keep raw */ }
      if (testId) return { page: 'detail', testId };
      return { page: 'dashboard' };
    }
    if (LIST_PAGES.indexOf(page) !== -1) return { page };
    return { page: 'dashboard' };
  }

  function hashFor(route) {
    const data = route || { page: 'dashboard' };
    if (data.page === 'detail' && data.testId) {
      return '#detail/' + encodeURIComponent(data.testId);
    }
    if (LIST_PAGES.indexOf(data.page) !== -1) return '#' + data.page;
    return '#dashboard';
  }

  function currentHash() {
    return '#' + String((root.location && root.location.hash) || '').replace(/^#/, '');
  }

  function appShell() {
    return document.getElementById('app-shell') || document.querySelector('.app-shell');
  }

  function loadCollapsed() {
    const api = persistApi();
    if (api.loadSidebarCollapsed) return !!api.loadSidebarCollapsed();
    try {
      return String((root.localStorage && root.localStorage.getItem(STORAGE_KEY)) || '') === '1';
    } catch (err) {
      return false;
    }
  }

  function saveCollapsed(collapsed) {
    const api = persistApi();
    if (api.saveSidebarCollapsed) {
      api.saveSidebarCollapsed(!!collapsed);
      return;
    }
    try {
      if (root.localStorage) root.localStorage.setItem(STORAGE_KEY, collapsed ? '1' : '0');
    } catch (err) { /* private mode */ }
  }

  function brand() {
    return document.getElementById('sidebar-brand');
  }

  function isCollapsed() {
    const shell = appShell();
    return !!(shell && shell.classList.contains('sidebar-collapsed'));
  }

  function syncBrand() {
    const el = brand();
    if (!el) return;
    const collapsed = isCollapsed();
    el.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
    const label = collapsed ? 'Expand sidebar' : 'Collapse sidebar';
    el.setAttribute('aria-label', label);
    el.setAttribute('title', label);
  }

  function setCollapsed(collapsed, persist) {
    const shell = appShell();
    if (!shell) return;
    shell.classList.toggle('sidebar-collapsed', !!collapsed);
    if (persist !== false) saveCollapsed(!!collapsed);
    syncBrand();
    scheduleResize();
  }

  function toggleSidebar() {
    setCollapsed(!isCollapsed(), true);
  }

  function restoreSidebar() {
    setCollapsed(loadCollapsed(), false);
  }

  function isSidebarPageLink(target) {
    return !!(target && target.closest && target.closest('.nav-item, .logout-btn'));
  }

  function setActiveView(page) {
    const target = LIST_PAGES.indexOf(page) !== -1 || page === 'detail' ? page : 'dashboard';
    document.querySelectorAll('.page-view').forEach(el => {
      el.classList.toggle('is-active', el.getAttribute('data-page') === target);
    });
    const navPage = target === 'detail' ? lastListPage : target;
    document.querySelectorAll('.nav-item').forEach(item => {
      item.classList.toggle('active', item.dataset.nav === navPage);
    });
  }

  function scheduleResize() {
    if (!root.dispatchEvent) return;
    const fire = function () {
      try { root.dispatchEvent(new Event('resize')); } catch (err) { /* ignore */ }
    };
    if (root.setTimeout) root.setTimeout(fire, 220);
    else fire();
  }

  function applyRoute(route) {
    const next = route && route.page ? route : parseHash(root.location && root.location.hash);
    if (LIST_PAGES.indexOf(next.page) !== -1) lastListPage = next.page;
    setActiveView(next.page);
    if (typeof onChange === 'function') onChange(next);
    if (next.page === 'detail' && next.testId && typeof openDetail === 'function') {
      openDetail(next.testId);
    }
    scheduleResize();
  }

  function navigate(page, extra) {
    extra = extra || {};
    const route = page === 'detail'
      ? { page: 'detail', testId: extra.testId || '' }
      : { page: LIST_PAGES.indexOf(page) !== -1 ? page : 'dashboard' };
    const nextHash = hashFor(route);
    if (currentHash() === nextHash) {
      applyRoute(route);
      return;
    }
    if (extra.replace && root.history && root.history.replaceState) {
      root.history.replaceState(null, '', nextHash);
      applyRoute(route);
      return;
    }
    if (root.location) root.location.hash = nextHash;
  }

  function syncHash() {
    const route = parseHash(root.location && root.location.hash);
    const canonical = hashFor(route);
    if (currentHash() !== canonical && root.history && root.history.replaceState) {
      root.history.replaceState(null, '', canonical);
    }
    applyRoute(route);
  }

  function bind(options) {
    options = options || {};
    onChange = options.onChange || null;
    openDetail = options.openDetail || null;
    if (bound) {
      syncHash();
      return;
    }
    bound = true;

    restoreSidebar();

    const sidebar = document.getElementById('app-sidebar');
    if (sidebar) {
      sidebar.addEventListener('click', function (event) {
        if (isSidebarPageLink(event.target)) return;
        toggleSidebar();
      });
    }

    const brandEl = brand();
    if (brandEl) {
      brandEl.addEventListener('keydown', function (event) {
        if (event.key !== 'Enter' && event.key !== ' ') return;
        event.preventDefault();
        toggleSidebar();
      });
    }

    document.querySelectorAll('.nav-item').forEach(item => {
      item.addEventListener('click', function (event) {
        event.preventDefault();
        event.stopPropagation();
        navigate(item.dataset.nav);
      });
    });

    const logoutBtn = sidebar && sidebar.querySelector('.logout-btn');
    if (logoutBtn) {
      logoutBtn.addEventListener('click', function (event) {
        event.stopPropagation();
      });
    }

    root.addEventListener('hashchange', function () {
      applyRoute(parseHash(root.location && root.location.hash));
    });

    syncHash();
  }

  const api = {
    LIST_PAGES,
    STORAGE_KEY,
    parseHash,
    hashFor,
    navigate,
    applyRoute,
    setActiveView,
    currentListPage: function () { return lastListPage; },
    bind,
    toggleSidebar,
    setCollapsed,
  };

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = api;
  }
  root.MedsumPageNav = api;
})(typeof window !== 'undefined' ? window : globalThis);
