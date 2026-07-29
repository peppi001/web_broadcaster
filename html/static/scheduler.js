(function () {
  'use strict';

  function qs(sel, root) { return (root || document).querySelector(sel); }
  function qsa(sel, root) { return Array.from((root || document).querySelectorAll(sel)); }

  const backdrop = qs('#scheduler-modal-backdrop');
  const newBtn = qs('#scheduler-new-rule-btn');
  const cancelBtn = qs('#scheduler-modal-cancel');
  const closeBtn = qs('#scheduler-modal-close');
  const saveBtn = qs('#scheduler-modal-save');
  const form = qs('#scheduler-new-rule-form');
  const errBox = qs('#scheduler-form-error');
  const urlBackdrop = qs('#scheduler-url-modal-backdrop');
  const fileBackdrop = qs('#scheduler-file-modal-backdrop');
  const fileCategoriesEl = qs('#scheduler-file-categories');
  const fileTracksEl = qs('#scheduler-file-tracks');
  const fileTracksTitleEl = qs('#scheduler-file-tracks-title');
  const fileOkBtn = qs('#scheduler-file-ok');
  const fileCancelBtn = qs('#scheduler-file-cancel');
  const fileCloseBtn = qs('#scheduler-file-close');

  const urlInput = qs('#scheduler-url-input');
  const urlDurationInput = qs('#scheduler-url-duration');
  const urlInfiniteCb = qs('#scheduler-url-infinite');
  const urlCancelBtn = qs('#scheduler-url-cancel');
  const urlCloseBtn = qs('#scheduler-url-close');
  const urlOkBtn = qs('#scheduler-url-ok');

  const modalTitle = qs('#scheduler-modal-title');
  const modalTitlebar = qs('#scheduler-modal-titlebar');
  const modal = backdrop ? backdrop.querySelector('.modal') : null;
  const modalResizeHandle = modal ? modal.querySelector('.panel-resize-handle') : null;

  const urlModal = qs('#scheduler-url-modal');
  const urlTitlebar = qs('#scheduler-url-modal-titlebar');
  const urlResizeHandle = urlModal ? urlModal.querySelector('.panel-resize-handle') : null;
  const fileModal = qs('#scheduler-file-modal');
  const fileTitlebar = qs('#scheduler-file-modal-titlebar');
  const fileResizeHandle = fileModal ? fileModal.querySelector('.panel-resize-handle') : null;

  let editingRuleId = null;
  let urlModalExternalResolver = null;
  let urlModalExternalRejecter = null;
  let urlModalExternalMode = null;
  let urlModalPreviousTitle = '';
  let schedulerModalDragState = null;
  let schedulerModalResizeState = null;
  let schedulerModalSuppressBackdropClickUntil = 0;

  const floatingStates = new Map();

  function getFloatingState(win) {
    if (!win) return null;
    if (!floatingStates.has(win)) {
      floatingStates.set(win, { dragState: null, resizeState: null, suppressBackdropClickUntil: 0 });
    }
    return floatingStates.get(win);
  }

  function clampFloatingModalPosition(win, left, top, width, height) {
    if (!win) return { left, top, width, height };
    const minLeft = 8;
    const minTop = 8;
    const viewportWidth = window.innerWidth || document.documentElement.clientWidth || 0;
    const viewportHeight = window.innerHeight || document.documentElement.clientHeight || 0;
    const explicitMinWidth = Number.parseInt((win.dataset && win.dataset.minWidth) || '', 10);
    const explicitMinHeight = Number.parseInt((win.dataset && win.dataset.minHeight) || '', 10);
    const minWidth = Number.isFinite(explicitMinWidth) ? explicitMinWidth : 520;
    const minHeight = Number.isFinite(explicitMinHeight) ? explicitMinHeight : 250;
    const maxWidth = Math.max(minWidth, viewportWidth - 16);
    const maxHeight = Math.max(minHeight, viewportHeight - 16);
    const safeWidth = Math.max(minWidth, Math.min(Number(width) || minWidth, maxWidth));
    const safeHeight = Math.max(minHeight, Math.min(Number(height) || minHeight, maxHeight));
    return {
      width: safeWidth,
      height: safeHeight,
      left: Math.max(minLeft, Math.min(Math.max(minLeft, viewportWidth - safeWidth - 8), left)),
      top: Math.max(minTop, Math.min(Math.max(minTop, viewportHeight - safeHeight - 8), top))
    };
  }

  function applyFloatingModalRect(win, left, top, width, height) {
    if (!win) return;
    const rect = clampFloatingModalPosition(win, left, top, width, height);
    win.style.width = `${Math.round(rect.width)}px`;
    win.style.height = `${Math.round(rect.height)}px`;
    win.style.left = `${Math.round(rect.left)}px`;
    win.style.top = `${Math.round(rect.top)}px`;
    win.style.right = 'auto';
    win.style.bottom = 'auto';
    win.style.margin = '0';
    win.style.transform = 'none';
  }

  function centerFloatingModal(win) {
    if (!win) return;
    const viewportWidth = window.innerWidth || document.documentElement.clientWidth || 0;
    const viewportHeight = window.innerHeight || document.documentElement.clientHeight || 0;
    const modalWidth = win.offsetWidth || win.getBoundingClientRect().width || Number.parseInt(win.style.width || '520', 10) || 520;
    const modalHeight = win.offsetHeight || win.getBoundingClientRect().height || Number.parseInt(win.style.height || '250', 10) || 250;
    applyFloatingModalRect(win, Math.round((viewportWidth - modalWidth) / 2), Math.round((viewportHeight - modalHeight) / 2), modalWidth, modalHeight);
  }

  function openFloatingModal(backdropEl, win, focusEl) {
    if (!backdropEl || !win) return;
    const schedulerIsOpen = !!(backdrop && backdrop.classList.contains('active') && backdrop.style.display !== 'none');
    if (schedulerIsOpen) {
      backdropEl.style.zIndex = '2147483647';
    } else {
      backdropEl.style.removeProperty('z-index');
    }
    backdropEl.style.display = 'flex';
    backdropEl.classList.add('active');
    backdropEl.setAttribute('aria-hidden', 'false');
    centerFloatingModal(win);
    if (focusEl) setTimeout(() => { try { focusEl.focus({ preventScroll: true }); } catch (_err) { focusEl.focus(); } }, 0);
  }

  function closeFloatingModal(backdropEl, win) {
    if (!backdropEl || !win) return;
    endFloatingModalInteraction(win);
    backdropEl.classList.remove('active');
    backdropEl.setAttribute('aria-hidden', 'true');
    backdropEl.style.display = 'none';
    backdropEl.style.removeProperty('z-index');
  }

  function onFloatingModalPointerMoveFactory(win) {
    return function onFloatingModalPointerMove(event) {
      const state = getFloatingState(win);
      if (!state || !win) return;
      if (state.dragState) {
        applyFloatingModalRect(
          win,
          state.dragState.startLeft + (event.clientX - state.dragState.startClientX),
          state.dragState.startTop + (event.clientY - state.dragState.startClientY),
          state.dragState.width,
          state.dragState.height
        );
        state.dragState.moved = true;
        return;
      }
      if (state.resizeState) {
        applyFloatingModalRect(
          win,
          state.resizeState.left,
          state.resizeState.top,
          state.resizeState.startWidth + (event.clientX - state.resizeState.startClientX),
          state.resizeState.startHeight + (event.clientY - state.resizeState.startClientY)
        );
        state.resizeState.moved = true;
      }
    };
  }

  function endFloatingModalInteraction(win) {
    const state = getFloatingState(win);
    if (!state || !win) return;
    const moved = !!((state.dragState && state.dragState.moved) || (state.resizeState && state.resizeState.moved));
    state.dragState = null;
    state.resizeState = null;
    win.classList.remove('is-dragging');
    win.classList.remove('is-resizing');
    const moveHandler = win._floatingMoveHandler;
    const endHandler = win._floatingEndHandler;
    if (moveHandler) document.removeEventListener('pointermove', moveHandler);
    if (endHandler) {
      document.removeEventListener('pointerup', endHandler);
      document.removeEventListener('pointercancel', endHandler);
    }
    if (moved) state.suppressBackdropClickUntil = Date.now() + 250;
  }

  function bindFloatingModal(backdropEl, win, titlebarEl, resizeHandleEl) {
    if (!backdropEl || !win || win.dataset.floatingSchedulerBound === '1') return;
    win.dataset.floatingSchedulerBound = '1';
    const moveHandler = onFloatingModalPointerMoveFactory(win);
    const endHandler = () => endFloatingModalInteraction(win);
    win._floatingMoveHandler = moveHandler;
    win._floatingEndHandler = endHandler;
    if (titlebarEl) {
      titlebarEl.addEventListener('pointerdown', event => {
        if (event.button !== 0) return;
        if (event.target.closest('button, a, input, select, textarea')) return;
        event.preventDefault();
        event.stopPropagation();
        const rect = win.getBoundingClientRect();
        const state = getFloatingState(win);
        state.dragState = {
          startClientX: event.clientX,
          startClientY: event.clientY,
          startLeft: Number.parseFloat(win.style.left) || rect.left,
          startTop: Number.parseFloat(win.style.top) || rect.top,
          width: win.offsetWidth || rect.width,
          height: win.offsetHeight || rect.height,
          moved: false
        };
        win.classList.add('is-dragging');
        document.addEventListener('pointermove', moveHandler);
        document.addEventListener('pointerup', endHandler);
        document.addEventListener('pointercancel', endHandler);
      });
    }
    if (resizeHandleEl) {
      resizeHandleEl.addEventListener('pointerdown', event => {
        if (event.button !== 0) return;
        event.preventDefault();
        event.stopPropagation();
        const rect = win.getBoundingClientRect();
        const state = getFloatingState(win);
        state.resizeState = {
          startClientX: event.clientX,
          startClientY: event.clientY,
          startWidth: win.offsetWidth || rect.width,
          startHeight: win.offsetHeight || rect.height,
          left: Number.parseFloat(win.style.left) || rect.left,
          top: Number.parseFloat(win.style.top) || rect.top,
          moved: false
        };
        win.classList.add('is-resizing');
        document.addEventListener('pointermove', moveHandler);
        document.addEventListener('pointerup', endHandler);
        document.addEventListener('pointercancel', endHandler);
      });
    }
    backdropEl.addEventListener('click', event => {
      if (event.target !== backdropEl) return;
      const state = getFloatingState(win);
      if (!state || state.dragState || state.resizeState || Date.now() < state.suppressBackdropClickUntil) return;
      closeFloatingModal(backdropEl, win);
    });
    window.addEventListener('resize', () => {
      if (backdropEl.style.display !== 'flex' || !backdropEl.classList.contains('active')) return;
      const rect = win.getBoundingClientRect();
      applyFloatingModalRect(win, Number.parseFloat(win.style.left) || rect.left, Number.parseFloat(win.style.top) || rect.top, win.offsetWidth || rect.width, win.offsetHeight || rect.height);
    });
  }

  function clampSchedulerModalPosition(left, top) {
    if (!modal) return { left, top };
    const minLeft = 8;
    const minTop = 8;
    const viewportWidth = window.innerWidth || document.documentElement.clientWidth || 0;
    const viewportHeight = window.innerHeight || document.documentElement.clientHeight || 0;
    const modalWidth = modal.offsetWidth || modal.getBoundingClientRect().width || 920;
    const modalHeight = modal.offsetHeight || modal.getBoundingClientRect().height || 560;
    return {
      left: Math.max(minLeft, Math.min(Math.max(minLeft, viewportWidth - modalWidth - 8), left)),
      top: Math.max(minTop, Math.min(Math.max(minTop, viewportHeight - modalHeight - 8), top))
    };
  }

  function applySchedulerModalRect(left, top, width, height) {
    if (!modal) return;
    const minWidth = 560;
    const minHeight = 360;
    const viewportWidth = window.innerWidth || document.documentElement.clientWidth || 0;
    const viewportHeight = window.innerHeight || document.documentElement.clientHeight || 0;
    const maxWidth = Math.max(minWidth, viewportWidth - 16);
    const maxHeight = Math.max(minHeight, viewportHeight - 16);
    const safeWidth = Math.max(minWidth, Math.min(Number(width) || minWidth, maxWidth));
    const safeHeight = Math.max(minHeight, Math.min(Number(height) || minHeight, maxHeight));
    modal.style.width = `${Math.round(safeWidth)}px`;
    modal.style.height = `${Math.round(safeHeight)}px`;
    const clamped = clampSchedulerModalPosition(left, top);
    modal.style.left = `${clamped.left}px`;
    modal.style.top = `${clamped.top}px`;
    modal.style.right = 'auto';
    modal.style.bottom = 'auto';
    modal.style.margin = '0';
    modal.style.transform = 'none';
  }

  function positionSchedulerModalCentered() {
    if (!modal) return;
    const viewportWidth = window.innerWidth || document.documentElement.clientWidth || 0;
    const viewportHeight = window.innerHeight || document.documentElement.clientHeight || 0;
    const modalWidth = modal.offsetWidth || modal.getBoundingClientRect().width || 920;
    const modalHeight = modal.offsetHeight || modal.getBoundingClientRect().height || 620;
    const centeredLeft = Math.round((viewportWidth - modalWidth) / 2);
    const centeredTop = Math.round((viewportHeight - modalHeight) / 2);
    applySchedulerModalRect(centeredLeft, centeredTop, modalWidth, modalHeight);
  }

  function onSchedulerModalPointerMove(event) {
    if (!modal) return;
    if (schedulerModalDragState) {
      const nextLeft = schedulerModalDragState.startLeft + (event.clientX - schedulerModalDragState.startClientX);
      const nextTop = schedulerModalDragState.startTop + (event.clientY - schedulerModalDragState.startClientY);
      applySchedulerModalRect(nextLeft, nextTop, schedulerModalDragState.width, schedulerModalDragState.height);
      schedulerModalDragState.moved = true;
      return;
    }
    if (schedulerModalResizeState) {
      const nextWidth = schedulerModalResizeState.startWidth + (event.clientX - schedulerModalResizeState.startClientX);
      const nextHeight = schedulerModalResizeState.startHeight + (event.clientY - schedulerModalResizeState.startClientY);
      applySchedulerModalRect(schedulerModalResizeState.left, schedulerModalResizeState.top, nextWidth, nextHeight);
      schedulerModalResizeState.moved = true;
    }
  }

  function endSchedulerModalPointerInteraction() {
    if (!modal) return;
    const moved = !!((schedulerModalDragState && schedulerModalDragState.moved) || (schedulerModalResizeState && schedulerModalResizeState.moved));
    schedulerModalDragState = null;
    schedulerModalResizeState = null;
    modal.classList.remove('is-dragging');
    modal.classList.remove('is-resizing');
    document.removeEventListener('pointermove', onSchedulerModalPointerMove);
    document.removeEventListener('pointerup', endSchedulerModalPointerInteraction);
    document.removeEventListener('pointercancel', endSchedulerModalPointerInteraction);
    if (moved) schedulerModalSuppressBackdropClickUntil = Date.now() + 250;
  }

  function onSchedulerModalTitlePointerDown(event) {
    if (!modal || !modalTitlebar || event.button !== 0) return;
    if (event.target.closest('button, a, input, select, textarea')) return;
    event.preventDefault();
    event.stopPropagation();
    const rect = modal.getBoundingClientRect();
    const inlineLeft = Number.parseFloat(modal.style.left);
    const inlineTop = Number.parseFloat(modal.style.top);
    schedulerModalDragState = {
      startClientX: event.clientX,
      startClientY: event.clientY,
      startLeft: Number.isFinite(inlineLeft) ? inlineLeft : rect.left,
      startTop: Number.isFinite(inlineTop) ? inlineTop : rect.top,
      width: modal.offsetWidth || rect.width,
      height: modal.offsetHeight || rect.height,
      moved: false
    };
    modal.classList.add('is-dragging');
    document.addEventListener('pointermove', onSchedulerModalPointerMove);
    document.addEventListener('pointerup', endSchedulerModalPointerInteraction);
    document.addEventListener('pointercancel', endSchedulerModalPointerInteraction);
  }

  function onSchedulerModalResizePointerDown(event) {
    if (!modal || event.button !== 0) return;
    event.preventDefault();
    event.stopPropagation();
    const rect = modal.getBoundingClientRect();
    schedulerModalResizeState = {
      startClientX: event.clientX,
      startClientY: event.clientY,
      startWidth: modal.offsetWidth || rect.width,
      startHeight: modal.offsetHeight || rect.height,
      left: Number.parseFloat(modal.style.left) || rect.left,
      top: Number.parseFloat(modal.style.top) || rect.top,
      moved: false
    };
    modal.classList.add('is-resizing');
    document.addEventListener('pointermove', onSchedulerModalPointerMove);
    document.addEventListener('pointerup', endSchedulerModalPointerInteraction);
    document.addEventListener('pointercancel', endSchedulerModalPointerInteraction);
  }

  function onSchedulerModalViewportResize() {
    if (!backdrop || !modal || !backdrop.classList.contains('active')) return;
    const rect = modal.getBoundingClientRect();
    applySchedulerModalRect(
      Number.parseFloat(modal.style.left) || rect.left,
      Number.parseFloat(modal.style.top) || rect.top,
      modal.offsetWidth || rect.width,
      modal.offsetHeight || rect.height
    );
  }

  function bindSchedulerModalDragging() {
    if (modalTitlebar && modalTitlebar.dataset.dragBound !== '1') {
      modalTitlebar.dataset.dragBound = '1';
      modalTitlebar.addEventListener('pointerdown', onSchedulerModalTitlePointerDown);
    }
    if (modalResizeHandle && modalResizeHandle.dataset.resizeBound !== '1') {
      modalResizeHandle.dataset.resizeBound = '1';
      modalResizeHandle.addEventListener('pointerdown', onSchedulerModalResizePointerDown);
    }
    if (backdrop && backdrop.dataset.viewportBound !== '1') {
      backdrop.dataset.viewportBound = '1';
      window.addEventListener('resize', onSchedulerModalViewportResize);
    }
  }

  // Recurring Event UI handling (date field transforms to weekday dropdown)
  function updateRecurringUI() {
    if (!form) return;
    const recurring = qs('#recurring_event', form);
    const dateEl = qs('#run_date', form);
    const dayEl = qs('#run_weekday', form);

    if (!recurring || !dateEl || !dayEl) return;

    const isRec = !!recurring.checked;
    // Use hidden attribute to avoid CSS specificity issues
    dateEl.hidden = isRec;
    dayEl.hidden = !isRec;

    // Also set display for safety
    dateEl.style.display = isRec ? 'none' : 'inline-block';
    dayEl.style.display = isRec ? 'inline-block' : 'none';
  }

  function formatSecondsToClock(totalSeconds) {
    const s = parseInt(totalSeconds || 0, 10);
    if (!s || s < 0) return '';
    const h = Math.floor(s / 3600);
    const m = Math.floor((s % 3600) / 60);
    const sec = s % 60;
    const mm = String(m).padStart(2, '0');
    const ss = String(sec).padStart(2, '0');
    if (h > 0) return `${h}:${mm}:${ss}`;
    return `${m}:${ss.padStart(2,'0')}`.replace(/^0:/, '0:').replace(/^(\d):(\d\d)$/, '$1:$2');
  }


  function wireRecurringListener() {
    if (!form) return;
    const recurring = qs('#recurring_event', form);
    if (!recurring) return;
    // Avoid double-binding
    if (recurring._sbBound) return;
    recurring._sbBound = true;
    recurring.addEventListener('change', updateRecurringUI);
  }


  async function confirmWithStopModal({ title, body, yesText, noText }) {
    const deleteWin = document.getElementById('scheduler-rule-delete-window');
    if (!deleteWin) return window.confirm(body || 'Are you sure?');

    const deleteTitlebar = document.getElementById('scheduler-rule-delete-titlebar');
    const deleteClose = document.getElementById('scheduler-rule-delete-close');
    const titleEl = document.getElementById('scheduler-rule-delete-title');
    const bodyEl = document.getElementById('scheduler-rule-delete-body');
    const yesBtn = document.getElementById('scheduler-rule-delete-yes');
    const noBtn = document.getElementById('scheduler-rule-delete-no');

    bindFloatingModal(deleteWin, deleteWin, deleteTitlebar, null);

    const prev = {
      title: titleEl ? titleEl.textContent : '',
      body: bodyEl ? bodyEl.textContent : '',
      yes: yesBtn ? yesBtn.textContent : '',
      no: noBtn ? noBtn.textContent : ''
    };

    if (titleEl && title) titleEl.textContent = title;
    if (bodyEl && body) bodyEl.textContent = body;
    if (yesBtn && yesText) yesBtn.textContent = yesText;
    if (noBtn && noText) noBtn.textContent = noText;

    return await new Promise((resolve) => {
      let done = false;

      const detach = () => {
        if (yesBtn) yesBtn.removeEventListener('click', onYes);
        if (noBtn) noBtn.removeEventListener('click', onNo);
        if (deleteClose) deleteClose.removeEventListener('click', onNo);
        document.removeEventListener('keydown', onKeyDown, true);
      };

      const finish = (result) => {
        if (done) return;
        done = true;
        detach();
        closeFloatingModal(deleteWin, deleteWin);
        if (titleEl) titleEl.textContent = prev.title;
        if (bodyEl) bodyEl.textContent = prev.body;
        if (yesBtn) yesBtn.textContent = prev.yes;
        if (noBtn) noBtn.textContent = prev.no;
        resolve(result);
      };

      const onYes = (event) => {
        if (event) event.preventDefault();
        finish(true);
      };
      const onNo = (event) => {
        if (event) event.preventDefault();
        finish(false);
      };
      const onKeyDown = (event) => {
        if (event.key !== 'Escape') return;
        if (deleteWin.style.display !== 'flex' && deleteWin.style.display !== 'block') return;
        event.preventDefault();
        event.stopPropagation();
        finish(false);
      };

      if (yesBtn) yesBtn.addEventListener('click', onYes);
      if (noBtn) noBtn.addEventListener('click', onNo);
      if (deleteClose) deleteClose.addEventListener('click', onNo);
      document.addEventListener('keydown', onKeyDown, true);

      openFloatingModal(deleteWin, deleteWin, yesBtn || deleteClose || noBtn);
    });
  }



  function openModal() {
    if (!backdrop) return;
    editingRuleId = null;
    if (modalTitle) modalTitle.textContent = 'New rule';
    if (errBox) { errBox.style.display = 'none'; errBox.textContent = ''; }
    form.reset();
    // default enabled unchecked
    const enabled = qs('input[name="is_enabled"]', form);
    if (enabled) enabled.checked = false;

    // Always start with an empty action list for new rules (form.reset() may not clear hidden fields reliably).
    setActionLines([]);

    wireRecurringListener();
    updateRecurringUI();
    renderActionItems();
    wireActionButtons();
    bindSchedulerModalDragging();
    backdrop.style.display = 'flex';
    backdrop.classList.add('active');
    backdrop.setAttribute('aria-hidden', 'false');
    document.body.classList.add('modal-open');
    positionSchedulerModalCentered();
  }

  function populateEditModalFromRule(ruleData) {
    if (!ruleData || !form) return;
    const ruleId = String(ruleData.id || '').trim();
    editingRuleId = ruleId || null;
    if (modalTitle) modalTitle.textContent = 'Edit rule';

    const name = String(ruleData.name || '');
    const runWhen = String(ruleData.run_when || '');
    const insertKind = String(ruleData.insert_kind || 'file').toLowerCase();
    const insertValue = String(ruleData.insert_value || '');
    const priority = String(ruleData.priority || 'next').toLowerCase();
    const enabled = !!Number(ruleData.is_enabled || 0);
    const autoStart = !!Number(ruleData.auto_start || 0);

    const nameEl = qs('input[name="name"]', form);
    if (nameEl) nameEl.value = name;

    const prEl = qs('select[name="priority"]', form);
    if (prEl) prEl.value = priority;

    const enEl = qs('input[name="is_enabled"]', form);
    if (enEl) enEl.checked = enabled;
    const autoStartEl = qs('input[name="auto_start"]', form);
    if (autoStartEl) autoStartEl.checked = autoStart;

    const lines = [];
    if (insertValue) {
      if (insertKind === 'stream') lines.push('URL:' + insertValue);
      else if (insertKind === 'dir') lines.push('DIR:' + insertValue);
      else lines.push('FILE:' + insertValue);
    }
    setActionLines(lines);

    const recurring = qs('#recurring_event', form);
    const dateEl = qs('#run_date', form);
    const dayEl = qs('#run_weekday', form);
    const timeEl = qs('#run_time', form);

    const parsed = parseRunWhen(runWhen);
    if (recurring) recurring.checked = parsed.isRecurring;
    updateRecurringUI();

    if (timeEl) timeEl.value = parsed.time || '';
    if (parsed.isRecurring) {
      if (dayEl && parsed.weekday) dayEl.value = parsed.weekday;
    } else {
      if (dateEl && parsed.date) dateEl.value = parsed.date;
    }
  }

  function openEditModalFromCard(card) {
    if (!card) return;
    openModal();
    populateEditModalFromRule({
      id: card.getAttribute('data-rule-id') || '',
      name: card.getAttribute('data-rule-name') || '',
      run_when: card.getAttribute('data-rule-run-when') || '',
      insert_kind: card.getAttribute('data-rule-insert-kind') || 'file',
      insert_value: card.getAttribute('data-rule-insert-value') || '',
      priority: card.getAttribute('data-rule-priority') || 'next',
      is_enabled: (card.getAttribute('data-rule-enabled') || '0') === '1' ? 1 : 0,
      auto_start: (card.getAttribute('data-rule-auto-start') || '0') === '1' ? 1 : 0
    });
  }

  function parseRunWhen(runWhen) {
    const s = (runWhen || '').trim();
    // Date case: YYYY-MM-DD HH:MM
    const mDate = s.match(/^(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2})$/);
    if (mDate) {
      return { isRecurring: false, date: mDate[1], time: mDate[2] };
    }

    // Everyday case: Everyday HH:MM
    const mEvery = s.match(/^Everyday\s+(\d{2}:\d{2})$/i);
    if (mEvery) {
      return { isRecurring: true, weekday: 'Everyday', time: mEvery[1] };
    }

    // Weekday case: Monday HH:MM
    const mDay = s.match(/^(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\s+(\d{2}:\d{2})$/i);
    if (mDay) {
      const day = (mDay[1] || '').toLowerCase();
      const dayNorm = day.charAt(0).toUpperCase() + day.slice(1);
      return { isRecurring: true, weekday: dayNorm, time: mDay[2] };
    }

    // Fallback: best-effort split
    const parts = s.split(/\s+/);
    if (parts.length >= 2 && /\d{2}:\d{2}/.test(parts[1])) {
      const p0 = parts[0];
      if (/^\d{4}-\d{2}-\d{2}$/.test(p0)) return { isRecurring: false, date: p0, time: parts[1] };
      const d = p0.toLowerCase();
      const dNorm = d.charAt(0).toUpperCase() + d.slice(1);
      return { isRecurring: true, weekday: dNorm, time: parts[1] };
    }
    return { isRecurring: false, date: '', time: '' };
  }

  function closeModal() {
    if (!backdrop) return;
    endSchedulerModalPointerInteraction();
    backdrop.classList.remove('active');
    backdrop.setAttribute('aria-hidden', 'true');
    backdrop.style.display = 'none';
    document.body.classList.remove('modal-open');
  }

  function computeInsertKind() {
    if (!form) return 'file';
    const lines = getActionLines();
    if (!lines.length) return 'file';
    const first = lines[0];
    if (/^URL:/i.test(first)) return 'stream';
    if (/^DIR:/i.test(first)) return 'dir';
    return 'file';
  }

  async function createRule() {
    // Build derived fields for backend compatibility
    const runWhenInput = qs('#run_when', form);
    const insertValueInput = qs('#insert_value', form);
    const insertKindInput = qs('#insert_kind', form);
    if (runWhenInput) runWhenInput.value = computeRunWhen();
    if (insertValueInput) insertValueInput.value = computeInsertValue();
    if (insertKindInput) insertKindInput.value = computeInsertKind();

    const fd = new FormData(form);
    const payload = {
      name: (fd.get('name') || '').toString().trim(),
      run_when: (fd.get('run_when') || '').toString().trim(),
      insert_kind: (fd.get('insert_kind') || '').toString(),
      insert_value: (fd.get('insert_value') || '').toString().trim(),
      priority: (fd.get('priority') || '').toString(),
      is_enabled: fd.get('is_enabled') ? 1 : 0,
      auto_start: fd.get('auto_start') ? 1 : 0
    };

    if (!payload.name || !payload.run_when || !payload.insert_kind || !payload.insert_value || !payload.priority) {
      throw new Error('Please fill in all fields.');
    }

    const res = await fetch('/api/scheduler/rules', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });

    const data = await res.json().catch(() => ({}));
    if (!res.ok || !data.ok) {
      throw new Error(data.error || 'Failed to create rule.');
    }
    closeModal();
    saveBtn.disabled = false;
    window.dispatchEvent(new CustomEvent('scheduler:rules-changed'));
  }

  async function updateRule(ruleId) {
    const runWhenInput = qs('#run_when', form);
    const insertValueInput = qs('#insert_value', form);
    const insertKindInput = qs('#insert_kind', form);
    if (runWhenInput) runWhenInput.value = computeRunWhen();
    if (insertValueInput) insertValueInput.value = computeInsertValue();
    if (insertKindInput) insertKindInput.value = computeInsertKind();

    const fd = new FormData(form);
    const payload = {
      name: (fd.get('name') || '').toString().trim(),
      run_when: (fd.get('run_when') || '').toString().trim(),
      insert_kind: (fd.get('insert_kind') || '').toString(),
      insert_value: (fd.get('insert_value') || '').toString().trim(),
      priority: (fd.get('priority') || '').toString(),
      is_enabled: fd.get('is_enabled') ? 1 : 0,
      auto_start: fd.get('auto_start') ? 1 : 0
    };

    if (!payload.name || !payload.run_when || !payload.insert_kind || !payload.insert_value || !payload.priority) {
      throw new Error('Please fill in all fields.');
    }

    const res = await fetch(`/api/scheduler/rules/${ruleId}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });

    const data = await res.json().catch(() => ({}));
    if (!res.ok || !data.ok) {
      throw new Error(data.error || 'Failed to update rule.');
    }
    closeModal();
    saveBtn.disabled = false;
    window.dispatchEvent(new CustomEvent('scheduler:rules-changed'));
  }

  async function deleteRule(ruleId) {
    const res = await fetch(`/api/scheduler/rules/${ruleId}`, { method: 'DELETE' });
    const data = await res.json().catch(() => ({}));
    if (!res.ok || !data.ok) {
      throw new Error(data.error || 'Failed to delete rule.');
    }
  }


  function computeRunWhen() {
    if (!form) return '';
    const recurring = qs('#recurring_event', form);
    const dateEl = qs('#run_date', form);
    const dayEl = qs('#run_weekday', form);
    const timeEl = qs('#run_time', form);

    const t = timeEl ? (timeEl.value || '').trim() : '';
    const isRec = recurring ? !!recurring.checked : false;

    if (!t) return '';

    if (isRec) {
      const day = dayEl ? (dayEl.value || '').trim() : '';
      if (!day) return '';
      if (day === 'Everyday') return `Everyday ${t}`;
      return `${day} ${t}`;
    } else {
      const d = dateEl ? (dateEl.value || '').trim() : '';
      if (!d) return '';
      return `${d} ${t}`;
    }
  }

  function getActionLines() {
    if (!form) return [];
    const action = qs('#action_script', form);
    const raw = action ? (action.value || '') : '';
    return raw.split(/\r?\n/).map(s => s.trim()).filter(Boolean);
  }

  function normalizeSingleActionLine(lines) {
    const arr = (lines || []).map(s => String(s || '').trim()).filter(Boolean);
    if (!arr.length) return [];
    // Keep only the first actionable line, discard the rest
    return [arr[0]];
  }

  

  function setActionLines(lines) {
    if (!form) return;
    const action = qs('#action_script', form);
    const normalized = normalizeSingleActionLine(lines);
    if (action) action.value = (normalized || []).join('\n');
    renderActionItems();
  }

  function renderActionItems() {
    if (!form) return;
    const box = qs('#scheduler-action-items', form);
    if (!box) return;
    const lines = getActionLines();
    box.innerHTML = '';
    if (!lines.length) {
      const empty = document.createElement('div');
      empty.className = 'sb-action-line';
      empty.style.opacity = '0.7';
      empty.textContent = '# file, directory or url to add';
      box.appendChild(empty);
      return;
    }
    // Single-line (only one item allowed)
    const row = document.createElement('div');
    row.className = 'sb-action-line';
    row.textContent = lines[0];
    box.appendChild(row);
  }

  function wireActionButtons() {
    if (!form) return;
    const btnFile = qs('#scheduler-add-file', form);
    const btnDir = qs('#scheduler-add-dir', form);
    const btnUrl = qs('#scheduler-add-url', form);
    if (btnFile && !btnFile._sbBound) {
      btnFile._sbBound = true;
      btnFile.addEventListener('click', function() { openFileModal(); });
    }
    if (btnDir && !btnDir._sbBound) {
      btnDir._sbBound = true;
      btnDir.addEventListener('click', function() { openDirectoryModal(); });
    }
    if (btnUrl && !btnUrl._sbBound) {
      btnUrl._sbBound = true;
      // v551: fix broken handler name (Add URL button)
      btnUrl.addEventListener('click', function() { showAddUrlPrompt(); });
    }
  }

  function computeInsertValue() {
    if (!form) return '';
    const action = qs('#action_script', form);
    const raw = action ? (action.value || '') : '';
    const lines = raw.split(/\r?\n/).map(s => s.trim()).filter(Boolean);
    if (!lines.length) return '';
    // Prefer first FILE:/DIR:/URL: line, otherwise first line
    const first = lines[0];
    return first.replace(/^FILE:/i,'').replace(/^DIR:/i,'').replace(/^URL:/i,'').trim();
  }

  async function toggleRule(ruleId, isEnabled) {
    const res = await fetch(`/api/scheduler/rules/${ruleId}/toggle`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ is_enabled: isEnabled ? 1 : 0 })
    });

    const data = await res.json().catch(() => ({}));
    if (!res.ok || !data.ok) {
      throw new Error(data.error || 'Failed to update rule.');
    }
  }

  if (newBtn) newBtn.addEventListener('click', openModal);
  window.openSchedulerRuleModal = openModal;
  window.openSchedulerRuleEditModal = function(ruleOrId) {
    if (!ruleOrId) return;
    if (typeof ruleOrId === 'object') {
      openModal();
      populateEditModalFromRule(ruleOrId);
      return;
    }
    const ruleId = String(ruleOrId || '').trim();
    if (!ruleId) return;
    const card = qs(`.scheduler-rule-card[data-rule-id="${CSS.escape(ruleId)}"]`);
    if (card) {
      openEditModalFromCard(card);
      return;
    }
  };

  (function autoOpenNewRuleFromQuery() {
    try {
      const params = new URLSearchParams(window.location.search || '');
      const shouldOpen = (params.get('new') || '').trim().toLowerCase();
      if (shouldOpen === '1' || shouldOpen === 'true' || shouldOpen === 'yes') {
        openModal();
      }
    } catch (err) {
      console.warn('Failed to evaluate scheduler auto-open query flag', err);
    }
  })();
  if (cancelBtn) cancelBtn.addEventListener('click', closeModal);
  if (closeBtn) closeBtn.addEventListener('click', closeModal);
  window.addEventListener('resize', () => {
    if (!backdrop || backdrop.style.display === 'none') return;
    positionSchedulerModalCentered();
  });
  if (backdrop) {
    backdrop.addEventListener('click', (e) => {
      if (e.target !== backdrop) return;
      if (schedulerModalDragState || schedulerModalResizeState || Date.now() < schedulerModalSuppressBackdropClickUntil) return;
      closeModal();
    });
  }

  document.addEventListener('keydown', (e) => {
    if (e.key !== 'Escape') return;
    if (!backdrop || backdrop.style.display !== 'flex' || !backdrop.classList.contains('active')) return;
    if (urlBackdrop && urlBackdrop.style.display === 'flex') return;
    if (fileBackdrop && fileBackdrop.style.display === 'flex') return;
    e.preventDefault();
    closeModal();
  });

  if (saveBtn) {
    saveBtn.addEventListener('click', async () => {
      try {
        saveBtn.disabled = true;
        if (editingRuleId) await updateRule(editingRuleId);
        else await createRule();
      } catch (e) {
        errBox.textContent = (e && e.message) ? e.message : String(e);
        errBox.style.display = 'block';
        saveBtn.disabled = false;
      }
    });
  }

  async function handleSchedulerRuleDeleteClick(buttonEl) {
    const card = buttonEl ? buttonEl.closest('.scheduler-rule-card') : null;
    const ruleId = card ? card.getAttribute('data-rule-id') : '';
    if (!ruleId) return;
    const name = card.getAttribute('data-rule-name') || 'this rule';
    const ok = await confirmWithStopModal({
      title: 'Delete rule',
      body: `Are you sure you want to delete "${name}"?`,
      yesText: 'Delete',
      noText: 'Cancel'
    });
    if (!ok) return;
    buttonEl.disabled = true;
    try {
      await deleteRule(ruleId);
      window.location.reload();
    } catch (err) {
      alert((err && err.message) ? err.message : String(err));
      buttonEl.disabled = false;
    }
  }

  qsa('.scheduler-rule-card').forEach((card) => {
    const ruleId = card.getAttribute('data-rule-id');
    const checkbox = qs('.scheduler-enabled', card);
    const editBtn = qs('.scheduler-edit-btn', card);
    const delBtn = qs('.scheduler-delete-btn', card);
    if (!ruleId || !checkbox) return;

    // Ensure checkbox state always reflects DB (avoid browser form-state restore after reload)
    const enabledAttr = card.getAttribute('data-rule-enabled');
    checkbox.checked = (enabledAttr === '1');


    checkbox.addEventListener('change', async () => {
      checkbox.disabled = true;
      try {
        await toggleRule(ruleId, checkbox.checked);
      } catch (e) {
        // revert
        checkbox.checked = !checkbox.checked;
        alert((e && e.message) ? e.message : String(e));
      } finally {
        checkbox.disabled = false;
      }
    });

    if (editBtn) {
      editBtn.addEventListener('click', function(e) {
        e.preventDefault();
        openEditModalFromCard(card);
      });
    }

    if (delBtn && !delBtn._sbDeleteBound) {
      delBtn._sbDeleteBound = true;
      delBtn.addEventListener('click', async function(e) {
        e.preventDefault();
        e.stopPropagation();
        await handleSchedulerRuleDeleteClick(delBtn);
      });
    }
  });

  document.addEventListener('click', async function(e) {
    const delBtn = e.target && e.target.closest ? e.target.closest('.scheduler-delete-btn') : null;
    if (!delBtn) return;
    e.preventDefault();
    e.stopPropagation();
    if (typeof e.stopImmediatePropagation === 'function') e.stopImmediatePropagation();
    if (delBtn._sbDeleteHandling) return;
    delBtn._sbDeleteHandling = true;
    try {
      await handleSchedulerRuleDeleteClick(delBtn);
    } finally {
      delBtn._sbDeleteHandling = false;
    }
  }, true);

  function resetUrlModalExternalState() {
    urlModalExternalResolver = null;
    urlModalExternalRejecter = null;
    urlModalExternalMode = null;
    const titleEl = qs('#scheduler-url-modal-title');
    if (titleEl && urlModalPreviousTitle) titleEl.textContent = urlModalPreviousTitle;
  }

  function openUrlModal(options) {
    if (!urlBackdrop || !urlInput || !urlModal) return;
    const opts = options && typeof options === 'object' ? options : {};
    const titleEl = qs('#scheduler-url-modal-title');
    urlModalPreviousTitle = titleEl ? (titleEl.textContent || 'Add URL') : 'Add URL';
    if (titleEl) titleEl.textContent = opts.title || 'Add URL';
    urlModalExternalMode = opts.mode || null;
    if (urlDurationInput) urlDurationInput.value = opts.defaultDuration != null ? String(opts.defaultDuration) : '60';
    if (urlInfiniteCb) urlInfiniteCb.checked = !!opts.defaultInfinite;
    if (urlInput) urlInput.value = opts.defaultUrl || '';
    bindFloatingModal(urlBackdrop, urlModal, urlTitlebar, urlResizeHandle);
    openFloatingModal(urlBackdrop, urlModal, urlInput);
  }

  window.openSchedulerUrlModal = function(options) {
    const opts = options && typeof options === 'object' ? options : {};
    return new Promise((resolve, reject) => {
      if (!urlBackdrop || !urlInput || !urlModal) {
        resolve(null);
        return;
      }
      urlModalExternalResolver = resolve;
      urlModalExternalRejecter = reject;
      openUrlModal(Object.assign({}, opts, { mode: opts.mode || 'external' }));
    });
  };

  function openFileModal() {
    if (!fileBackdrop || !fileCategoriesEl || !fileTracksEl || !fileModal) return;
    fileSelectedTrackPath = '';
    fileSelectedTrackLabel = '';
    fileActiveCategoryId = null;
    if (fileOkBtn) fileOkBtn.disabled = true;
    if (fileTracksTitleEl) fileTracksTitleEl.textContent = 'Tracks';
    fileCategoriesEl.innerHTML = 'Loading categories...';
    fileTracksEl.innerHTML = '<div class="category-track-item" style="opacity:0.75;">Select a category.</div>';
    bindFloatingModal(fileBackdrop, fileModal, fileTitlebar, fileResizeHandle);
    openFloatingModal(fileBackdrop, fileModal);
    fetchFileCategories();
  }

  function closeFileModal() {
    if (!fileBackdrop || !fileModal) return;
    closeFloatingModal(fileBackdrop, fileModal);
  }

  async function fetchFileCategories() {
    if (!fileCategoriesEl) return;
    try {
      const res = await fetch('/api/library/categories');
      const data = await res.json().catch(() => ({}));
      const cats = data.categories || [];
      fileCategoriesEl.innerHTML = '';
      if (!cats.length) {
        const empty = document.createElement('div');
        empty.className = 'file-item';
        empty.textContent = 'No categories yet.';
        fileCategoriesEl.appendChild(empty);
        return;
      }
      cats.forEach((cat) => {
        const row = document.createElement('div');
        row.className = 'file-item';
        row.dataset.id = String(cat.id);
        row.textContent = cat.name;
        row.addEventListener('click', () => {
          setActiveFileCategory(cat.id, cat.name);
        });
        fileCategoriesEl.appendChild(row);
      });
    } catch (e) {
      fileCategoriesEl.innerHTML = '<div class="file-item">Error loading categories.</div>';
    }
  }

  async function setActiveFileCategory(categoryId, name) {
    fileActiveCategoryId = categoryId;
    fileSelectedTrackPath = '';
    fileSelectedTrackLabel = '';
    if (fileOkBtn) fileOkBtn.disabled = true;
    if (fileTracksTitleEl) fileTracksTitleEl.textContent = 'Tracks in: ' + name;
    // highlight selected category
    if (fileCategoriesEl) {
      fileCategoriesEl.querySelectorAll('.file-item').forEach((el) => {
        if (String(el.dataset.id) === String(categoryId)) el.classList.add('selected');
        else el.classList.remove('selected');
      });
    }
    await fetchFileTracks(categoryId);
  }

  async function fetchFileTracks(categoryId) {
    if (!fileTracksEl) return;
    fileTracksEl.innerHTML = 'Loading tracks...';
    try {
      const res = await fetch(`/api/library/category/${categoryId}/tracks`);
      const data = await res.json().catch(() => ({}));
      const tracks = data.tracks || [];
      fileTracksEl.innerHTML = '';
      if (!tracks.length) {
        const empty = document.createElement('div');
        empty.className = 'category-track-item';
        empty.textContent = 'No tracks in this category.';
        fileTracksEl.appendChild(empty);
        return;
      }
      tracks.forEach((t) => {
        const row = document.createElement('div');
        row.className = 'category-track-item';
        row.dataset.path = t.path || '';
        const dur = (t && t.cue_duration_seconds != null) ? formatSecondsToClock(t.cue_duration_seconds) : '';
        row.textContent = (t.filename || t.path || 'track') + (dur ? ` [${dur}]` : '');
        row.addEventListener('click', () => {
          selectFileTrack(row, t);
        });
        row.addEventListener('dblclick', () => {
          selectFileTrack(row, t);
          addSelectedFileTrack();
        });
        fileTracksEl.appendChild(row);
      });
    } catch (e) {
      fileTracksEl.innerHTML = '<div class="category-track-item">Error loading tracks.</div>';
    }
  }

  function selectFileTrack(rowEl, track) {
    fileTracksEl.querySelectorAll('.category-track-item').forEach((el) => el.classList.remove('selected'));
    rowEl.classList.add('selected');
    fileSelectedTrackPath = (track && track.path) ? String(track.path) : '';
    fileSelectedTrackLabel = (track && track.filename) ? String(track.filename) : fileSelectedTrackPath;
    if (fileOkBtn) fileOkBtn.disabled = !fileSelectedTrackPath;
  }

  function addSelectedFileTrack() {
    if (!fileSelectedTrackPath) return;
    const lines = [`FILE:${fileSelectedTrackPath}`];
    setActionLines(lines);
    closeFileModal();
  }

  async function openDirectoryModal() {
    if (typeof window.openPlaylistBrowserWindow !== 'function') return;
    const modes = window.PLAYLIST_BROWSER_MODES || {};
    const directoryMode = modes.DIRECTORIES || 'directories';
    await window.openPlaylistBrowserWindow('', directoryMode, 'scheduler', {
      onConfirm: async ({ directoryPaths, selectedEntries }) => {
        const selectedEntry = Array.isArray(selectedEntries) ? selectedEntries[0] : null;
        const selectedPath = String(
          (selectedEntry && (selectedEntry.full_path || selectedEntry.path || selectedEntry.relative_path))
          || (Array.isArray(directoryPaths) ? directoryPaths[0] : '')
          || ''
        ).trim();
        if (!selectedPath) return;
        setActionLines([`DIR:${selectedPath}`]);
      }
    });
  }


  if (fileCancelBtn) {
    fileCancelBtn.addEventListener('click', function(e) {
      e.preventDefault();
      closeFileModal();
    });
  }
  if (fileCloseBtn) {
    fileCloseBtn.addEventListener('click', function(e) {
      e.preventDefault();
      closeFileModal();
    });
  }
  if (fileOkBtn) {
    fileOkBtn.addEventListener('click', function(e) {
      e.preventDefault();
      addSelectedFileTrack();
    });
  }

  function closeUrlModal() {
    if (!urlBackdrop || !urlModal) return;
    if (urlModalExternalResolver) {
      const resolver = urlModalExternalResolver;
      resetUrlModalExternalState();
      resolver(null);
    }
    closeFloatingModal(urlBackdrop, urlModal);
  }

  function showAddUrlPrompt() {
    // Open a dedicated modal so the page layout does not shift.
    openUrlModal({ title: 'Add URL', defaultDuration: 60, defaultInfinite: false });
  }

  function readUrlModalValues() {
    if (!urlInput) return null;
    const url = (urlInput.value || '').trim();
    if (!url) return null;

    let dur = 60;
    const inf = !!(urlInfiniteCb && urlInfiniteCb.checked);
    if (inf) {
      dur = -1;
    } else if (urlDurationInput) {
      const v = (urlDurationInput.value || '').trim();
      const n = parseInt(v, 10);
      if (!isNaN(n) && n > 0) dur = n;
    }
    return { url, duration: dur, infinite: inf };
  }

  function commitUrlFromModal() {
    const values = readUrlModalValues();
    if (!values) return;

    if (urlModalExternalResolver) {
      const resolver = urlModalExternalResolver;
      resetUrlModalExternalState();
      closeFloatingModal(urlBackdrop, urlModal);
      resolver(values);
      return;
    }

    const lines = getActionLines();
    lines.unshift('URL:' + String(values.duration) + ':' + values.url);
    setActionLines(lines);
    closeUrlModal();
  }

  // Wire URL modal events once
  (function wireUrlModal() {
    if (!urlBackdrop || urlBackdrop._sbBound) return;
    urlBackdrop._sbBound = true;

    if (urlCancelBtn) {
      urlCancelBtn.addEventListener('click', function(e){
        e.preventDefault();
        closeUrlModal();
      });
    }
    if (urlCloseBtn) {
      urlCloseBtn.addEventListener('click', function(e){
        e.preventDefault();
        closeUrlModal();
      });
    }
    if (urlOkBtn) {
      urlOkBtn.addEventListener('click', function(e){
        e.preventDefault();
        commitUrlFromModal();
      });
    }
    if (urlDurationInput) {
      // numeric-only
      urlDurationInput.addEventListener('keydown', function(e){
        const k = e.key;
        if (k === 'Enter') {
          e.preventDefault();
          commitUrlFromModal();
          return;
        }
        if (k === 'Escape') {
          e.preventDefault();
          closeUrlModal();
          return;
        }
        // Allow navigation/edit keys
        if (k === 'Backspace' || k === 'Delete' || k === 'ArrowLeft' || k === 'ArrowRight' || k === 'Home' || k === 'End' || k === 'Tab') return;
        // Allow digits only
        if (!/^[0-9]$/.test(k)) {
          e.preventDefault();
        }
      });
      urlDurationInput.addEventListener('input', function(){
        // strip any non-digits (e.g. paste)
        urlDurationInput.value = (urlDurationInput.value || '').replace(/[^0-9]/g, '');
      });
    }
    if (urlInput) {
      urlInput.addEventListener('keydown', function(e){
        if (e.key === 'Enter') {
          e.preventDefault();
          commitUrlFromModal();
        } else if (e.key === 'Escape') {
          e.preventDefault();
          closeUrlModal();
        }
      });
    }
    // Global Esc closes if open
    document.addEventListener('keydown', function(e){
      if (e.key !== 'Escape') return;
      if (urlBackdrop && urlBackdrop.style.display === 'flex') {
        e.preventDefault();
        e.stopPropagation();
        if (typeof e.stopImmediatePropagation === 'function') e.stopImmediatePropagation();
        closeUrlModal();
      }
    }, true);
    document.addEventListener('keydown', function(e){
      if (e.key !== 'Escape') return;
      if (fileBackdrop && fileBackdrop.style.display === 'flex') {
        e.preventDefault();
        e.stopPropagation();
        if (typeof e.stopImmediatePropagation === 'function') e.stopImmediatePropagation();
        closeFileModal();
      }
    }, true);
  })();


  // ---------------------------
  // Next run countdown rendering
  // ---------------------------
  function parseLocalDateTime(s) {
    // Expect "YYYY-MM-DD HH:MM" (same format as displayed in UI / stored in DB)
    const m = (s || '').trim().match(/^(\d{4})-(\d{2})-(\d{2})\s+(\d{2}):(\d{2})$/);
    if (!m) return null;
    const y = Number(m[1]), mo = Number(m[2]) - 1, d = Number(m[3]);
    const hh = Number(m[4]), mm = Number(m[5]);
    const dt = new Date(y, mo, d, hh, mm, 0, 0);
    return isNaN(dt.getTime()) ? null : dt;
  }

  function nextDateFromWeekdayTime(weekdayName, hhmm) {
    const w = (weekdayName || '').trim().toLowerCase();
    const mTime = (hhmm || '').trim().match(/^(\d{2}):(\d{2})$/);
    if (!w || !mTime) return null;

    const map = {
      sunday: 0, monday: 1, tuesday: 2, wednesday: 3,
      thursday: 4, friday: 5, saturday: 6
    };
    const targetDow = map[w];
    if (targetDow === undefined) return null;

    const hh = Number(mTime[1]), mm = Number(mTime[2]);
    const now = new Date();
    const base = new Date(now.getFullYear(), now.getMonth(), now.getDate(), hh, mm, 0, 0);

    const todayDow = base.getDay();
    let delta = targetDow - todayDow;
    if (delta < 0) delta += 7;

    let candidate = new Date(base.getTime() + delta * 24 * 60 * 60 * 1000);

    // If it's today but already passed, push to next week.
    if (delta === 0 && candidate.getTime() <= now.getTime()) {
      candidate = new Date(candidate.getTime() + 7 * 24 * 60 * 60 * 1000);
    }
    return candidate;
  }

  function computeNextRunDateFromRunWhen(runWhen) {
    const s = (runWhen || '').trim();
    if (!s) return null;

    // Date case: YYYY-MM-DD HH:MM
    const dt = parseLocalDateTime(s);
    if (dt) {
      const now = new Date();
      if (dt.getTime() <= now.getTime()) return null;
      return dt;
    }

    // Everyday case: Everyday HH:MM
    const mEvery = s.match(/^Everyday\s+(\d{2}:\d{2})$/i);
    if (mEvery) {
      const now = new Date();
      const parts = mEvery[1].split(':');
      const hh = parseInt(parts[0], 10);
      const mm = parseInt(parts[1], 10);
      const candidate = new Date(now.getFullYear(), now.getMonth(), now.getDate(), hh, mm, 0, 0);
      if (candidate.getTime() > now.getTime()) return candidate;
      return new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1, hh, mm, 0, 0);
    }

    // Weekday case: Monday HH:MM
    const mDay = s.match(/^(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\s+(\d{2}:\d{2})$/i);
    if (mDay) {
      return nextDateFromWeekdayTime(mDay[1], mDay[2]);
    }

    return null;
  }

  function formatCountdown(ms) {
    if (ms <= 0) return 'Now';
    let total = Math.floor(ms / 1000);
    const days = Math.floor(total / 86400); total -= days * 86400;
    const hours = Math.floor(total / 3600); total -= hours * 3600;
    const minutes = Math.floor(total / 60); total -= minutes * 60;
    const seconds = total;

    function pad2(n) { return String(n).padStart(2, '0'); }

    if (days > 0) return `${days}d ${pad2(hours)}:${pad2(minutes)}:${pad2(seconds)}`;
    return `${pad2(hours)}:${pad2(minutes)}:${pad2(seconds)}`;
  }

  function startNextRunCountdowns() {
    const els = qsa('.scheduler-next-countdown');
    if (!els.length) return;

    function tick() {
      const now = new Date();
      els.forEach((el) => {
        const card = el.closest('.scheduler-rule-card');
        const enabledBox = card ? qs('.scheduler-enabled', card) : null;
        if (enabledBox && !enabledBox.checked) {
          el.textContent = '-';
          el.removeAttribute('title');
          return;
        }

        const runWhen = el.getAttribute('data-run-when') || '';
        const nextDt = computeNextRunDateFromRunWhen(runWhen);

        if (!nextDt) {
          el.textContent = '-';
          el.removeAttribute('title');
          return;
        }

        const diff = nextDt.getTime() - now.getTime();
        el.textContent = formatCountdown(diff);
        el.setAttribute('title', nextDt.toLocaleString());
      });
    }

    tick();
    // Avoid multiple timers if the script is reloaded dynamically.
    if (window._sbSchedulerNextTimer) clearInterval(window._sbSchedulerNextTimer);
    window._sbSchedulerNextTimer = setInterval(tick, 1000);
  }


  // File picker modal button wiring (Add File)
  if (fileOkBtn && !fileOkBtn._sbBound) {
    fileOkBtn._sbBound = true;
    fileOkBtn.addEventListener('click', function(e) {
      e.preventDefault();
      addSelectedFileTrack();
    });
  }
  if (fileCancelBtn && !fileCancelBtn._sbBound) {
    fileCancelBtn._sbBound = true;
    fileCancelBtn.addEventListener('click', function(e) {
      e.preventDefault();
      closeFileModal();
    });
  }
  if (fileBackdrop && !fileBackdrop._sbBound) {
    fileBackdrop._sbBound = true;
    fileBackdrop.addEventListener('click', function(e) {
      if (e.target === fileBackdrop) closeFileModal();
    });
  }

  // Start countdowns immediately.
  startNextRunCountdowns();


})(); 


/* Marquee helper: if insert text overflows, animate it back-and-forth like a scoreboard */

/* Marquee helper: if insert text overflows, animate it back-and-forth like a scoreboard.
   Ensure inner has horizontal padding so the text can scroll *under* the fade masks,
   preventing the first/last characters from being permanently obscured. */
function setupMarquee(el) {
    if (!el) return;
    const FADE_PX = 30; // must match CSS fade width & inner padding
    // Avoid re-wrapping
    if (!el.querySelector('.marquee-inner')) {
        const inner = document.createElement('span');
        inner.className = 'marquee-inner';
        while (el.firstChild) {
            inner.appendChild(el.firstChild);
        }
        el.appendChild(inner);
        const leftFade = document.createElement('div');
        leftFade.className = 'marquee-fade left';
        const rightFade = document.createElement('div');
        rightFade.className = 'marquee-fade right';
        el.appendChild(leftFade);
        el.appendChild(rightFade);
    }
    const inner = el.querySelector('.marquee-inner');
    inner.style.animation = '';
    inner.style.transform = '';
    // ensure inner has matching padding so text can move under fades
    inner.style.paddingLeft = FADE_PX + 'px';
    inner.style.paddingRight = FADE_PX + 'px';
    // measure after layout
    requestAnimationFrame(() => {
        const outerW = el.clientWidth;
        const innerW = inner.scrollWidth;
        // If inner content (including padding) is wider than outer, animate.
        if (innerW > outerW + 2) {
            // distance to translate so the visible *text* moves fully across including fade areas.
            // Because we added FADE_PX padding on both sides, ensure we move by that extra amount too.
            const distance = innerW - outerW; // includes padding
            const duration = Math.max(6, Math.round(distance / 30));
            const name = 'marquee_' + Math.random().toString(36).substr(2,6);
            const keyframes = `@keyframes ${name} { 0% {  } 50% {  } 100% {  } }`;
            const styleTag = document.createElement('style');
            styleTag.dataset.marquee = name;
            styleTag.appendChild(document.createTextNode(keyframes));
            document.head.appendChild(styleTag);
            inner.style.animation = `${name} ${duration}s linear infinite`;
            // ensure fades visible
            const fades = el.querySelectorAll('.marquee-fade');
            fades.forEach(f => { f.style.display = ''; });
        } else {
            const fades = el.querySelectorAll('.marquee-fade');
            fades.forEach(f => f.style.display = 'none');
            inner.style.transform = '';
        }
    });
}


// Initialize marquees for all current and future insert value elements
function initAllMarquees() {
    const els = document.querySelectorAll('.scheduler-insert-value');
    els.forEach(setupMarquee);
}

// Re-run on window resize (debounced)
let _marqResizeTimer = null;
window.addEventListener('resize', function(){
    clearTimeout(_marqResizeTimer);
    _marqResizeTimer = setTimeout(initAllMarquees, 200);
});

// If the app has an initialization or rendering hook for rules, call initAllMarquees after rendering rules.
// We'll also call it once on DOMContentLoaded to cover initial render.
document.addEventListener('DOMContentLoaded', initAllMarquees);



/*
 * Queue insertion API helpers
 *
 * Usage:
 *   // insertObj should contain at least {id, title, artist, duration}
 *   addInsertToQueue(insertObj, 'next'|'immediate'|'end')
 *
 * Behavior:
 *  - 'next'     : send request to add to queue top (next play)
 *  - 'immediate': add to queue top AND trigger player 'next' to start playback immediately
 *  - 'end'      : append to queue end
 *
 * The code first tries to call server endpoints:
 *   POST /api/queue/add    payload: { item, priority }
 *   POST /api/player/next  no body
 *
 * If those endpoints aren't available, it falls back to client-side queue manipulation
 * if a global `window.clientQueue` is present, or it creates one.
 */

function _safeFetch(url, opts) {
    return fetch(url, opts).then(resp => {
        if (!resp.ok) throw new Error('HTTP ' + resp.status);
        return resp.json ? resp.json().catch(()=>null) : null;
    });
}

function _updateLocalQueueUI(item, where) {
    // where: 'top' or 'end'
    try {
        const q = document.getElementById('queue-list');
        if (!q) return;
        const li = document.createElement('li');
        li.className = 'queue-item';
        li.dataset.itemId = item.id || '';
        li.textContent = (item.title || 'Unknown') + (item.artist ? ' — ' + item.artist : '');
        if (where === 'top') {
            q.insertBefore(li, q.firstChild);
        } else {
            q.appendChild(li);
        }
    } catch (e) {
        console.warn('updateLocalQueueUI failed', e);
    }
}

function _ensureClientQueue() {
    if (!window.clientQueue) {
        window.clientQueue = [];
    }
    return window.clientQueue;
}

/**
 * Add an insert object to the queue with given priority.
 * insertObj: { id, title, artist, duration, ... }
 * priority: 'next' | 'immediate' | 'end'
 */
function addInsertToQueue(insertObj, priority) {
    if (!insertObj || !priority) {
        console.error('addInsertToQueue missing args', insertObj, priority);
        return Promise.reject(new Error('missing args'));
    }
    const payload = { item: insertObj, priority: priority };
    const headers = { 'Content-Type': 'application/json' };

    // Try server API first
    return _safeFetch('/api/queue/add', {
        method: 'POST',
        headers: headers,
        body: JSON.stringify(payload),
    }).then((data) => {
        console.log('Server queue/add success', data);
        // Update UI optimistically
        if (priority === 'end') {
            _updateLocalQueueUI(insertObj, 'end');
        } else {
            _updateLocalQueueUI(insertObj, 'top');
        }
        if (priority === 'immediate') {
            // trigger player next
            return _safeFetch('/api/player/next', { method: 'POST' }).then(() => {
                console.log('player/next triggered');
                return { status: 'ok', server: true };
            }).catch((err) => {
                console.warn('player/next failed', err);
                return { status: 'ok', server: true, playerNextFailed: true };
            });
        }
        return { status: 'ok', server: true };
    }).catch((err) => {
        console.warn('Server endpoints unavailable or failed:', err);
        // Fallback: manipulate clientQueue
        const q = _ensureClientQueue();
        if (priority === 'end') {
            q.push(insertObj);
            _updateLocalQueueUI(insertObj, 'end');
        } else {
            q.unshift(insertObj);
            _updateLocalQueueUI(insertObj, 'top');
            if (priority === 'immediate') {
                // If there's a global player with next() call it, else simulate
                try {
                    if (window.player && typeof window.player.next === 'function') {
                        window.player.next();
                        console.log('player.next() called (client fallback)');
                    } else if (window.player && typeof window.player.playItem === 'function') {
                        // try to start the newly added item
                        window.player.playItem(insertObj);
                        console.log('player.playItem() called (client fallback)');
                    } else {
                        console.log('No player API found; immediate item added to clientQueue but did not trigger playback.');
                    }
                } catch (e) {
                    console.warn('Error triggering local player', e);
                }
            }
        }
        return Promise.resolve({ status: 'ok', server: false, fallback: true });
    });
}

// Expose on window for UI to call
window.addInsertToQueue = addInsertToQueue;

// Optional convenience: wire buttons with data attributes
document.addEventListener('click', function(e){
    const btn = e.target.closest && e.target.closest('[data-insert-action]');
    if (!btn) return;
    const action = btn.getAttribute('data-insert-action'); // next|immediate|end
    if (!action) return;
    // read data attributes for item
    const id = btn.getAttribute('data-item-id') || '';
    const title = btn.getAttribute('data-item-title') || btn.getAttribute('data-title') || '';
    const artist = btn.getAttribute('data-item-artist') || '';
    const duration = btn.getAttribute('data-item-duration') || '';
    const item = { id: id, title: title, artist: artist, duration: duration };
    addInsertToQueue(item, action).then(res => {
        // show quick visual feedback if success
        try {
            btn.classList.add('inserted-ok');
            setTimeout(()=>btn.classList.remove('inserted-ok'), 900);
        } catch(e){}
    }).catch(()=>{});
});
