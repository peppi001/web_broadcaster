
document.addEventListener('click', function(e){
  const btn = e.target.closest('button');
  if (!btn) return;
  if (btn.textContent && btn.textContent.toLowerCase().includes('add directory')) {
    const modal = btn.closest('.studio-floating-window');
    if (modal) {
      let el = modal.querySelector('.add-dir-loading');
      if (!el) {
        el = document.createElement('div');
        el.className = 'add-dir-loading';
        el.style.marginTop = '10px';
        el.innerHTML = '<span class="spinner"></span> Adding files... please wait';
        modal.appendChild(el);
      }
      el.style.display = 'block';
    }
    btn.disabled = true;
  }
});

(function(){
  'use strict';

  const shell = document.querySelector('.studio-shell');
  if (!shell) return;

  const els = {
    statusPill: document.getElementById('studio-status-pill'),
    statusMeta: document.getElementById('studio-meta'),
    deckTitle: document.getElementById('deck-title'),
    deckArtist: document.getElementById('deck-artist'),
    deckYear: document.getElementById('deck-year'),
    deckProgressBar: document.getElementById('deck-progress'),
    deckProgressFill: document.getElementById('deck-progress-fill'),
    deckProgressTooltip: document.getElementById('deck-progress-tooltip'),
    deckTime: document.getElementById('deck-time'),
    deckOnAirButton: document.getElementById('deck-onair-button'),
    nextControl: document.querySelector('[data-control="next"]'),
    queueList: document.getElementById('queue-list'),
    queueSummary: document.getElementById('queue-summary'),
    playlistCategories: document.getElementById('playlist-categories'),
    playlistRootToggle: document.getElementById('playlist-root-toggle'),
    playlistContextMenu: document.getElementById('playlist-context-menu'),
    tracksAddMenu: document.getElementById('tracks-add-menu'),
    scriptsAddBtn: document.getElementById('scripts-add-btn'),
    scriptsRemoveBtn: document.getElementById('scripts-remove-btn'),
    scriptsConfigBtn: document.getElementById('scripts-config-btn'),
    scriptsStartBtn: document.getElementById('scripts-start-btn'),
    scriptsStopBtn: document.getElementById('scripts-stop-btn'),
    scriptsAddMenu: document.getElementById('scripts-add-menu'),
    playlistPromptBackdrop: document.getElementById('playlist-prompt-backdrop'),
    playlistPromptTitle: document.getElementById('playlist-prompt-title'),
    playlistPromptLabel: document.getElementById('playlist-prompt-label'),
    playlistPromptInput: document.getElementById('playlist-prompt-input'),
    playlistPromptOk: document.getElementById('playlist-prompt-ok'),
    playlistPromptCancel: document.getElementById('playlist-prompt-cancel'),
    playlistPromptClose: document.getElementById('playlist-prompt-close'),
    playlistDeleteWindow: document.getElementById('playlist-delete-window'),
    playlistDeleteTitle: document.getElementById('playlist-delete-title'),
    playlistDeleteBody: document.getElementById('playlist-delete-body'),
    playlistDeleteYes: document.getElementById('playlist-delete-yes'),
    playlistDeleteNo: document.getElementById('playlist-delete-no'),
    playlistDeleteClose: document.getElementById('playlist-delete-close'),
    playlistAddFilesWindow: document.getElementById('playlist-add-files-window'),
    playlistAddFilesTitle: document.getElementById('playlist-add-files-title'),
    playlistBrowserBreadcrumb: document.getElementById('playlist-browser-breadcrumb'),
    playlistBrowserRows: document.getElementById('playlist-browser-rows'),
    playlistBrowserSummary: document.getElementById('playlist-browser-summary'),
    playlistAddFilesOk: document.getElementById('playlist-add-files-ok'),
    playlistAddFilesCancel: document.getElementById('playlist-add-files-cancel'),
    playlistAddFilesClose: document.getElementById('playlist-add-files-close'),
    scriptsAddScriptWindow: document.getElementById('scripts-add-script-window'),
    scriptsAddScriptTitle: document.getElementById('scripts-add-script-title'),
    scriptsBrowserBreadcrumb: document.getElementById('scripts-browser-breadcrumb'),
    scriptsBrowserRows: document.getElementById('scripts-browser-rows'),
    scriptsBrowserSummary: document.getElementById('scripts-browser-summary'),
    scriptsAddScriptOk: document.getElementById('scripts-add-script-ok'),
    scriptsAddScriptCancel: document.getElementById('scripts-add-script-cancel'),
    scriptsAddScriptClose: document.getElementById('scripts-add-script-close'),
    scriptsAutoStart: document.getElementById('scripts-auto-start'),
    scriptsConfigWindow: document.getElementById('scripts-config-window'),
    scriptsConfigTitle: document.getElementById('scripts-config-title'),
    scriptsConfigFilePath: document.getElementById('scripts-config-file-path'),
    scriptsConfigAutoStart: document.getElementById('scripts-config-auto-start'),
    scriptsConfigEditor: document.getElementById('scripts-config-editor'),
    scriptsConfigOk: document.getElementById('scripts-config-ok'),
    scriptsConfigCancel: document.getElementById('scripts-config-cancel'),
    scriptsConfigClose: document.getElementById('scripts-config-close'),
    scriptsDeleteBackdrop: document.getElementById('scripts-delete-backdrop'),
    categoryTracksDeleteWindow: document.getElementById('category-tracks-delete-window'),
    categoryTracksDeleteTitle: document.getElementById('category-tracks-delete-title'),
    categoryTracksDeleteBody: document.getElementById('category-tracks-delete-body'),
    categoryTracksDeleteYes: document.getElementById('category-tracks-delete-yes'),
    categoryTracksDeleteNo: document.getElementById('category-tracks-delete-no'),
    categoryTracksDeleteClose: document.getElementById('category-tracks-delete-close'),
    scriptsDeleteBody: document.getElementById('scripts-delete-body'),
    scriptsDeleteNo: document.getElementById('scripts-delete-no'),
    scriptsDeleteYes: document.getElementById('scripts-delete-yes'),
    scriptsDeleteClose: document.getElementById('scripts-delete-close'),
    schedulerRuleDeleteWindow: document.getElementById('scheduler-rule-delete-window'),
    schedulerRuleDeleteTitle: document.getElementById('scheduler-rule-delete-title'),
    schedulerRuleDeleteBody: document.getElementById('scheduler-rule-delete-body'),
    schedulerRuleDeleteYes: document.getElementById('scheduler-rule-delete-yes'),
    schedulerRuleDeleteNo: document.getElementById('scheduler-rule-delete-no'),
    schedulerRuleDeleteClose: document.getElementById('scheduler-rule-delete-close'),
    scriptsStartBlockedBackdrop: document.getElementById('scripts-start-blocked-backdrop'),
    scriptsStartBlockedOk: document.getElementById('scripts-start-blocked-ok'),
    playlistAddUrlWindow: document.getElementById('playlist-add-url-window'),
    playlistAddUrlTitle: document.getElementById('playlist-add-url-title'),
    playlistAddUrlInput: document.getElementById('playlist-add-url-input'),
    playlistAddUrlOk: document.getElementById('playlist-add-url-ok'),
    playlistAddUrlCancel: document.getElementById('playlist-add-url-cancel'),
    playlistAddUrlClose: document.getElementById('playlist-add-url-close'),
    historyList: document.getElementById('history-list'),
    historySummary: document.getElementById('history-summary'),
    tracksList: document.getElementById('tracks-list'),
    tracksCategoryName: document.getElementById('tracks-category-name'),
    tracksSummary: document.getElementById('tracks-summary'),
    playlistSplitter: document.getElementById('playlist-splitter'),
    playlistTreeSection: document.getElementById('playlist-tree-section'),
    playlistTracksSection: document.getElementById('playlist-tracks-section'),
    layoutContextMenu: document.getElementById('layout-context-menu'),
    encodersList: document.getElementById('encoders-list'),
    encodersAddBtn: document.getElementById('encoders-add-btn'),
    encodersRemoveBtn: document.getElementById('encoders-remove-btn'),
    encodersStartBtn: document.getElementById('encoders-start-btn'),
    encodersStopBtn: document.getElementById('encoders-stop-btn'),
    encodersConfigBtn: document.getElementById('encoders-config-btn'),
    encoderConfigBackdrop: document.getElementById('encoder-config-backdrop'),
    encoderConfigHost: document.getElementById('encoder-config-host'),
    encoderConfigPort: document.getElementById('encoder-config-port'),
    encoderConfigPassword: document.getElementById('encoder-config-password'),
    encoderConfigMount: document.getElementById('encoder-config-mount'),
    encoderConfigCodec: document.getElementById('encoder-config-codec'),
    encoderConfigBitrate: document.getElementById('encoder-config-bitrate'),
    encoderConfigName: document.getElementById('encoder-config-name'),
    encoderConfigDescription: document.getElementById('encoder-config-description'),
    encoderConfigGenre: document.getElementById('encoder-config-genre'),
    encoderConfigWebsite: document.getElementById('encoder-config-website'),
    encoderConfigAutostart: document.getElementById('encoder-config-autostart'),
    encoderConfigAddYearToIcecastMeta: document.getElementById('encoder-config-add-year-to-icecast-meta'),
    encoderConfigOk: document.getElementById('encoder-config-ok'),
    encoderConfigCancel: document.getElementById('encoder-config-cancel'),
    encoderConfigClose: document.getElementById('encoder-config-close'),
    studioStopConfirmBackdrop: document.getElementById('studio-stop-confirm-backdrop'),
    studioStopConfirmYes: document.getElementById('studio-stop-confirm-yes'),
    studioStopConfirmNo: document.getElementById('studio-stop-confirm-no'),
    studioStopConfirmClose: document.getElementById('studio-stop-confirm-close'),
    queueDeleteWindow: document.getElementById('queue-delete-window'),
    queueDeleteTitle: document.getElementById('queue-delete-title'),
    queueDeleteBody: document.getElementById('queue-delete-body'),
    queueDeleteYes: document.getElementById('queue-delete-yes'),
    queueDeleteNo: document.getElementById('queue-delete-no'),
    queueDeleteClose: document.getElementById('queue-delete-close'),
    encodersDeleteWindow: document.getElementById('encoders-delete-window'),
    encodersDeleteTitle: document.getElementById('encoders-delete-title'),
    encodersDeleteBody: document.getElementById('encoders-delete-body'),
    encodersDeleteYes: document.getElementById('encoders-delete-yes'),
    encodersDeleteNo: document.getElementById('encoders-delete-no'),
    encodersDeleteClose: document.getElementById('encoders-delete-close'),
    palList: document.getElementById('pal-list'),
    studioSearchWindow: document.getElementById('studio-search-window'),
    studioSearchClose: document.getElementById('studio-search-close'),
    studioSearchFooterClose: document.getElementById('studio-search-footer-close'),
    studioSearchAdd: document.getElementById('studio-search-add'),
    studioSearchInput: document.getElementById('studio-search-input'),
    studioSearchResults: document.getElementById('studio-search-results'),
    studioSettingsOpen: document.getElementById('studio-settings-open'),
    studioSettingsWindow: document.getElementById('studio-settings-window'),
    studioSettingsClose: document.getElementById('studio-settings-close'),
    studioSettingsFooterClose: document.getElementById('studio-settings-footer-close'),
    studioSettingsSave: document.getElementById('studio-settings-save'),
    studioSettingsForm: document.getElementById('studio-settings-form'),
    studioSettingsDspEnabled: document.getElementById('studio-settings-dsp-enabled'),
    studioSettingsFeedback: document.getElementById('studio-settings-feedback'),
    studioSettingsAudioEngineStatus: document.getElementById('studio-settings-audio-engine-status'),
    studioConsoleOutput: document.getElementById('studio-console-output'),
    studioConsoleStatus: document.getElementById('studio-console-status'),
    studioConsoleAutoscroll: document.getElementById('studio-console-autoscroll'),
    studioConsolePause: document.getElementById('studio-console-pause'),
    studioConsoleClear: document.getElementById('studio-console-clear'),
    studioUsersAdd: document.getElementById('studio-users-add'),
    studioUsersAddWindow: document.getElementById('studio-users-add-window'),
    studioUsersAddClose: document.getElementById('studio-users-add-close'),
    studioUsersList: document.getElementById('studio-users-list'),
    studioUsersFeedback: document.getElementById('studio-users-feedback'),
    studioUsersAddForm: document.getElementById('studio-users-add-form'),
    studioUsersAddUsername: document.getElementById('studio-users-add-username'),
    studioUsersAddPassword: document.getElementById('studio-users-add-password'),
    studioUsersAddPassword2: document.getElementById('studio-users-add-password2'),
    studioUsersAddSave: document.getElementById('studio-users-add-save'),
    studioUsersAddCancel: document.getElementById('studio-users-add-cancel'),
    studioUsersPasswordWindow: document.getElementById('studio-users-password-window'),
    studioUsersPasswordClose: document.getElementById('studio-users-password-close'),
    studioUsersPasswordForm: document.getElementById('studio-users-password-form'),
    studioUsersPasswordUsername: document.getElementById('studio-users-password-username'),
    studioUsersCurrentPassword: document.getElementById('studio-users-current-password'),
    studioUsersPassword1: document.getElementById('studio-users-password1'),
    studioUsersPassword2: document.getElementById('studio-users-password2'),
    studioUsersPasswordSave: document.getElementById('studio-users-password-save'),
    studioUsersPasswordCancel: document.getElementById('studio-users-password-cancel'),
    studioUsersDeleteWindow: document.getElementById('studio-users-delete-window'),
    studioUsersDeleteClose: document.getElementById('studio-users-delete-close'),
    studioUsersDeleteMessage: document.getElementById('studio-users-delete-message'),
    studioUsersDeleteConfirm: document.getElementById('studio-users-delete-confirm'),
    studioUsersDeleteCancel: document.getElementById('studio-users-delete-cancel'),
    studioStationsAdd: document.getElementById('studio-stations-add'),
    studioStationsList: document.getElementById('studio-stations-list'),
    studioStationsFeedback: document.getElementById('studio-stations-feedback'),
    studioStationDeleteConfirmBackdrop: document.getElementById('studio-station-delete-confirm-backdrop'),
    studioStationDeleteConfirmBody: document.getElementById('studio-station-delete-confirm-body'),
    studioStationDeleteConfirmYes: document.getElementById('studio-station-delete-confirm-yes'),
    studioStationDeleteConfirmNo: document.getElementById('studio-station-delete-confirm-no'),
    studioStationDeleteConfirmClose: document.getElementById('studio-station-delete-confirm-close'),
    studioStationDeletePasswordBackdrop: document.getElementById('studio-station-delete-password-backdrop'),
    studioStationDeletePasswordBody: document.getElementById('studio-station-delete-password-body'),
    studioStationDeletePasswordInput: document.getElementById('studio-station-delete-password-input'),
    studioStationDeletePasswordError: document.getElementById('studio-station-delete-password-error'),
    studioStationDeletePasswordYes: document.getElementById('studio-station-delete-password-yes'),
    studioStationDeletePasswordNo: document.getElementById('studio-station-delete-password-no'),
    studioStationDeletePasswordClose: document.getElementById('studio-station-delete-password-close'),
    studioStationRenameBackdrop: document.getElementById('studio-station-rename-backdrop'),
    studioStationRenameBody: document.getElementById('studio-station-rename-body'),
    studioStationRenameInput: document.getElementById('studio-station-rename-input'),
    studioStationRenameError: document.getElementById('studio-station-rename-error'),
    studioStationRenameYes: document.getElementById('studio-station-rename-yes'),
    studioStationRenameNo: document.getElementById('studio-station-rename-no'),
    studioStationRenameClose: document.getElementById('studio-station-rename-close'),
    studioAddStationBackdrop: document.getElementById('studio-add-station-backdrop'),
    studioAddStationForm: document.getElementById('studio-add-station-form'),
    studioAddStationSave: document.getElementById('studio-add-station-save'),
    studioAddStationClose: document.getElementById('studio-add-station-close'),
    studioAddStationCancel: document.getElementById('studio-add-station-cancel'),
    studioAddStationError: document.getElementById('studio-add-station-error'),
    studioAutodjNoRepeatArtist: document.getElementById('studio-autodj-no-repeat-artist'),
    studioAutodjNoRepeatTitle: document.getElementById('studio-autodj-no-repeat-title'),
    studioAutodjNoRepeatTrack: document.getElementById('studio-autodj-no-repeat-track'),
    studioAutodjKeepQueue: document.getElementById('studio-autodj-keep-queue'),
    studioAutodjAddCategory: document.getElementById('studio-autodj-add-category'),
    studioAutodjCategoryWindow: document.getElementById('studio-autodj-category-window'),
    studioAutodjCategoryClose: document.getElementById('studio-autodj-category-close'),
    studioAutodjCategoryCancel: document.getElementById('studio-autodj-category-cancel'),
    studioAutodjCategoryOk: document.getElementById('studio-autodj-category-ok'),
    studioAutodjCategoryList: document.getElementById('studio-autodj-category-list'),
    studioAutodjCategoryNoRules: document.getElementById('studio-autodj-category-norules'),
    studioAutodjLoad: document.getElementById('studio-autodj-load'),
    studioAutodjSave: document.getElementById('studio-autodj-save'),
    studioAutodjEditor: document.getElementById('studio-autodj-editor'),
    studioAutodjLoadWindow: document.getElementById('studio-autodj-load-window'),
    studioAutodjLoadClose: document.getElementById('studio-autodj-load-close'),
    studioAutodjLoadCancel: document.getElementById('studio-autodj-load-cancel'),
    studioAutodjLoadConfirm: document.getElementById('studio-autodj-load-confirm'),
    studioAutodjLoadBreadcrumb: document.getElementById('studio-autodj-load-breadcrumb'),
    studioAutodjLoadRows: document.getElementById('studio-autodj-load-rows'),
    studioAutodjLoadSummary: document.getElementById('studio-autodj-load-summary'),
    studioAutodjSaveWindow: document.getElementById('studio-autodj-save-window'),
    studioAutodjSaveClose: document.getElementById('studio-autodj-save-close'),
    studioAutodjSaveCancel: document.getElementById('studio-autodj-save-cancel'),
    studioAutodjSaveConfirm: document.getElementById('studio-autodj-save-confirm'),
    studioAutodjSaveBreadcrumb: document.getElementById('studio-autodj-save-breadcrumb'),
    studioAutodjSaveRows: document.getElementById('studio-autodj-save-rows'),
    studioAutodjSaveSummary: document.getElementById('studio-autodj-save-summary'),
    studioAutodjSaveFilename: document.getElementById('studio-autodj-save-filename'),
    autodjToast: document.getElementById('autodj-toast'),
    autodjToastMsg: document.getElementById('autodj-toast-msg'),
    autodjToastClose: document.getElementById('autodj-toast-close'),
    clockDate: document.getElementById('clock-date'),
    clockMajorTicks: document.getElementById('clock-major-ticks'),
    clockMinorTicks: document.getElementById('clock-minor-ticks'),
    clockHourHand: document.getElementById('clock-hour-hand'),
    clockMinuteHand: document.getElementById('clock-minute-hand'),
    clockSecondHand: document.getElementById('clock-second-hand')
  };

  let scriptsAddMenuOpen = false;

  const panelElements = Array.from(document.querySelectorAll('.studio-panel[data-panel]'));
  const studioWorkspace = document.getElementById('studio-workspace');
  const studioStage = document.getElementById('studio-stage');
  const PANEL_GRID_SIZE = 12;
  const studioUsersState = {
    users: [],
    currentUserId: null,
    passwordUserId: null,
    deleteUserId: null
  };

  const studioStationsState = {
    stations: [],
    renameStationKey: null,
    renameStationName: '',
    uptimeTimer: null
  };


  const studioConsoleState = {
    socket: null,
    paused: false,
    pending: '',
    intentionallyClosed: false,
    initialized: false,
    currentLineNode: null,
    currentLineCells: [],
    cursorColumn: 0,
    escapePending: '',
    lineNodes: [],
    maxLines: 10000,
    style: null,
    styleKey: '',
    styleRegistry: new Map()
  };

  function studioPlural(count, singular, pluralForm){
    return count === 1 ? singular : pluralForm;
  }

  function formatStudioStationUptime(ms){
    if (!Number.isFinite(ms) || ms < 0) ms = 0;
    const totalMinutes = Math.floor(ms / 60000);
    const days = Math.floor(totalMinutes / (60 * 24));
    const hours = Math.floor((totalMinutes % (60 * 24)) / 60);
    const minutes = totalMinutes % 60;
    const parts = [];
    if (days > 0) parts.push(days + ' ' + studioPlural(days, 'day', 'days'));
    if (hours > 0 || days > 0) parts.push(hours + ' ' + studioPlural(hours, 'hour', 'hours'));
    parts.push(minutes + ' ' + studioPlural(minutes, 'minute', 'minutes'));
    if (parts.length === 1) return parts[0];
    if (parts.length === 2) return parts[0] + ' and ' + parts[1];
    return parts[0] + ', ' + parts[1] + ' and ' + parts[2];
  }

  function updateStudioStationsUptimeNow(){
    if (!els.studioStationsList) return;
    els.studioStationsList.querySelectorAll('[data-station-uptime]').forEach(node => {
      const startedAtIso = String(node.dataset.startedAt || '').trim();
      if (!startedAtIso) {
        node.textContent = '';
        return;
      }
      const startedAt = new Date(startedAtIso);
      if (Number.isNaN(startedAt.getTime())) {
        node.textContent = '';
        return;
      }
      node.textContent = 'Uptime: ' + formatStudioStationUptime(Date.now() - startedAt.getTime());
    });
  }

  function syncStudioStationsUptimeTimer(){
    const hasRunningStations = Boolean(els.studioStationsList && els.studioStationsList.querySelector('[data-station-uptime][data-started-at]'));
    if (hasRunningStations && !studioStationsState.uptimeTimer) {
      updateStudioStationsUptimeNow();
      studioStationsState.uptimeTimer = window.setInterval(updateStudioStationsUptimeNow, 1000);
      return;
    }
    if (!hasRunningStations && studioStationsState.uptimeTimer) {
      window.clearInterval(studioStationsState.uptimeTimer);
      studioStationsState.uptimeTimer = null;
    }
  }

  function getStudioWorkspaceScale(){
    if (!studioStage) return 1;
    const raw = Number(studioStage.dataset.workspaceScale || '1');
    return Number.isFinite(raw) && raw > 0 ? raw : 1;
  }

  function updateStudioWorkspaceScale(){
    if (!studioWorkspace || !studioStage) return 1;
    const stageWidth = studioStage.offsetWidth || parseFloat(getComputedStyle(studioStage).width) || 0;
    if (!stageWidth) return 1;
    const availableWidth = Math.max(0, studioWorkspace.clientWidth - 24);
    const nextScale = availableWidth > 0 ? Math.min(1, availableWidth / stageWidth) : 1;
    studioStage.dataset.workspaceScale = String(nextScale);
    studioStage.style.transform = nextScale === 1 ? '' : `scale(${nextScale})`;
    const grid = studioStage.querySelector('.studio-grid');
    const stageHeight = (grid ? grid.offsetHeight : studioStage.offsetHeight) || 0;
    const scaledHeight = stageHeight > 0 ? Math.ceil(stageHeight * nextScale) : 0;
    studioWorkspace.style.minHeight = scaledHeight ? `${scaledHeight + 24}px` : '';
    return nextScale;
  }


  let selectedCategoryId = null;
  const selectedQueueIds = new Set();
  let lastSelectedQueueId = null;
  let currentQueueItems = [];
  let currentQueueEmptyText = 'Queue is empty';
  let queueDragSourceId = null;
  let queueDropIndicator = null;
  let queueReorderInFlight = false;
  let playlistTrackDragIds = [];
  const selectedTrackIds = new Set();
  let lastSelectedTrackId = null;
  let currentRenderedTracks = [];
  let currentRenderedTracksEmptyText = 'No tracks in category';
  let preferredLayoutName = 'layout-1';
  let selectedEncoderId = null;
  const panelStateByLayout = {
    'layout-1': {},
    'layout-2': {}
  };
  let saveStateTimer = null;
  let playlistContextTargetId = null;
  let tracksAddMenuOpen = false;
  let tracksAddMenuTarget = "playlist";
  let playlistRenameState = null;
  let playlistBrowserState = null;
  let scriptsBrowserState = null;
  let studioSearchContext = 'queue';
  let studioSearchTimer = null;
  let studioSearchController = null;
  let studioSearchResultsData = [];
  let studioAutodjSaveBrowserState = { currentSub: '', parentSub: '', dirs: [] };
  let studioAutodjLoadBrowserState = { currentSub: '', parentSub: '', dirs: [], files: [], selectedFile: '' };
  let studioAutodjCategoryState = { categories: [], selectedId: null };
  const selectedStudioSearchIds = new Set();
  let lastSelectedStudioSearchId = null;
  let studioSearchAddInFlight = false;
  let studioCurrentDuration = 0;
  let studioScriptsData = [];
  let studioSchedulerRulesData = [];
  let selectedStudioScriptId = null;
  let selectedStudioEntryType = 'script';
  let selectedStudioEntryKey = null;
  let studioLastKnownOnAir = null;
  let studioOffAirStopInFlight = false;
  let studioOnAirAutoStartInFlight = false;
  let studioCurrentElapsed = 0;
  let studioCurrentDurationDisplay = '';
  let studioProgressAnchorElapsed = 0;
  let studioProgressAnchorNowMs = 0;
  let studioProgressIdentity = '';
  let studioIsPlaying = false;
  let studioIsPaused = false;
  let studioLastSongFile = null;
  let studioPauseUiOverride = null;
  let studioPauseUiOverrideIssuedAt = 0;
  let studioStopUiOverride = false;
  let studioPauseFrozenElapsed = 0;
  let studioPauseFrozenDuration = 0;
  let studioPauseFrozenDurationDisplay = '';
  let studioResumeHoldUntil = 0;
  let studioQueueEtaBaseMs = 0;
  let studioQueueEtaSignature = '';
  let studioEncoderStreamsData = [];
  let studioEncoderServerOffsetMs = 0;
  let studioUiEventsSource = null;
  let studioLastUiEventSeq = 0;
  let studioQueueHistoryRefreshTimer = 0;
  let studioQueueHistoryRefreshInFlight = false;
  let studioEncoderEventRefreshTimers = [];
  let studioOnAirEventRefreshTimers = [];
  let studioManualNextPending = false;
  let studioManualNextPendingSince = 0;

  // Browser-side refresh intervals. Keep UI timers local and avoid hitting
  // SQLite/native audio engine once per second for unchanged background data.
  const STUDIO_STATUS_POLL_MS = 2000;
  const STUDIO_PROGRESS_UI_TICK_MS = 100;
  const STUDIO_PROGRESS_HARD_SYNC_SECONDS = 1.5;
  const STUDIO_PROGRESS_MAX_SOFT_CORRECTION_SECONDS = 0.08;
  const STUDIO_ON_AIR_SYNC_MS = 10000;
  const STUDIO_QUEUE_POLL_MS = 5000;
  const STUDIO_HISTORY_POLL_MS = 30000;
  const STUDIO_ENCODERS_POLL_MS = 15000;

  function studioMonotonicNowMs(){
    if (typeof performance !== 'undefined' && performance && typeof performance.now === 'function') {
      return performance.now();
    }
    return Date.now();
  }

  function studioSongProgressIdentity(song){
    const value = song || {};
    return [
      (value.file || '').toString(),
      Number(value.queue_id || 0),
      Number(value.track_id || 0),
      (value.active_player || '').toString()
    ].join('|');
  }

  function setStudioProgressAnchor(elapsed, identity = studioProgressIdentity){
    const safeElapsed = Math.max(0, Number(elapsed) || 0);
    studioProgressAnchorElapsed = safeElapsed;
    studioProgressAnchorNowMs = studioMonotonicNowMs();
    studioProgressIdentity = (identity || '').toString();
    studioCurrentElapsed = safeElapsed;
    return safeElapsed;
  }

  function readStudioProgressElapsed(duration = studioCurrentDuration){
    let elapsed = Math.max(0, Number(studioProgressAnchorElapsed) || 0);
    if (studioIsPlaying && !studioIsPaused && studioProgressAnchorNowMs > 0) {
      elapsed += Math.max(0, (studioMonotonicNowMs() - studioProgressAnchorNowMs) / 1000);
    }
    const safeDuration = Number(duration) || 0;
    if (safeDuration > 0) elapsed = Math.min(safeDuration, elapsed);
    return elapsed;
  }

  function syncStudioProgressFromServer(serverElapsed, duration, identity, forceHardSync = false){
    const safeDuration = Number(duration) || 0;
    let authoritativeElapsed = Math.max(0, Number(serverElapsed) || 0);
    if (safeDuration > 0) authoritativeElapsed = Math.min(safeDuration, authoritativeElapsed);

    const nextIdentity = (identity || '').toString();
    const identityChanged = nextIdentity !== studioProgressIdentity;
    const now = studioMonotonicNowMs();
    let nextElapsed = authoritativeElapsed;

    if (!forceHardSync && !identityChanged && studioProgressAnchorNowMs > 0 && studioIsPlaying && !studioIsPaused) {
      let predicted = Math.max(0, Number(studioProgressAnchorElapsed) || 0);
      predicted += Math.max(0, (now - studioProgressAnchorNowMs) / 1000);
      if (safeDuration > 0) predicted = Math.min(safeDuration, predicted);

      const drift = authoritativeElapsed - predicted;
      if (Math.abs(drift) < STUDIO_PROGRESS_HARD_SYNC_SECONDS) {
        // Never step the visible clock backwards for normal sub-threshold
        // status jitter. Small positive drift is absorbed gradually; a real
        // discontinuity (seek/track change) still uses the hard-sync path.
        const correction = Math.max(
          0,
          Math.min(STUDIO_PROGRESS_MAX_SOFT_CORRECTION_SECONDS, drift)
        );
        nextElapsed = predicted + correction;
      }
    }

    if (safeDuration > 0) nextElapsed = Math.min(safeDuration, nextElapsed);
    studioProgressAnchorElapsed = Math.max(0, nextElapsed);
    studioProgressAnchorNowMs = now;
    studioProgressIdentity = nextIdentity;
    studioCurrentElapsed = studioProgressAnchorElapsed;
    return studioCurrentElapsed;
  }


  function setStudioSettingsFeedback(message, type = ''){
    if (!els.studioSettingsFeedback) return;
    els.studioSettingsFeedback.textContent = message || '';
    els.studioSettingsFeedback.classList.remove('is-error', 'is-success');
    if (type) els.studioSettingsFeedback.classList.add(type === 'error' ? 'is-error' : 'is-success');
  }

  function setStudioUsersFeedback(message, type = ''){
    if (!els.studioUsersFeedback) return;
    els.studioUsersFeedback.textContent = message || '';
    els.studioUsersFeedback.classList.remove('is-error', 'is-success');
    if (type) els.studioUsersFeedback.classList.add(type === 'error' ? 'is-error' : 'is-success');
  }

  function closeStudioUsersAddWindow(){
    if (!els.studioUsersAddWindow) return;
    closeFloatingWindow(els.studioUsersAddWindow);
  }

  function closeStudioUsersPasswordWindow(){
    if (!els.studioUsersPasswordWindow) return;
    closeFloatingWindow(els.studioUsersPasswordWindow);
  }

  function closeStudioUsersDeleteWindow(){
    if (!els.studioUsersDeleteWindow) return;
    closeFloatingWindow(els.studioUsersDeleteWindow);
  }

  function hideStudioUsersForms(){
    studioUsersState.passwordUserId = null;
    studioUsersState.deleteUserId = null;
    closeStudioUsersAddWindow();
    closeStudioUsersPasswordWindow();
    closeStudioUsersDeleteWindow();
    if (els.studioUsersAddUsername) els.studioUsersAddUsername.value = '';
    if (els.studioUsersAddPassword) els.studioUsersAddPassword.value = '';
    if (els.studioUsersAddPassword2) els.studioUsersAddPassword2.value = '';
    if (els.studioUsersCurrentPassword) els.studioUsersCurrentPassword.value = '';
    if (els.studioUsersPassword1) els.studioUsersPassword1.value = '';
    if (els.studioUsersPassword2) els.studioUsersPassword2.value = '';
    if (els.studioUsersPasswordUsername) els.studioUsersPasswordUsername.textContent = '';
    if (els.studioUsersDeleteMessage) els.studioUsersDeleteMessage.textContent = 'Are you sure you want to delete this user?';
  }

  function showStudioUsersAddForm(){
    hideStudioUsersForms();
    setStudioUsersFeedback('');
    if (els.studioUsersAddWindow) openFloatingWindow(els.studioUsersAddWindow);
    if (els.studioUsersAddUsername) {
      window.setTimeout(() => {
        try { els.studioUsersAddUsername.focus(); } catch (error) {}
      }, 0);
    }
  }

  function showStudioUsersPasswordForm(userId){
    const user = studioUsersState.users.find(item => Number(item.id) === Number(userId));
    if (!user) return;
    hideStudioUsersForms();
    studioUsersState.passwordUserId = Number(user.id);
    setStudioUsersFeedback('');
    if (els.studioUsersPasswordUsername) els.studioUsersPasswordUsername.textContent = `User: ${user.username}`;
    if (els.studioUsersPasswordWindow) openFloatingWindow(els.studioUsersPasswordWindow);
    if (els.studioUsersCurrentPassword) {
      window.setTimeout(() => {
        try { els.studioUsersCurrentPassword.focus(); } catch (error) {}
      }, 0);
    }
  }

  function showStudioUsersDeleteModal(userId){
    const user = studioUsersState.users.find(item => Number(item.id) === Number(userId));
    if (!user) return;
    hideStudioUsersForms();
    studioUsersState.deleteUserId = Number(user.id);
    setStudioUsersFeedback('');
    if (els.studioUsersDeleteMessage) els.studioUsersDeleteMessage.textContent = `Are you sure you want to delete user ${user.username}?`;
    if (els.studioUsersDeleteWindow) openFloatingWindow(els.studioUsersDeleteWindow);
    if (els.studioUsersDeleteConfirm) {
      window.setTimeout(() => {
        try { els.studioUsersDeleteConfirm.focus(); } catch (error) {}
      }, 0);
    }
  }

  function formatStudioCreatedAt(value){
    const raw = String(value || '').trim();
    if (!raw) return '';
    return raw.replace('T', ' ').replace(/\.\d+$/, '');
  }

  function renderStudioUsers(){
    if (!els.studioUsersList) return;
    if (!studioUsersState.users.length) {
      els.studioUsersList.innerHTML = '<div class="studio-users-empty">No users found.</div>';
      return;
    }
    els.studioUsersList.innerHTML = studioUsersState.users.map(user => {
      const isCurrent = Number(user.id) === Number(studioUsersState.currentUserId);
      const created = user.created_at ? `Created: ${formatStudioCreatedAt(user.created_at)}` : '';
      return `
        <div class="studio-users-card studio-users-row" data-user-id="${user.id}">
          <div class="studio-users-row__main">
            <div class="studio-users-row__name">
              <span>${user.username}</span>
              ${isCurrent ? '<span class="studio-users-badge">Current</span>' : ''}
            </div>
            <div class="studio-users-row__meta">${created}</div>
          </div>
          <div class="studio-users-row__actions">
            <button type="button" class="modal-btn modal-secondary" data-user-action="password" data-user-id="${user.id}">Change password</button>
            ${isCurrent ? '' : `<button type="button" class="modal-btn modal-secondary" data-user-action="delete" data-user-id="${user.id}">Delete</button>`}
          </div>
        </div>
      `;
    }).join('');
  }

  async function refreshStudioUsers(){
    if (!els.studioUsersList) return;
    try {
      const response = await fetch('/api/users/list', { credentials: 'same-origin' });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || !data.ok) throw new Error(data.error || 'Failed to load users.');
      studioUsersState.users = Array.isArray(data.users) ? data.users : [];
      studioUsersState.currentUserId = data.current_user_id || null;
      renderStudioUsers();
    } catch (error) {
      els.studioUsersList.innerHTML = '<div class="studio-users-empty">Failed to load users.</div>';
      setStudioUsersFeedback(error.message || 'Failed to load users.', 'error');
    }
  }

  async function submitStudioAddUser(){
    const username = String((els.studioUsersAddUsername && els.studioUsersAddUsername.value) || '').trim();
    const password = String((els.studioUsersAddPassword && els.studioUsersAddPassword.value) || '').trim();
    const password2 = String((els.studioUsersAddPassword2 && els.studioUsersAddPassword2.value) || '').trim();
    if (!username || !password || !password2) {
      setStudioUsersFeedback('Username and both password fields are required.', 'error');
      return;
    }
    if (password !== password2) {
      setStudioUsersFeedback('Passwords do not match.', 'error');
      return;
    }
    if (password.length < 12 || password.length > 256) {
      setStudioUsersFeedback('Password must be 12 to 256 characters long.', 'error');
      return;
    }
    if (els.studioUsersAddSave) els.studioUsersAddSave.disabled = true;
    try {
      const response = await fetch('/users/add', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password, password2, website: '' })
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || !data.ok) throw new Error(data.error || 'Failed to add user.');
      setStudioUsersFeedback('User added.', 'success');
      hideStudioUsersForms();
      await refreshStudioUsers();
    } catch (error) {
      let message = error.message || 'Failed to add user.';
      if (message === 'username_exists') message = 'That username already exists.';
      else if (message === 'password_mismatch') message = 'Passwords do not match.';
      else if (message === 'missing_fields') message = 'Username and both password fields are required.';
      else if (message === 'password_policy') message = 'Password must be 12 to 256 characters long.';
      setStudioUsersFeedback(message, 'error');
    } finally {
      if (els.studioUsersAddSave) els.studioUsersAddSave.disabled = false;
    }
  }

  async function deleteStudioUser(userId){
    const user = studioUsersState.users.find(item => Number(item.id) === Number(userId));
    if (!user) return;
    try {
      const response = await fetch('/api/users/delete', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_id: user.id })
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || !data.ok) throw new Error(data.error || 'Failed to delete user.');
      setStudioUsersFeedback('User deleted.', 'success');
      hideStudioUsersForms();
      await refreshStudioUsers();
    } catch (error) {
      let message = error.message || 'Failed to delete user.';
      if (message === 'cannot_delete_current_user') message = 'You cannot delete the current user.';
      setStudioUsersFeedback(message, 'error');
    }
  }

  async function submitStudioPasswordChange(){
    if (!studioUsersState.passwordUserId) return;
    const currentPassword = String((els.studioUsersCurrentPassword && els.studioUsersCurrentPassword.value) || '').trim();
    const password = String((els.studioUsersPassword1 && els.studioUsersPassword1.value) || '').trim();
    const password2 = String((els.studioUsersPassword2 && els.studioUsersPassword2.value) || '').trim();
    if (!currentPassword || !password || !password2) {
      setStudioUsersFeedback('Current password and both new password fields are required.', 'error');
      return;
    }
    if (password !== password2) {
      setStudioUsersFeedback('Passwords do not match.', 'error');
      return;
    }
    if (password.length < 12 || password.length > 256) {
      setStudioUsersFeedback('Password must be 12 to 256 characters long.', 'error');
      return;
    }
    if (els.studioUsersPasswordSave) els.studioUsersPasswordSave.disabled = true;
    try {
      const response = await fetch('/api/users/change-password', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_id: studioUsersState.passwordUserId, current_password: currentPassword, password, password2 })
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || !data.ok) throw new Error(data.error || 'Failed to change password.');
      setStudioUsersFeedback('Password updated.', 'success');
      hideStudioUsersForms();
      await refreshStudioUsers();
    } catch (error) {
      let message = error.message || 'Failed to change password.';
      if (message === 'password_mismatch') message = 'Passwords do not match.';
      else if (message === 'missing_fields') message = 'Current password and both new password fields are required.';
      else if (message === 'invalid_current_password') message = 'Current password is incorrect.';
      else if (message === 'password_policy') message = 'Password must be 12 to 256 characters long.';
      setStudioUsersFeedback(message, 'error');
    } finally {
      if (els.studioUsersPasswordSave) els.studioUsersPasswordSave.disabled = false;
    }
  }

  function getStudioAutodjToastElements(){
    return {
      toast: els.autodjToast || document.getElementById('autodj-toast'),
      msg: els.autodjToastMsg || document.getElementById('autodj-toast-msg'),
      close: els.autodjToastClose || document.getElementById('autodj-toast-close')
    };
  }

  function showStudioAutodjToast(text, stationKey, ts){
    const toastEls = getStudioAutodjToastElements();
    if (!toastEls.toast || !toastEls.msg) return;
    try{
      const storageKey = 'autodj_toast_ts_' + (stationKey || 'unknown');
      const lastToastTs = localStorage.getItem(storageKey);
      if (lastToastTs && ts && String(lastToastTs) === String(ts)) return;
      if (ts) localStorage.setItem(storageKey, String(ts));
    }catch(err){}
    toastEls.msg.textContent = text || '';
    toastEls.toast.style.display = 'block';
  }

  function hideStudioAutodjToast(){
    const toastEls = getStudioAutodjToastElements();
    if (!toastEls.toast) return;
    toastEls.toast.style.display = 'none';
  }


  function setStudioStationsFeedback(message, type = ''){
    if (!els.studioStationsFeedback) return;
    els.studioStationsFeedback.textContent = message || '';
    els.studioStationsFeedback.classList.remove('is-error', 'is-success');
    if (type) els.studioStationsFeedback.classList.add(type === 'error' ? 'is-error' : 'is-success');
  }

  function openStudioAddStationModal(){
    if (!els.studioAddStationBackdrop) return;
    els.studioAddStationBackdrop.classList.add('active');
    els.studioAddStationBackdrop.setAttribute('aria-hidden', 'false');
    document.body.classList.add('modal-open');
    if (els.studioAddStationError) {
      els.studioAddStationError.textContent = '';
      els.studioAddStationError.style.display = 'none';
    }
    const firstInput = els.studioAddStationForm ? els.studioAddStationForm.querySelector('input[name="radio_name"]') : null;
    if (firstInput) window.setTimeout(() => { try { firstInput.focus(); } catch (error) {} }, 0);
  }

  function closeStudioAddStationModal(){
    if (!els.studioAddStationBackdrop) return;
    els.studioAddStationBackdrop.classList.remove('active');
    els.studioAddStationBackdrop.setAttribute('aria-hidden', 'true');
    document.body.classList.remove('modal-open');
  }

  function setStudioAddStationError(message){
    if (!els.studioAddStationError) return;
    els.studioAddStationError.textContent = message || '';
    els.studioAddStationError.style.display = message ? 'block' : 'none';
  }

  function initializeStudioAddStationModal(){
    const backdrop = els.studioAddStationBackdrop;
    const modal = backdrop ? backdrop.querySelector('.modal--studio-station') : null;
    const titlebar = document.getElementById('studio-add-station-titlebar');
    const handle = modal ? modal.querySelector('.panel-resize-handle') : null;
    if (!backdrop || !modal || !titlebar) return;
    let dragState = null;
    let resizeState = null;
    let suppressBackdropClickUntil = 0;

    function getModalRect(){
      const rect = modal.getBoundingClientRect();
      return {
        width: modal.offsetWidth || rect.width || 760,
        height: modal.offsetHeight || rect.height || 560
      };
    }

    function applyModalRect(left, top, width, height){
      const current = getModalRect();
      const clamped = clampFloatingWindow(
        modal,
        left,
        top,
        Number.isFinite(width) ? width : current.width,
        Number.isFinite(height) ? height : current.height
      );
      modal.style.left = `${Math.round(clamped.left)}px`;
      modal.style.top = `${Math.round(clamped.top)}px`;
      modal.style.width = `${Math.round(clamped.width)}px`;
      modal.style.height = `${Math.round(clamped.height)}px`;
      modal.style.right = 'auto';
      modal.style.bottom = 'auto';
      modal.style.margin = '0';
      modal.style.transform = 'none';
    }

    function resetModalPosition(){
      const rect = getModalRect();
      const viewportWidth = window.innerWidth || document.documentElement.clientWidth || 1280;
      const left = Math.round((viewportWidth - rect.width) / 2);
      applyModalRect(left, 8, rect.width, rect.height);
    }

    const originalOpen = openStudioAddStationModal;
    openStudioAddStationModal = function(){
      originalOpen();
      window.setTimeout(resetModalPosition, 0);
    };

    function onPointerMove(event){
      if (dragState){
        applyModalRect(
          dragState.startLeft + (event.clientX - dragState.startClientX),
          dragState.startTop + (event.clientY - dragState.startClientY),
          dragState.width,
          dragState.height
        );
        dragState.moved = true;
      } else if (resizeState){
        applyModalRect(
          resizeState.left,
          resizeState.top,
          resizeState.startWidth + (event.clientX - resizeState.startClientX),
          resizeState.startHeight + (event.clientY - resizeState.startClientY)
        );
        resizeState.moved = true;
      }
    }

    function endPointerInteraction(){
      const moved = !!((dragState && dragState.moved) || (resizeState && resizeState.moved));
      dragState = null;
      resizeState = null;
      modal.classList.remove('is-dragging');
      modal.classList.remove('is-resizing');
      document.removeEventListener('pointermove', onPointerMove);
      document.removeEventListener('pointerup', endPointerInteraction);
      document.removeEventListener('pointercancel', endPointerInteraction);
      if (moved) suppressBackdropClickUntil = Date.now() + 250;
    }

    titlebar.addEventListener('pointerdown', event => {
      if (event.button !== 0) return;
      if (event.target.closest('button, a, input, select, textarea')) return;
      event.preventDefault();
      const rect = modal.getBoundingClientRect();
      dragState = {
        startClientX: event.clientX,
        startClientY: event.clientY,
        startLeft: Number.parseFloat(modal.style.left) || rect.left,
        startTop: Number.parseFloat(modal.style.top) || rect.top,
        width: modal.offsetWidth || rect.width,
        height: modal.offsetHeight || rect.height,
        moved: false
      };
      modal.classList.add('is-dragging');
      document.addEventListener('pointermove', onPointerMove);
      document.addEventListener('pointerup', endPointerInteraction);
      document.addEventListener('pointercancel', endPointerInteraction);
    });

    if (handle){
      handle.addEventListener('pointerdown', event => {
        if (event.button !== 0) return;
        event.preventDefault();
        event.stopPropagation();
        const rect = modal.getBoundingClientRect();
        resizeState = {
          startClientX: event.clientX,
          startClientY: event.clientY,
          left: Number.parseFloat(modal.style.left) || rect.left,
          top: Number.parseFloat(modal.style.top) || rect.top,
          startWidth: modal.offsetWidth || rect.width,
          startHeight: modal.offsetHeight || rect.height,
          moved: false
        };
        modal.classList.add('is-resizing');
        document.addEventListener('pointermove', onPointerMove);
        document.addEventListener('pointerup', endPointerInteraction);
        document.addEventListener('pointercancel', endPointerInteraction);
      });
    }

    backdrop.addEventListener('click', event => {
      if (event.target !== backdrop) return;
      if (dragState || resizeState || Date.now() < suppressBackdropClickUntil) return;
      closeStudioAddStationModal();
    }, true);

    modal.addEventListener('pointerdown', () => {
      modal.style.zIndex = '1';
    });

    window.addEventListener('resize', () => {
      if (!backdrop.classList.contains('active')) return;
      const rect = getModalRect();
      const left = Number.parseFloat(modal.style.left);
      const top = Number.parseFloat(modal.style.top);
      if (!Number.isFinite(left) || !Number.isFinite(top)) {
        resetModalPosition();
        return;
      }
      applyModalRect(left, top, rect.width, rect.height);
    });
  }

  function renderStudioStations(){
    if (!els.studioStationsList) return;
    if (!studioStationsState.stations.length) {
      els.studioStationsList.innerHTML = '<div class="studio-users-empty">No stations found.</div>';
      return;
    }
    els.studioStationsList.innerHTML = studioStationsState.stations.map(station => {
      const stationKey = String(station.station_key || station.id || '').trim();
      const stationName = String(station.name || stationKey || 'Station');
      const running = Boolean(station.running);
      const startedAt = running && station.started_at ? String(station.started_at).trim() : '';
      const stationNameAttr = stationName.replace(/"/g, '&quot;');
      return `
        <div class="studio-users-card studio-stations-row" data-station-key="${stationKey}">
          <div class="studio-stations-row__main">
            <div class="studio-stations-row__name">${stationName}</div>
            <div class="studio-stations-row__meta">Status: ${running ? 'Running' : 'Stopped'}${startedAt ? ` · <span data-station-uptime data-started-at="${startedAt}"></span>` : ''}</div>
          </div>
          <div class="studio-stations-row__actions">
            ${running ? '' : `<button type="button" class="modal-btn modal-secondary" data-station-action="rename" data-station-key="${stationKey}" data-station-name="${stationNameAttr}">Rename</button>`}
            ${running ? '' : `<button type="button" class="modal-btn modal-secondary" data-station-action="delete" data-station-key="${stationKey}" data-station-name="${stationNameAttr}">Delete</button>`}
          </div>
        </div>
      `;
    }).join('');
    updateStudioStationsUptimeNow();
    syncStudioStationsUptimeTimer();
  }

  async function refreshStudioStations(){
    if (!els.studioStationsList) return;
    try {
      const response = await fetch('/api/dashboard_overview', { credentials: 'same-origin', cache: 'no-store' });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || !data.success) throw new Error(data.error || 'Failed to load stations.');
      studioStationsState.stations = Array.isArray(data.stations) ? data.stations : [];
      renderStudioStations();
    } catch (error) {
      els.studioStationsList.innerHTML = '<div class="studio-users-empty">Failed to load stations.</div>';
      setStudioStationsFeedback(error.message || 'Failed to load stations.', 'error');
    }
  }

  function refreshStudioHeaderStationSwitcher(activeStationName){
    const switchers = Array.from(document.querySelectorAll('.studio-station-switcher'));
    if (!switchers.length) return;

    const body = document.body;
    const activeStationKey = String((body && body.dataset && body.dataset.stationKey) || '').trim();
    const stations = Array.isArray(studioStationsState.stations) ? studioStationsState.stations : [];
    const activeStation = stations.find(station => {
      const stationKey = String(station.station_key || station.db_filename || station.db || station.id || '').trim();
      return stationKey === activeStationKey;
    });
    const resolvedLabel = String(
      activeStationName ||
      (activeStation ? (activeStation.name || activeStationKey || 'Station') : '')
    ).trim();

    switchers.forEach(switcher => {
      let stationLabel = switcher.querySelector('.studio-station-switcher__station');
      if (!stationLabel) {
        const button = switcher.querySelector('.studio-station-switcher__btn');
        const brand = switcher.querySelector('.studio-station-switcher__brand');
        const caret = switcher.querySelector('.station-switcher-caret');
        if (button && brand) {
          stationLabel = document.createElement('span');
          stationLabel.className = 'studio-station-switcher__station';
          if (caret && caret.parentNode === button) {
            button.insertBefore(document.createTextNode(' '), caret);
            button.insertBefore(stationLabel, caret);
          } else {
            brand.insertAdjacentText('afterend', ' ');
            brand.insertAdjacentElement('afterend', stationLabel);
          }
        }
      }
      if (stationLabel) {
        stationLabel.textContent = resolvedLabel ? `(${resolvedLabel})` : '';
        stationLabel.style.display = resolvedLabel ? '' : 'none';
      }

      const menu = switcher.querySelector('.studio-station-switcher__menu');
      if (!menu) return;

      const parts = ['<a class="station-switcher-item station-switcher-dashboard" role="menuitem" href="/dashboard">Dashboard</a>', '<div class="station-switcher-divider" role="separator"></div>'];
      if (stations.length) {
        stations.forEach(station => {
          const stationKey = String(station.station_key || station.db_filename || station.db || station.id || '').trim();
          const stationName = String(station.name || stationKey || 'Station').trim()
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
          const stationKeyAttr = stationKey
            .replace(/&/g, '&amp;')
            .replace(/"/g, '&quot;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;');
          parts.push(`<button type="button" class="station-switcher-item" role="menuitem" data-station-id="${stationKeyAttr}">${stationName}</button>`);
        });
      } else {
        parts.push('<div class="station-switcher-empty">No stations</div>');
      }
      menu.innerHTML = parts.join('');
    });
  }

  async function selectStudioStation(stationId){
    const response = await fetch('/stations/select', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ station_id: stationId })
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || !data.ok) throw new Error(data.error || 'Failed to select station.');
    window.location.href = '/broadcaster';
  }

  async function submitStudioAddStation(){
    if (!els.studioAddStationForm || !els.studioAddStationSave) return;
    const formData = new FormData(els.studioAddStationForm);
    const payload = Object.fromEntries(formData.entries());
    els.studioAddStationSave.disabled = true;
    setStudioAddStationError('');
    try {
      const response = await fetch('/api/studio/stations/create', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      const data = await response.json().catch(() => null);
      if (!response.ok || !data || !data.ok) {
        setStudioAddStationError((data && data.error) || 'Failed to create station.');
        return;
      }
      closeStudioAddStationModal();
      await refreshStudioStations();
      setStudioStationsFeedback('Station created.', 'success');
    } catch (error) {
      setStudioAddStationError('Failed to create station.');
    } finally {
      els.studioAddStationSave.disabled = false;
    }
  }

  function hideStudioStationRenameModal(){
    if (!els.studioStationRenameBackdrop) return;
    closeFloatingWindow(els.studioStationRenameBackdrop);
    if (els.studioStationRenameError) {
      els.studioStationRenameError.style.display = 'none';
      els.studioStationRenameError.textContent = '';
    }
  }

  function openStudioStationRenameModal(stationKey, stationName){
    if (!els.studioStationRenameBackdrop || !els.studioStationRenameInput) return;
    studioStationsState.renameStationKey = String(stationKey || '').trim();
    studioStationsState.renameStationName = String(stationName || stationKey || 'Station').trim();
    if (els.studioStationRenameBody) {
      els.studioStationRenameBody.textContent = 'Enter the new name for "' + studioStationsState.renameStationName + '".';
    }
    els.studioStationRenameInput.value = studioStationsState.renameStationName;
    if (els.studioStationRenameError) {
      els.studioStationRenameError.style.display = 'none';
      els.studioStationRenameError.textContent = '';
    }
    openFloatingWindow(els.studioStationRenameBackdrop);
    window.setTimeout(() => {
      try {
        els.studioStationRenameInput.focus();
        els.studioStationRenameInput.select();
      } catch (error) {}
    }, 0);
  }

  async function submitStudioStationRename(){
    const stationKey = String(studioStationsState.renameStationKey || '').trim();
    const input = els.studioStationRenameInput;
    if (!stationKey || !input) return;
    const newName = String(input.value || '').trim();
    if (!newName) {
      if (els.studioStationRenameError) {
        els.studioStationRenameError.textContent = 'Please enter a new station name.';
        els.studioStationRenameError.style.display = 'block';
      }
      try { input.focus(); } catch (error) {}
      return;
    }
    if (els.studioStationRenameYes) els.studioStationRenameYes.disabled = true;
    if (els.studioStationRenameError) {
      els.studioStationRenameError.style.display = 'none';
      els.studioStationRenameError.textContent = '';
    }
    try {
      const response = await fetch('/api/studio/stations/rename', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ station_id: stationKey, new_name: newName })
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || !data.ok) {
        throw new Error(data.error || 'Failed to rename station.');
      }
      hideStudioStationRenameModal();
      await refreshStudioStations();
      if (data && data.station && data.station.db_filename) {
        document.body.dataset.stationKey = String(data.station.db_filename);
      }
      refreshStudioHeaderStationSwitcher(data && data.station ? data.station.name : '');
      setStudioStationsFeedback('Station renamed.', 'success');
    } catch (error) {
      if (els.studioStationRenameError) {
        els.studioStationRenameError.textContent = error.message || 'Failed to rename station.';
        els.studioStationRenameError.style.display = 'block';
      }
    } finally {
      if (els.studioStationRenameYes) els.studioStationRenameYes.disabled = false;
    }
  }

  function hideStudioStationDeleteConfirmModal(){
    if (!els.studioStationDeleteConfirmBackdrop) return;
    closeFloatingWindow(els.studioStationDeleteConfirmBackdrop);
  }

  function hideStudioStationDeletePasswordModal(){
    if (!els.studioStationDeletePasswordBackdrop) return;
    closeFloatingWindow(els.studioStationDeletePasswordBackdrop);
  }

  function promptStudioStationDeleteConfirm(stationName){
    const backdrop = els.studioStationDeleteConfirmBackdrop;
    if (!backdrop) return Promise.resolve(false);
    const body = els.studioStationDeleteConfirmBody;
    const name = (stationName || 'this station').trim();
    if (body) body.textContent = 'Are you sure you want to delete "' + name + '"?';
    backdrop.dataset.result = '';
    openFloatingWindow(backdrop);
    return new Promise(resolve => {
      const timer = window.setInterval(() => {
        if (backdrop.dataset.result === 'yes') {
          window.clearInterval(timer);
          backdrop.dataset.result = '';
          hideStudioStationDeleteConfirmModal();
          resolve(true);
          return;
        }
        if (backdrop.getAttribute('aria-hidden') === 'true' || backdrop.style.display === 'none') {
          window.clearInterval(timer);
          resolve(false);
        }
      }, 100);
    });
  }

  function promptStudioStationDeletePassword(stationName, preserveError){
    const backdrop = els.studioStationDeletePasswordBackdrop;
    if (!backdrop) return Promise.resolve(null);
    const name = (stationName || 'this station').trim();
    if (els.studioStationDeletePasswordBody) {
      els.studioStationDeletePasswordBody.textContent = 'Enter your password to delete "' + name + '".';
    }
    if (els.studioStationDeletePasswordInput) els.studioStationDeletePasswordInput.value = '';
    if (els.studioStationDeletePasswordError && !preserveError) {
      els.studioStationDeletePasswordError.style.display = 'none';
      els.studioStationDeletePasswordError.textContent = '';
    }
    backdrop.dataset.result = '';
    openFloatingWindow(backdrop);
    window.setTimeout(() => {
      try { if (els.studioStationDeletePasswordInput) els.studioStationDeletePasswordInput.focus(); } catch (error) {}
    }, 50);
    return new Promise(resolve => {
      const timer = window.setInterval(() => {
        if (backdrop.dataset.result === 'yes') {
          window.clearInterval(timer);
          backdrop.dataset.result = '';
          resolve(els.studioStationDeletePasswordInput ? els.studioStationDeletePasswordInput.value : '');
          return;
        }
        if (backdrop.getAttribute('aria-hidden') === 'true' || backdrop.style.display === 'none') {
          window.clearInterval(timer);
          resolve(null);
        }
      }, 100);
    });
  }

  async function deleteStudioStation(stationKey, stationName){
    if (!stationKey) return;
    const confirmed = await promptStudioStationDeleteConfirm(stationName || stationKey);
    if (!confirmed) return;
    let preserveError = false;
    while (true) {
      const password = await promptStudioStationDeletePassword(stationName || stationKey, preserveError);
      if (password === null) return;
      const response = await fetch(`/stations/${encodeURIComponent(stationKey)}/delete`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ password })
      });
      const data = await response.json().catch(() => ({}));
      if (response.ok && data.success) {
        hideStudioStationDeletePasswordModal();
        await refreshStudioStations();
        if (!studioStationsState.stations || !studioStationsState.stations.length) {
          window.location.href = '/dashboard';
          return;
        }
        setStudioStationsFeedback('Station deleted.', 'success');
        return;
      }
      let message = data.error || 'Delete failed.';
      if (message === 'invalid_password') {
        if (els.studioStationDeletePasswordError) {
          els.studioStationDeletePasswordError.textContent = 'Invalid password.';
          els.studioStationDeletePasswordError.style.display = 'block';
        }
        preserveError = true;
        continue;
      }
      hideStudioStationDeletePasswordModal();
      if (message === 'station_running') message = 'Station is running.';
      throw new Error(message);
    }
  }

  function setStudioConsoleStatus(text, state){
    if (!els.studioConsoleStatus) return;
    els.studioConsoleStatus.textContent = text || '';
    els.studioConsoleStatus.classList.remove('is-connected', 'is-connecting', 'is-disconnected', 'is-paused');
    els.studioConsoleStatus.classList.add(`is-${state || 'disconnected'}`);
  }

  const STUDIO_CONSOLE_DEFAULT_FOREGROUND = '#d6deeb';
  const STUDIO_CONSOLE_DEFAULT_BACKGROUND = '#070b12';
  const STUDIO_CONSOLE_BASIC_COLORS = [
    '#000000', '#cd3131', '#0dbc79', '#e5e510',
    '#2472c8', '#bc3fbc', '#11a8cd', '#e5e5e5',
    '#666666', '#f14c4c', '#23d18b', '#f5f543',
    '#3b8eea', '#d670d6', '#29b8db', '#ffffff'
  ];

  function defaultStudioConsoleAnsiStyle(){
    return {
      foreground: null,
      background: null,
      bold: false,
      dim: false,
      italic: false,
      underline: false,
      inverse: false,
      strike: false
    };
  }

  function studioConsoleAnsiStyleKey(style){
    return [
      style.foreground || '',
      style.background || '',
      style.bold ? '1' : '0',
      style.dim ? '1' : '0',
      style.italic ? '1' : '0',
      style.underline ? '1' : '0',
      style.inverse ? '1' : '0',
      style.strike ? '1' : '0'
    ].join('|');
  }

  function registerStudioConsoleAnsiStyle(){
    const key = studioConsoleAnsiStyleKey(studioConsoleState.style);
    studioConsoleState.styleKey = key;
    if (!studioConsoleState.styleRegistry.has(key)) {
      studioConsoleState.styleRegistry.set(key, {...studioConsoleState.style});
    }
  }

  function studioConsoleColor256(index){
    const value = Math.max(0, Math.min(255, Number(index) || 0));
    if (value < 16) return STUDIO_CONSOLE_BASIC_COLORS[value];
    if (value >= 232) {
      const gray = 8 + ((value - 232) * 10);
      return `rgb(${gray}, ${gray}, ${gray})`;
    }
    const cube = value - 16;
    const red = Math.floor(cube / 36);
    const green = Math.floor((cube % 36) / 6);
    const blue = cube % 6;
    const component = part => part === 0 ? 0 : 55 + (part * 40);
    return `rgb(${component(red)}, ${component(green)}, ${component(blue)})`;
  }

  function applyStudioConsoleSgr(parameterText){
    const normalized = String(parameterText || '').replace(/:/g, ';');
    const params = normalized === '' ? [0] : normalized.split(';').map(value => {
      const number = Number.parseInt(value, 10);
      return Number.isFinite(number) ? number : 0;
    });
    for (let index = 0; index < params.length; index += 1) {
      const code = params[index];
      if (code === 0) {
        studioConsoleState.style = defaultStudioConsoleAnsiStyle();
      } else if (code === 1) {
        studioConsoleState.style.bold = true;
      } else if (code === 2) {
        studioConsoleState.style.dim = true;
      } else if (code === 3) {
        studioConsoleState.style.italic = true;
      } else if (code === 4) {
        studioConsoleState.style.underline = true;
      } else if (code === 7) {
        studioConsoleState.style.inverse = true;
      } else if (code === 9) {
        studioConsoleState.style.strike = true;
      } else if (code === 22) {
        studioConsoleState.style.bold = false;
        studioConsoleState.style.dim = false;
      } else if (code === 23) {
        studioConsoleState.style.italic = false;
      } else if (code === 24) {
        studioConsoleState.style.underline = false;
      } else if (code === 27) {
        studioConsoleState.style.inverse = false;
      } else if (code === 29) {
        studioConsoleState.style.strike = false;
      } else if (code >= 30 && code <= 37) {
        studioConsoleState.style.foreground = STUDIO_CONSOLE_BASIC_COLORS[code - 30];
      } else if (code === 39) {
        studioConsoleState.style.foreground = null;
      } else if (code >= 40 && code <= 47) {
        studioConsoleState.style.background = STUDIO_CONSOLE_BASIC_COLORS[code - 40];
      } else if (code === 49) {
        studioConsoleState.style.background = null;
      } else if (code >= 90 && code <= 97) {
        studioConsoleState.style.foreground = STUDIO_CONSOLE_BASIC_COLORS[8 + code - 90];
      } else if (code >= 100 && code <= 107) {
        studioConsoleState.style.background = STUDIO_CONSOLE_BASIC_COLORS[8 + code - 100];
      } else if ((code === 38 || code === 48) && params[index + 1] === 5 && Number.isFinite(params[index + 2])) {
        const color = studioConsoleColor256(params[index + 2]);
        if (code === 38) studioConsoleState.style.foreground = color;
        else studioConsoleState.style.background = color;
        index += 2;
      } else if ((code === 38 || code === 48) && params[index + 1] === 2 && params.length > index + 4) {
        const red = Math.max(0, Math.min(255, params[index + 2]));
        const green = Math.max(0, Math.min(255, params[index + 3]));
        const blue = Math.max(0, Math.min(255, params[index + 4]));
        const color = `rgb(${red}, ${green}, ${blue})`;
        if (code === 38) studioConsoleState.style.foreground = color;
        else studioConsoleState.style.background = color;
        index += 4;
      }
    }
    registerStudioConsoleAnsiStyle();
  }

  function applyStudioConsoleSpanStyle(span, style){
    let foreground = style.foreground;
    let background = style.background;
    if (style.inverse) {
      const originalForeground = foreground || STUDIO_CONSOLE_DEFAULT_FOREGROUND;
      foreground = background || STUDIO_CONSOLE_DEFAULT_BACKGROUND;
      background = originalForeground;
    }
    if (foreground) span.style.color = foreground;
    if (background) span.style.backgroundColor = background;
    if (style.bold) span.style.fontWeight = '700';
    if (style.dim) span.style.opacity = '.68';
    if (style.italic) span.style.fontStyle = 'italic';
    const decorations = [];
    if (style.underline) decorations.push('underline');
    if (style.strike) decorations.push('line-through');
    if (decorations.length) span.style.textDecoration = decorations.join(' ');
  }

  function renderStudioConsoleCurrentLine(){
    const lineNode = studioConsoleState.currentLineNode;
    if (!lineNode) return;
    lineNode.textContent = '';
    const cells = studioConsoleState.currentLineCells;
    if (!cells.length) return;
    const fragment = document.createDocumentFragment();
    let currentKey = cells[0].styleKey;
    let currentText = '';
    const flush = () => {
      if (!currentText) return;
      const style = studioConsoleState.styleRegistry.get(currentKey) || defaultStudioConsoleAnsiStyle();
      const isDefault = currentKey === studioConsoleAnsiStyleKey(defaultStudioConsoleAnsiStyle());
      if (isDefault) {
        fragment.appendChild(document.createTextNode(currentText));
      } else {
        const span = document.createElement('span');
        applyStudioConsoleSpanStyle(span, style);
        span.textContent = currentText;
        fragment.appendChild(span);
      }
      currentText = '';
    };
    for (const cell of cells) {
      if (cell.styleKey !== currentKey) {
        flush();
        currentKey = cell.styleKey;
      }
      currentText += cell.char;
    }
    flush();
    lineNode.appendChild(fragment);
  }

  function trimStudioConsoleLines(){
    const output = els.studioConsoleOutput;
    if (!output) return;
    while (studioConsoleState.lineNodes.length > studioConsoleState.maxLines) {
      const oldest = studioConsoleState.lineNodes.shift();
      if (oldest && oldest.parentNode === output) output.removeChild(oldest);
    }
  }

  function createStudioConsoleLine(){
    const output = els.studioConsoleOutput;
    if (!output) return null;
    const line = document.createElement('span');
    line.className = 'studio-console-line';
    output.appendChild(line);
    studioConsoleState.lineNodes.push(line);
    trimStudioConsoleLines();
    return line;
  }

  function resetStudioConsoleDom(){
    const output = els.studioConsoleOutput;
    if (!output) return;
    output.textContent = '';
    studioConsoleState.currentLineCells = [];
    studioConsoleState.cursorColumn = 0;
    studioConsoleState.escapePending = '';
    studioConsoleState.lineNodes = [];
    studioConsoleState.styleRegistry = new Map();
    studioConsoleState.style = defaultStudioConsoleAnsiStyle();
    registerStudioConsoleAnsiStyle();
    studioConsoleState.currentLineNode = createStudioConsoleLine();
    studioConsoleState.initialized = true;
  }

  function initializeStudioConsoleDom(){
    if (studioConsoleState.initialized || !els.studioConsoleOutput) return;
    resetStudioConsoleDom();
  }

  function isStudioConsoleNearBottom(){
    const output = els.studioConsoleOutput;
    if (!output) return true;
    return output.scrollHeight - output.scrollTop - output.clientHeight < 30;
  }

  function putStudioConsoleCharacter(character){
    const cells = studioConsoleState.currentLineCells;
    while (cells.length < studioConsoleState.cursorColumn) {
      cells.push({char: ' ', styleKey: studioConsoleAnsiStyleKey(defaultStudioConsoleAnsiStyle())});
    }
    const cell = {char: character, styleKey: studioConsoleState.styleKey};
    if (studioConsoleState.cursorColumn < cells.length) cells[studioConsoleState.cursorColumn] = cell;
    else cells.push(cell);
    studioConsoleState.cursorColumn += 1;
  }

  function newStudioConsoleLine(){
    renderStudioConsoleCurrentLine();
    studioConsoleState.currentLineCells = [];
    studioConsoleState.cursorColumn = 0;
    studioConsoleState.currentLineNode = createStudioConsoleLine();
  }

  function handleStudioConsoleCsi(parameterText, finalByte){
    const firstParamText = String(parameterText || '').split(';', 1)[0].replace(/[^0-9]/g, '');
    const firstParam = firstParamText === '' ? 0 : Number.parseInt(firstParamText, 10);
    if (finalByte === 'm') {
      applyStudioConsoleSgr(parameterText);
      return;
    }
    if (finalByte === 'K') {
      if (firstParam === 2) {
        studioConsoleState.currentLineCells = [];
        studioConsoleState.cursorColumn = 0;
      } else if (firstParam === 1) {
        const limit = Math.min(studioConsoleState.cursorColumn + 1, studioConsoleState.currentLineCells.length);
        for (let index = 0; index < limit; index += 1) {
          studioConsoleState.currentLineCells[index] = {char: ' ', styleKey: studioConsoleState.styleKey};
        }
      } else {
        studioConsoleState.currentLineCells.splice(studioConsoleState.cursorColumn);
      }
      return;
    }
    if (finalByte === 'G') {
      studioConsoleState.cursorColumn = Math.max(0, (firstParam || 1) - 1);
      return;
    }
    if (finalByte === 'C') {
      studioConsoleState.cursorColumn += Math.max(1, firstParam || 1);
      return;
    }
    if (finalByte === 'D') {
      studioConsoleState.cursorColumn = Math.max(0, studioConsoleState.cursorColumn - Math.max(1, firstParam || 1));
      return;
    }
    if (finalByte === 'J' && (firstParam === 2 || firstParam === 3)) {
      resetStudioConsoleDom();
    }
  }

  function parseStudioConsoleChunk(rawText){
    initializeStudioConsoleDom();
    let text = studioConsoleState.escapePending + String(rawText || '');
    studioConsoleState.escapePending = '';
    let index = 0;
    while (index < text.length) {
      const character = text[index];
      if (character === '\x1b') {
        if (index + 1 >= text.length) {
          studioConsoleState.escapePending = text.slice(index);
          break;
        }
        const next = text[index + 1];
        if (next === '[') {
          let end = index + 2;
          while (end < text.length) {
            const code = text.charCodeAt(end);
            if (code >= 0x40 && code <= 0x7e) break;
            end += 1;
          }
          if (end >= text.length) {
            studioConsoleState.escapePending = text.slice(index);
            break;
          }
          handleStudioConsoleCsi(text.slice(index + 2, end), text[end]);
          index = end + 1;
          continue;
        }
        if (next === ']') {
          let end = index + 2;
          let found = false;
          while (end < text.length) {
            if (text[end] === '\x07') {
              end += 1;
              found = true;
              break;
            }
            if (text[end] === '\x1b' && text[end + 1] === '\\') {
              end += 2;
              found = true;
              break;
            }
            end += 1;
          }
          if (!found) {
            studioConsoleState.escapePending = text.slice(index);
            break;
          }
          index = end;
          continue;
        }
        index += 2;
        continue;
      }
      if (character === '\r') {
        studioConsoleState.cursorColumn = 0;
        index += 1;
        continue;
      }
      if (character === '\n') {
        newStudioConsoleLine();
        index += 1;
        continue;
      }
      if (character === '\b') {
        studioConsoleState.cursorColumn = Math.max(0, studioConsoleState.cursorColumn - 1);
        index += 1;
        continue;
      }
      if (character === '\t') {
        const spaces = 8 - (studioConsoleState.cursorColumn % 8);
        for (let count = 0; count < spaces; count += 1) putStudioConsoleCharacter(' ');
        index += 1;
        continue;
      }
      const code = character.charCodeAt(0);
      if (code < 0x20 || character === '\x7f') {
        index += 1;
        continue;
      }
      const codePoint = text.codePointAt(index);
      const printable = String.fromCodePoint(codePoint);
      putStudioConsoleCharacter(printable);
      index += printable.length;
    }
    renderStudioConsoleCurrentLine();
  }

  function writeStudioConsole(text){
    if (!text) return;
    if (studioConsoleState.paused) {
      studioConsoleState.pending += text;
      if (studioConsoleState.pending.length > 1_000_000) {
        studioConsoleState.pending = studioConsoleState.pending.slice(-1_000_000);
      }
      return;
    }
    const output = els.studioConsoleOutput;
    const shouldScroll = Boolean(output && els.studioConsoleAutoscroll && els.studioConsoleAutoscroll.checked && isStudioConsoleNearBottom());
    parseStudioConsoleChunk(text);
    if (shouldScroll && output) output.scrollTop = output.scrollHeight;
  }

  function clearStudioConsoleView(){
    studioConsoleState.pending = '';
    resetStudioConsoleDom();
  }

  function isStudioConsoleConnected(){
    return Boolean(studioConsoleState.socket && studioConsoleState.socket.readyState === 1);
  }

  function closeStudioConsoleSocket(intentional){
    studioConsoleState.intentionallyClosed = Boolean(intentional);
    const source = studioConsoleState.socket;
    studioConsoleState.socket = null;
    if (source) {
      try { source.close(); } catch (_) {}
    }
    if (intentional) setStudioConsoleStatus('Disconnected', 'disconnected');
  }

  function connectStudioConsole(){
    if (!els.studioConsoleOutput || studioConsoleState.socket) return;
    initializeStudioConsoleDom();
    if (typeof window.EventSource !== 'function') {
      setStudioConsoleStatus('Event stream unavailable', 'disconnected');
      return;
    }
    studioConsoleState.intentionallyClosed = false;
    clearStudioConsoleView();
    setStudioConsoleStatus('Connecting...', 'connecting');
    const source = new EventSource('/api/console/stream');
    studioConsoleState.socket = source;

    source.addEventListener('open', () => {
      if (studioConsoleState.socket !== source) return;
      setStudioConsoleStatus(studioConsoleState.paused ? 'Paused' : 'Connected', studioConsoleState.paused ? 'paused' : 'connected');
    });
    source.addEventListener('message', event => {
      if (studioConsoleState.socket !== source) return;
      let text = '';
      try {
        text = JSON.parse(event.data);
      } catch (_) {
        text = typeof event.data === 'string' ? event.data : '';
      }
      writeStudioConsole(typeof text === 'string' ? text : String(text || ''));
    });
    source.addEventListener('error', () => {
      if (studioConsoleState.socket !== source || studioConsoleState.intentionallyClosed) return;
      // EventSource reconnects automatically using the retry value sent by the server.
      setStudioConsoleStatus('Disconnected - reconnecting...', 'disconnected');
    });
  }

  function setStudioConsolePaused(paused){
    studioConsoleState.paused = Boolean(paused);
    if (els.studioConsolePause) els.studioConsolePause.textContent = studioConsoleState.paused ? 'Resume' : 'Pause';
    if (studioConsoleState.paused) {
      setStudioConsoleStatus('Paused', 'paused');
      return;
    }
    const pending = studioConsoleState.pending;
    studioConsoleState.pending = '';
    if (pending) writeStudioConsole(pending);
    setStudioConsoleStatus(isStudioConsoleConnected() ? 'Connected' : 'Disconnected', isStudioConsoleConnected() ? 'connected' : 'disconnected');
  }

  function switchStudioSettingsSection(sectionName){
    document.querySelectorAll('[data-settings-section]').forEach(btn => {
      btn.classList.toggle('is-active', btn.dataset.settingsSection === sectionName);
    });
    document.querySelectorAll('[data-settings-panel]').forEach(panel => {
      panel.classList.toggle('is-active', panel.dataset.settingsPanel === sectionName);
    });
    const settingsContent = document.querySelector('.studio-settings-content');
    if (settingsContent) settingsContent.classList.toggle('is-console-active', sectionName === 'console');
    if (sectionName === 'users') {
      refreshStudioUsers().catch(() => {});
    }
    if (sectionName === 'stations') {
      refreshStudioStations().catch(() => {});
    }
    if (sectionName === 'console') {
      connectStudioConsole();
    } else {
      closeStudioConsoleSocket(true);
    }
  }

  async function refreshStudioSettingsStatus(){
    if (!els.studioSettingsAudioEngineStatus) return;
    try {
      const response = await fetch('/api/audio-engine/status');
      const data = await response.json();
      let text = data.status || 'unknown';
      if (data.pid) text += ` (PID ${data.pid})`;
      els.studioSettingsAudioEngineStatus.textContent = text;
    } catch (error) {
      els.studioSettingsAudioEngineStatus.textContent = 'error';
    }
  }

  function sanitizeStudioNumericInput(input){
    if (!input) return '0';
    input.value = String(input.value || '').replace(/\D+/g, '');
    if (!input.value) input.value = '0';
    return input.value;
  }

  function bindStudioNumericOnly(input){
    if (!input) return;
    input.addEventListener('input', () => {
      input.value = String(input.value || '').replace(/\D+/g, '');
    });
    input.addEventListener('blur', () => {
      sanitizeStudioNumericInput(input);
    });
  }


  function setStudioAutodjCategorySelection(categoryId){
    studioAutodjCategoryState.selectedId = categoryId ? String(categoryId) : null;
    if (!els.studioAutodjCategoryList) return;
    els.studioAutodjCategoryList.querySelectorAll('.studio-autodj-category-item').forEach(button => {
      button.classList.toggle('is-selected', button.dataset.categoryId === String(studioAutodjCategoryState.selectedId || ''));
    });
  }

  function renderStudioAutodjCategoryList(){
    const list = els.studioAutodjCategoryList;
    if (!list) return;
    list.innerHTML = '';
    const categories = Array.isArray(studioAutodjCategoryState.categories) ? studioAutodjCategoryState.categories : [];
    if (!categories.length) {
      const empty = document.createElement('div');
      empty.className = 'browser-footer';
      empty.textContent = 'No categories found.';
      list.appendChild(empty);
      return;
    }
    categories.forEach(category => {
      const button = document.createElement('button');
      button.type = 'button';
      button.className = 'studio-autodj-category-item';
      button.dataset.categoryId = String(category.id);
      button.textContent = String(category.name || `Category #${category.id}`);
      button.addEventListener('click', () => setStudioAutodjCategorySelection(category.id));
      list.appendChild(button);
    });
    if (!studioAutodjCategoryState.selectedId && categories.length) {
      studioAutodjCategoryState.selectedId = String(categories[0].id);
    }
    setStudioAutodjCategorySelection(studioAutodjCategoryState.selectedId);
  }

  async function loadStudioAutodjCategoryChoices(){
    const response = await fetch('/api/library/categories', { credentials: 'same-origin' });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(data.error || 'Failed to load categories.');
    }
    studioAutodjCategoryState.categories = Array.isArray(data.categories) ? data.categories : [];
    if (studioAutodjCategoryState.selectedId && !studioAutodjCategoryState.categories.some(category => String(category.id) === String(studioAutodjCategoryState.selectedId))) {
      studioAutodjCategoryState.selectedId = null;
    }
    renderStudioAutodjCategoryList();
  }

  async function openStudioAutodjCategoryWindow(){
    if (!els.studioAutodjCategoryWindow) return;
    if (els.studioAutodjCategoryNoRules) els.studioAutodjCategoryNoRules.checked = false;
    await loadStudioAutodjCategoryChoices();
    openFloatingWindow(els.studioAutodjCategoryWindow);
  }

  function closeStudioAutodjCategoryWindow(forceWindow = null){
    const win = forceWindow || els.studioAutodjCategoryWindow;
    if (!win) return;
    if (win.contains(document.activeElement) && typeof document.activeElement.blur === 'function') {
      document.activeElement.blur();
    }
    win.classList.remove('is-dragging', 'is-resizing');
    win.setAttribute('aria-hidden', 'true');
    win.style.display = 'none';
  }

  function confirmStudioAutodjCategoryWindow(sourceElement = null){
    const categoryWindow = sourceElement && typeof sourceElement.closest === 'function'
      ? sourceElement.closest('#studio-autodj-category-window') || els.studioAutodjCategoryWindow
      : els.studioAutodjCategoryWindow;
    if (!els.studioAutodjEditor) {
      closeStudioAutodjCategoryWindow(categoryWindow);
      return;
    }
    const selectedId = String(studioAutodjCategoryState.selectedId || '').trim();
    const category = (Array.isArray(studioAutodjCategoryState.categories) ? studioAutodjCategoryState.categories : []).find(item => String(item.id) === selectedId);
    if (!category) {
      closeStudioAutodjCategoryWindow(categoryWindow);
      return;
    }
    const categoryName = String(category.name || `Category #${category.id}`)
      .replace(/\\/g, '\\\\')
      .replace(/'/g, "\\'");
    const queueArgs = [];
    if (els.studioAutodjCategoryNoRules && els.studioAutodjCategoryNoRules.checked) queueArgs.push('NoRules');
    const line = `Cat['${categoryName}'].QueueBottom(${queueArgs.join(', ')});`;
    const textarea = els.studioAutodjEditor;
    const start = textarea.selectionStart ?? textarea.value.length;
    const end = textarea.selectionEnd ?? textarea.value.length;
    const prefix = textarea.value.slice(0, start);
    const suffix = textarea.value.slice(end);
    const needsLeadingBreak = prefix.length > 0 && !prefix.endsWith('\n');
    const normalizedSuffix = suffix.startsWith('\n') ? suffix.slice(1) : suffix;
    const insertion = `${needsLeadingBreak ? '\n' : ''}${line}\n`;
    textarea.value = `${prefix}${insertion}${normalizedSuffix}`;
    const caret = prefix.length + insertion.length;
    saveStudioAutodjEditorDraft();
    closeStudioAutodjCategoryWindow(categoryWindow);
    window.setTimeout(() => {
      closeStudioAutodjCategoryWindow(categoryWindow);
      textarea.focus();
      textarea.setSelectionRange(caret, caret);
    }, 0);
  }


  const STUDIO_AUTODJ_EDITOR_STORAGE_KEY = 'studioAutodjEditorDraft';

  function getStudioAutodjEditorValue(){
    return els.studioAutodjEditor ? String(els.studioAutodjEditor.value || '') : '';
  }

  function setStudioAutodjEditorValue(value){
    if (!els.studioAutodjEditor) return;
    els.studioAutodjEditor.value = String(value || '');
  }

  function insertStudioAutodjCategoryTemplate(){
    if (!els.studioAutodjEditor) return;
    const textarea = els.studioAutodjEditor;
    const template = '\n[New Category]\n';
    const start = textarea.selectionStart ?? textarea.value.length;
    const end = textarea.selectionEnd ?? textarea.value.length;
    const prefix = textarea.value.slice(0, start);
    const suffix = textarea.value.slice(end);
    const needsLeadingBreak = prefix && !prefix.endsWith('\n');
    const insertion = `${needsLeadingBreak ? '\n' : ''}${template}`;
    textarea.value = `${prefix}${insertion}${suffix}`;
    const caret = prefix.length + insertion.length;
    textarea.focus();
    textarea.setSelectionRange(caret, caret);
  }

  function loadStudioAutodjEditorDraft(){
    try {
      const value = window.localStorage.getItem(STUDIO_AUTODJ_EDITOR_STORAGE_KEY);
      if (value !== null) setStudioAutodjEditorValue(value);
    } catch (error) {
      console.error('Failed to load studio AutoDJ editor draft', error);
    }
  }

  function saveStudioAutodjEditorDraft(){
    try {
      window.localStorage.setItem(STUDIO_AUTODJ_EDITOR_STORAGE_KEY, getStudioAutodjEditorValue());
    } catch (error) {
      console.error('Failed to save studio AutoDJ editor draft', error);
    }
  }

  function renderStudioAutodjSaveBreadcrumb(currentSub){
    const container = els.studioAutodjSaveBreadcrumb;
    if (!container) return;
    const normalized = String(currentSub || '').replace(/\\/g, '/').replace(/^\/+|\/+$/g, '');
    container.textContent = normalized ? normalized.split('/').join(' / ') : '/';
  }

  function renderStudioAutodjSaveRows(){
    const rowsEl = els.studioAutodjSaveRows;
    if (!rowsEl) return;
    rowsEl.innerHTML = '';
    const { currentSub, parentSub, dirs, files } = studioAutodjSaveBrowserState;

    if (currentSub) {
      const upRow = document.createElement('button');
      upRow.type = 'button';
      upRow.className = 'browser-table__row studio-autodj-save-row';
      upRow.innerHTML = '<div class="browser-table__name">↖ ..</div>';
      upRow.addEventListener('dblclick', () => { loadStudioAutodjSaveBrowser(parentSub || '').catch(handleStudioAutodjSaveError); });
      rowsEl.appendChild(upRow);
    }

    dirs.forEach(dir => {
      const row = document.createElement('button');
      row.type = 'button';
      row.className = 'browser-table__row studio-autodj-save-row';
      row.innerHTML = `<div class="browser-table__name">📁 ${escapeHtml(dir.name || '')}</div>`;
      row.addEventListener('dblclick', () => { loadStudioAutodjSaveBrowser(dir.relative_path || '').catch(handleStudioAutodjSaveError); });
      rowsEl.appendChild(row);
    });

    files.forEach(file => {
      const row = document.createElement('button');
      row.type = 'button';
      row.className = 'browser-table__row studio-autodj-save-row';
      row.innerHTML = `<div class="browser-table__name">📄 ${escapeHtml(file.name || '')}</div>`;
      row.addEventListener('click', () => {
        if (els.studioAutodjSaveFilename) els.studioAutodjSaveFilename.value = file.name || '';
      });
      row.addEventListener('dblclick', () => {
        if (els.studioAutodjSaveFilename) {
          els.studioAutodjSaveFilename.value = file.name || '';
          els.studioAutodjSaveFilename.focus();
          els.studioAutodjSaveFilename.select();
        }
      });
      rowsEl.appendChild(row);
    });

    if ((!dirs || !dirs.length) && (!files || !files.length)) {
      const empty = document.createElement('div');
      empty.className = 'browser-empty';
      empty.textContent = 'No .adj files or folders found under Base music directory.';
      rowsEl.appendChild(empty);
    }
  }

  async function loadStudioAutodjSaveBrowser(sub = ''){
    const url = new URL('/api/studio/autodj/text-browser', window.location.origin);
    if (sub) url.searchParams.set('sub', sub);
    const response = await fetch(url.toString(), { credentials: 'same-origin' });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || 'Failed to load save folders.');
    studioAutodjSaveBrowserState = {
      currentSub: data.current_sub || '',
      parentSub: data.parent_sub || '',
      dirs: Array.isArray(data.dirs) ? data.dirs : [],
      files: Array.isArray(data.files) ? data.files : []
    };
    renderStudioAutodjSaveBreadcrumb(studioAutodjSaveBrowserState.currentSub);
    renderStudioAutodjSaveRows();
    if (els.studioAutodjSaveSummary) {
      const label = studioAutodjSaveBrowserState.currentSub || 'Root';
      els.studioAutodjSaveSummary.textContent = `Saving under: ${label}`;
    }
  }

  function renderStudioAutodjLoadBreadcrumb(currentSub){
    const container = els.studioAutodjLoadBreadcrumb;
    if (!container) return;
    const normalized = String(currentSub || '').replace(/\\/g, '/').replace(/^\/+|\/+$/g, '');
    container.textContent = normalized ? normalized.split('/').join(' / ') : '/';
  }

  function updateStudioAutodjLoadSummary(){
    if (!els.studioAutodjLoadSummary) return;
    if (studioAutodjLoadBrowserState.selectedFile) {
      els.studioAutodjLoadSummary.textContent = `Selected file: ${studioAutodjLoadBrowserState.selectedFile}`;
      return;
    }
    const label = studioAutodjLoadBrowserState.currentSub || 'Root';
    els.studioAutodjLoadSummary.textContent = `Browsing: ${label}`;
  }

  function syncStudioAutodjLoadSelection(){
    const rowsEl = els.studioAutodjLoadRows;
    if (!rowsEl) return;
    const selectedFile = studioAutodjLoadBrowserState.selectedFile || '';
    rowsEl.querySelectorAll('.studio-autodj-load-row[data-relative-path]').forEach(row => {
      const rowPath = row.getAttribute('data-relative-path') || '';
      row.classList.toggle('is-selected', !!selectedFile && rowPath === selectedFile);
    });
  }

  function selectStudioAutodjLoadFile(relativePath){
    studioAutodjLoadBrowserState.selectedFile = relativePath || '';
    syncStudioAutodjLoadSelection();
    updateStudioAutodjLoadSummary();
  }

  function renderStudioAutodjLoadRows(){
    const rowsEl = els.studioAutodjLoadRows;
    if (!rowsEl) return;
    rowsEl.innerHTML = '';
    const { currentSub, parentSub, dirs, files, selectedFile } = studioAutodjLoadBrowserState;

    if (currentSub) {
      const upRow = document.createElement('button');
      upRow.type = 'button';
      upRow.className = 'browser-table__row studio-autodj-load-row';
      upRow.innerHTML = '<div class="browser-table__name">↖ ..</div>';
      upRow.addEventListener('dblclick', () => { loadStudioAutodjLoadBrowser(parentSub || '').catch(handleStudioAutodjLoadError); });
      rowsEl.appendChild(upRow);
    }

    dirs.forEach(dir => {
      const row = document.createElement('button');
      row.type = 'button';
      row.className = 'browser-table__row studio-autodj-load-row';
      row.innerHTML = `<div class="browser-table__name">📁 ${escapeHtml(dir.name || '')}</div>`;
      row.addEventListener('dblclick', () => { loadStudioAutodjLoadBrowser(dir.relative_path || '').catch(handleStudioAutodjLoadError); });
      rowsEl.appendChild(row);
    });

    files.forEach(file => {
      const row = document.createElement('button');
      row.type = 'button';
      row.className = 'browser-table__row studio-autodj-load-row';
      row.setAttribute('data-relative-path', file.relative_path || '');
      if ((file.relative_path || '') === selectedFile) row.classList.add('is-selected');
      row.innerHTML = `<div class="browser-table__name">📄 ${escapeHtml(file.name || '')}</div>`;
      row.addEventListener('click', () => { selectStudioAutodjLoadFile(file.relative_path || ''); });
      row.addEventListener('dblclick', async () => {
        try {
          selectStudioAutodjLoadFile(file.relative_path || '');
          if (els.studioAutodjLoadConfirm) els.studioAutodjLoadConfirm.disabled = true;
          await submitStudioAutodjLoad();
        } catch (error) {
          handleStudioAutodjLoadError(error);
        } finally {
          if (els.studioAutodjLoadConfirm) els.studioAutodjLoadConfirm.disabled = false;
        }
      });
      rowsEl.appendChild(row);
    });

    if ((!dirs || !dirs.length) && (!files || !files.length)) {
      const empty = document.createElement('div');
      empty.className = 'browser-empty';
      empty.textContent = 'No .adj files or folders found under Base music directory.';
      rowsEl.appendChild(empty);
    }
  }

  async function loadStudioAutodjLoadBrowser(sub = ''){
    const url = new URL('/api/studio/autodj/text-load-browser', window.location.origin);
    if (sub) url.searchParams.set('sub', sub);
    const response = await fetch(url.toString(), { credentials: 'same-origin' });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.error || 'Failed to load files.');
    studioAutodjLoadBrowserState = {
      currentSub: data.current_sub || '',
      parentSub: data.parent_sub || '',
      dirs: Array.isArray(data.dirs) ? data.dirs : [],
      files: Array.isArray(data.files) ? data.files : [],
      selectedFile: ''
    };
    renderStudioAutodjLoadBreadcrumb(studioAutodjLoadBrowserState.currentSub);
    renderStudioAutodjLoadRows();
    updateStudioAutodjLoadSummary();
  }

  function handleStudioAutodjLoadError(error){
    console.error('Studio AutoDJ load browser error', error);
    setStudioSettingsFeedback((error && error.message) || 'Failed to open AutoDJ load dialog.', 'error');
  }

  async function openStudioAutodjLoadWindow(){
    if (!els.studioAutodjLoadWindow) return;
    await loadStudioAutodjLoadBrowser('');
    openFloatingWindow(els.studioAutodjLoadWindow);
  }

  function closeStudioAutodjLoadWindow(){
    closeFloatingWindow(els.studioAutodjLoadWindow);
  }

  async function submitStudioAutodjLoad(){
    const relativePath = studioAutodjLoadBrowserState.selectedFile || '';
    if (!relativePath) throw new Error('Please select a file to load.');
    const response = await fetch('/api/studio/autodj/text-load', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ relative_path: relativePath })
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || data.success !== true) {
      throw new Error(data.error || 'Failed to load AutoDJ text file.');
    }
    setStudioAutodjEditorValue(String(data.content || ''));
    setStudioSettingsFeedback(`AutoDJ text loaded: ${data.relative_path || data.filename || relativePath}`, 'success');
    closeStudioAutodjLoadWindow();
  }

  function buildStudioAutodjDefaultFilename(){
    const now = new Date();
    const parts = [
      now.getFullYear(),
      String(now.getMonth() + 1).padStart(2, '0'),
      String(now.getDate()).padStart(2, '0')
    ];
    const timeParts = [
      String(now.getHours()).padStart(2, '0'),
      String(now.getMinutes()).padStart(2, '0'),
      String(now.getSeconds()).padStart(2, '0')
    ];
    return `autodj_${parts.join('')}_${timeParts.join('')}.adj`;
  }

  function handleStudioAutodjSaveError(error){
    console.error('Studio AutoDJ save browser error', error);
    setStudioSettingsFeedback((error && error.message) || 'Failed to open AutoDJ save dialog.', 'error');
  }

  async function openStudioAutodjSaveWindow(){
    if (!els.studioAutodjSaveWindow) return;
    if (els.studioAutodjSaveFilename) {
      const currentValue = els.studioAutodjSaveFilename.value.trim();
      if (!currentValue || /^autodj_\d{8}_\d{6}\.adj$/i.test(currentValue) || currentValue.toLowerCase() === 'autodj.adj') {
        els.studioAutodjSaveFilename.value = buildStudioAutodjDefaultFilename();
      }
    }
    await loadStudioAutodjSaveBrowser('');
    openFloatingWindow(els.studioAutodjSaveWindow);
    if (els.studioAutodjSaveFilename) els.studioAutodjSaveFilename.focus();
  }

  function closeStudioAutodjSaveWindow(){
    closeFloatingWindow(els.studioAutodjSaveWindow);
  }

  async function submitStudioAutodjSave(){
    let filename = els.studioAutodjSaveFilename ? els.studioAutodjSaveFilename.value.trim() : '';
    if (!filename) throw new Error('Filename is required.');
    if (!/\.adj$/i.test(filename)) filename = `${filename}.adj`;
    if (els.studioAutodjSaveFilename) els.studioAutodjSaveFilename.value = filename;
    const response = await fetch('/api/studio/autodj/text-save', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        sub: studioAutodjSaveBrowserState.currentSub || '',
        filename,
        content: getStudioAutodjEditorValue()
      })
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || data.success !== true) {
      throw new Error(data.error || 'Failed to save AutoDJ text file.');
    }
    if (els.studioAutodjSaveFilename) els.studioAutodjSaveFilename.value = data.filename || filename;
    setStudioSettingsFeedback(`AutoDJ text saved: ${data.relative_path || data.filename || filename}`, 'success');
    closeStudioAutodjSaveWindow();
  }

  async function loadStudioAutodjSettings(){
    const response = await fetch('/api/autodj/settings', { credentials: 'same-origin' });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(data.error || `HTTP ${response.status}`);
    }
    const settings = data.settings || {};
    if (els.studioAutodjNoRepeatArtist) els.studioAutodjNoRepeatArtist.value = String(Number(settings.no_repeat_artist_minutes ?? 0));
    if (els.studioAutodjNoRepeatTitle) els.studioAutodjNoRepeatTitle.value = String(Number(settings.no_repeat_title_minutes ?? 0));
    if (els.studioAutodjNoRepeatTrack) els.studioAutodjNoRepeatTrack.value = String(Number(settings.no_repeat_track_minutes ?? 0));
    if (els.studioAutodjKeepQueue) els.studioAutodjKeepQueue.value = String(Number(settings.keep_queue ?? 0));
    setStudioAutodjEditorValue(String(settings.editor_text || ''));
  }

  async function saveStudioAutodjSettings(){
    const payload = {
      no_repeat_artist_minutes: Number(sanitizeStudioNumericInput(els.studioAutodjNoRepeatArtist) || 0),
      no_repeat_title_minutes: Number(sanitizeStudioNumericInput(els.studioAutodjNoRepeatTitle) || 0),
      no_repeat_track_minutes: Number(sanitizeStudioNumericInput(els.studioAutodjNoRepeatTrack) || 0),
      keep_queue: Number(sanitizeStudioNumericInput(els.studioAutodjKeepQueue) || 0),
      editor_text: getStudioAutodjEditorValue()
    };
    const response = await fetch('/api/autodj/settings', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok || data.success !== true) {
      throw new Error(data.error || 'Failed to save AutoDJ settings.');
    }
    return data;
  }

  function openStudioSettings(){
    if (!els.studioSettingsWindow) return;
    switchStudioSettingsSection('general');
    setStudioSettingsFeedback('');
    openFloatingWindow(els.studioSettingsWindow);
    refreshStudioSettingsStatus().catch(() => {});
    hideStudioUsersForms();
    refreshStudioUsers().catch(() => {});
    loadStudioAutodjSettings().catch(error => {
      console.error('Failed to load studio AutoDJ settings', error);
      setStudioSettingsFeedback('Failed to load AutoDJ settings.', 'error');
    });
  }

  function closeStudioSettings(){
    if (!els.studioSettingsWindow) return;
    closeStudioConsoleSocket(true);
    closeFloatingWindow(els.studioSettingsWindow);
  }

  async function applyStudioDspSettingImmediately(){
    const checkbox = els.studioSettingsDspEnabled;
    if (!checkbox || checkbox.disabled) return;

    const requestedEnabled = Boolean(checkbox.checked);
    const persistedEnabled = checkbox.dataset.persistedEnabled === '1';
    const saveWasDisabled = Boolean(els.studioSettingsSave && els.studioSettingsSave.disabled);
    checkbox.disabled = true;
    if (els.studioSettingsSave) els.studioSettingsSave.disabled = true;
    checkbox.setAttribute('aria-busy', 'true');
    setStudioSettingsFeedback(requestedEnabled ? 'Enabling DSP...' : 'Disabling DSP...');

    try {
      const response = await fetch('/api/studio/settings/dsp', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled: requestedEnabled })
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || !data.ok) {
        throw new Error(data.error || 'Failed to apply DSP setting.');
      }

      const appliedEnabled = Boolean(data.dsp_enabled);
      checkbox.checked = appliedEnabled;
      checkbox.dataset.persistedEnabled = appliedEnabled ? '1' : '0';
      setStudioSettingsFeedback(
        data.message || (appliedEnabled ? 'DSP enabled.' : 'DSP disabled.'),
        'success'
      );
      refreshStudioSettingsStatus().catch(() => {});
    } catch (error) {
      checkbox.checked = persistedEnabled;
      setStudioSettingsFeedback(error.message || 'Failed to apply DSP setting.', 'error');
    } finally {
      checkbox.disabled = false;
      if (els.studioSettingsSave) els.studioSettingsSave.disabled = saveWasDisabled;
      checkbox.removeAttribute('aria-busy');
    }
  }

  async function saveStudioSettings(){
    if (!els.studioSettingsSave) return;
    const activePanel = document.querySelector('[data-settings-panel].is-active');
    const activeSection = activePanel ? String(activePanel.dataset.settingsPanel || '').toLowerCase() : 'general';
    els.studioSettingsSave.disabled = true;
    setStudioSettingsFeedback('Saving settings...');
    try {
      if (activeSection === 'autodj') {
        await saveStudioAutodjSettings();
        setStudioSettingsFeedback('AutoDJ settings updated successfully.', 'success');
        loadStudioAutodjSettings().catch(() => {});
        closeStudioSettings();
        return;
      }

      if (activeSection !== 'general' && activeSection !== 'crossfade') {
        setStudioSettingsFeedback('', '');
        closeStudioSettings();
        return;
      }

      const activeForm = activePanel ? activePanel.querySelector('form') : null;
      const settingsForm = activeForm || els.studioSettingsForm;
      if (!settingsForm) {
        throw new Error('Settings form is not available.');
      }
      const formData = new FormData(settingsForm);
      const response = await fetch('/api/studio/settings', {
        method: 'POST',
        body: formData
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok || !data.ok) {
        throw new Error(data.error || 'Failed to save settings.');
      }
      await refreshStudioStations();
      if (data && data.station && data.station.db_filename) {
        document.body.dataset.stationKey = String(data.station.db_filename);
      }
      refreshStudioHeaderStationSwitcher(data && data.station ? data.station.name : '');
      setStudioSettingsFeedback(data.message || 'Settings updated successfully.', 'success');
      refreshStudioSettingsStatus().catch(() => {});
      closeStudioSettings();
    } catch (error) {
      setStudioSettingsFeedback(error.message || 'Failed to save settings.', 'error');
    } finally {
      els.studioSettingsSave.disabled = false;
    }
  }

  function initializeStudioSettings(){
    const win = els.studioSettingsWindow;
    if (!win) return;
    const titlebar = win.querySelector('.studio-floating-window__titlebar');
    const handle = win.querySelector('.panel-resize-handle');
    let dragState = null;
    let resizeState = null;

    function onPointerMove(event){
      if (dragState){
        applyFloatingWindowRect(
          win,
          dragState.startLeft + (event.clientX - dragState.startClientX),
          dragState.startTop + (event.clientY - dragState.startClientY),
          dragState.width,
          dragState.height
        );
      } else if (resizeState){
        applyFloatingWindowRect(
          win,
          resizeState.left,
          resizeState.top,
          resizeState.startWidth + (event.clientX - resizeState.startClientX),
          resizeState.startHeight + (event.clientY - resizeState.startClientY)
        );
      }
    }

    function endPointerInteraction(){
      dragState = null;
      resizeState = null;
      win.classList.remove('is-dragging');
      win.classList.remove('is-resizing');
      document.removeEventListener('pointermove', onPointerMove);
      document.removeEventListener('pointerup', endPointerInteraction);
      document.removeEventListener('pointercancel', endPointerInteraction);
    }

    if (titlebar){
      titlebar.addEventListener('pointerdown', event => {
        if (event.button !== 0) return;
        if (event.target.closest('button, a, input, select, textarea')) return;
        event.preventDefault();
        bringWindowToFront(win);
        dragState = {
          startClientX: event.clientX,
          startClientY: event.clientY,
          startLeft: Number.parseInt(win.style.left || '80', 10),
          startTop: Number.parseInt(win.style.top || '96', 10),
          width: win.offsetWidth,
          height: win.offsetHeight
        };
        win.classList.add('is-dragging');
        document.addEventListener('pointermove', onPointerMove);
        document.addEventListener('pointerup', endPointerInteraction);
        document.addEventListener('pointercancel', endPointerInteraction);
      });
    }

    if (handle){
      handle.addEventListener('pointerdown', event => {
        if (event.button !== 0) return;
        event.preventDefault();
        event.stopPropagation();
        bringWindowToFront(win);
        resizeState = {
          startClientX: event.clientX,
          startClientY: event.clientY,
          left: Number.parseInt(win.style.left || '80', 10),
          top: Number.parseInt(win.style.top || '96', 10),
          startWidth: win.offsetWidth,
          startHeight: win.offsetHeight
        };
        win.classList.add('is-resizing');
        document.addEventListener('pointermove', onPointerMove);
        document.addEventListener('pointerup', endPointerInteraction);
        document.addEventListener('pointercancel', endPointerInteraction);
      });
    }

    if (els.studioSettingsOpen) els.studioSettingsOpen.addEventListener('click', openStudioSettings);
    if (els.studioSettingsClose) els.studioSettingsClose.addEventListener('click', closeStudioSettings);
    if (els.studioSettingsFooterClose) els.studioSettingsFooterClose.addEventListener('click', closeStudioSettings);
    if (els.studioSettingsSave) els.studioSettingsSave.addEventListener('click', saveStudioSettings);
    if (els.studioSettingsDspEnabled) {
      els.studioSettingsDspEnabled.dataset.persistedEnabled = els.studioSettingsDspEnabled.checked ? '1' : '0';
      els.studioSettingsDspEnabled.addEventListener('change', () => {
        applyStudioDspSettingImmediately().catch(() => {});
      });
    }
    if (els.studioConsolePause) {
      els.studioConsolePause.addEventListener('click', () => setStudioConsolePaused(!studioConsoleState.paused));
    }
    if (els.studioConsoleClear) {
      els.studioConsoleClear.addEventListener('click', clearStudioConsoleView);
    }
    if (els.studioSettingsForm) {
      els.studioSettingsForm.addEventListener('submit', event => {
        event.preventDefault();
        saveStudioSettings().catch(() => {});
      });
    }
    [
      els.studioAutodjNoRepeatArtist,
      els.studioAutodjNoRepeatTitle,
      els.studioAutodjNoRepeatTrack,
      els.studioAutodjKeepQueue
    ].forEach(bindStudioNumericOnly);
    if (els.studioAutodjAddCategory) {
      els.studioAutodjAddCategory.addEventListener('click', () => {
        openStudioAutodjCategoryWindow().catch(error => {
          console.error('Failed to open studio AutoDJ category window', error);
          setStudioSettingsFeedback(error.message || 'Failed to load categories.', 'error');
        });
      });
    }
    if (els.studioAutodjCategoryClose) els.studioAutodjCategoryClose.addEventListener('click', event => {
      event.preventDefault();
      event.stopPropagation();
      closeStudioAutodjCategoryWindow();
    });
    if (els.studioAutodjCategoryCancel) els.studioAutodjCategoryCancel.addEventListener('click', event => {
      event.preventDefault();
      event.stopPropagation();
      closeStudioAutodjCategoryWindow();
    });
    if (els.studioAutodjCategoryOk) els.studioAutodjCategoryOk.addEventListener('click', event => {
      event.preventDefault();
      event.stopPropagation();
      if (typeof event.stopImmediatePropagation === 'function') event.stopImmediatePropagation();
      confirmStudioAutodjCategoryWindow(event.currentTarget);
    });
    if (els.studioAutodjLoad) {
      els.studioAutodjLoad.addEventListener('click', () => {
        openStudioAutodjLoadWindow().catch(handleStudioAutodjLoadError);
      });
    }
    if (els.studioAutodjLoadClose) els.studioAutodjLoadClose.addEventListener('click', closeStudioAutodjLoadWindow);
    if (els.studioAutodjLoadCancel) els.studioAutodjLoadCancel.addEventListener('click', closeStudioAutodjLoadWindow);
    if (els.studioAutodjLoadConfirm) {
      els.studioAutodjLoadConfirm.addEventListener('click', async () => {
        els.studioAutodjLoadConfirm.disabled = true;
        try {
          await submitStudioAutodjLoad();
        } catch (error) {
          handleStudioAutodjLoadError(error);
        } finally {
          els.studioAutodjLoadConfirm.disabled = false;
        }
      });
    }
    if (els.studioAutodjSave) {
      els.studioAutodjSave.addEventListener('click', () => {
        openStudioAutodjSaveWindow().catch(handleStudioAutodjSaveError);
      });
    }
    if (els.studioAutodjSaveClose) els.studioAutodjSaveClose.addEventListener('click', closeStudioAutodjSaveWindow);
    if (els.studioAutodjSaveCancel) els.studioAutodjSaveCancel.addEventListener('click', closeStudioAutodjSaveWindow);
    if (els.studioAutodjSaveConfirm) {
      els.studioAutodjSaveConfirm.addEventListener('click', async () => {
        els.studioAutodjSaveConfirm.disabled = true;
        try {
          await submitStudioAutodjSave();
        } catch (error) {
          handleStudioAutodjSaveError(error);
        } finally {
          els.studioAutodjSaveConfirm.disabled = false;
        }
      });
    }
    if (els.studioUsersAdd) {
      els.studioUsersAdd.addEventListener('click', showStudioUsersAddForm);
    }
    if (els.studioUsersAddForm) {
      els.studioUsersAddForm.addEventListener('submit', event => {
        event.preventDefault();
        submitStudioAddUser().catch(() => {});
      });
    }
    if (els.studioUsersPasswordForm) {
      els.studioUsersPasswordForm.addEventListener('submit', event => {
        event.preventDefault();
        submitStudioPasswordChange().catch(() => {});
      });
    }
    if (els.studioUsersDeleteConfirm) {
      els.studioUsersDeleteConfirm.addEventListener('click', () => {
        const userId = studioUsersState.deleteUserId;
        if (!userId) return;
        deleteStudioUser(userId).catch(() => {});
      });
    }
    if (els.studioUsersList) {
      els.studioUsersList.addEventListener('click', event => {
        const actionButton = event.target.closest('[data-user-action]');
        if (!actionButton) return;
        const action = actionButton.dataset.userAction || '';
        const userId = Number(actionButton.dataset.userId || 0);
        if (!userId) return;
        if (action === 'password') showStudioUsersPasswordForm(userId);
        else if (action === 'delete') showStudioUsersDeleteModal(userId);
      });
    }
    if (els.studioStationsAdd) {
      els.studioStationsAdd.addEventListener('click', openStudioAddStationModal);
    }
    if (els.studioAddStationClose) {
      els.studioAddStationClose.addEventListener('click', closeStudioAddStationModal);
    }
    if (els.studioAddStationCancel) {
      els.studioAddStationCancel.addEventListener('click', closeStudioAddStationModal);
    }
    if (els.studioAddStationSave) {
      els.studioAddStationSave.addEventListener('click', () => {
        submitStudioAddStation().catch(() => {});
      });
    }
    if (els.studioAddStationForm) {
      els.studioAddStationForm.addEventListener('submit', event => {
        event.preventDefault();
        submitStudioAddStation().catch(() => {});
      });
    }
    if (els.studioAddStationBackdrop) {
      els.studioAddStationBackdrop.addEventListener('click', event => {
        if (event.target === els.studioAddStationBackdrop) closeStudioAddStationModal();
      });
    }
    if (els.studioStationDeleteConfirmNo) {
      els.studioStationDeleteConfirmNo.addEventListener('click', hideStudioStationDeleteConfirmModal);
    }
    if (els.studioStationDeleteConfirmClose) {
      els.studioStationDeleteConfirmClose.addEventListener('click', hideStudioStationDeleteConfirmModal);
    }
    if (els.studioStationDeleteConfirmYes) {
      els.studioStationDeleteConfirmYes.addEventListener('click', () => {
        if (els.studioStationDeleteConfirmBackdrop) els.studioStationDeleteConfirmBackdrop.dataset.result = 'yes';
      });
    }
    if (els.studioStationRenameNo) {
      els.studioStationRenameNo.addEventListener('click', hideStudioStationRenameModal);
    }
    if (els.studioStationRenameClose) {
      els.studioStationRenameClose.addEventListener('click', hideStudioStationRenameModal);
    }
    if (els.studioStationRenameYes) {
      els.studioStationRenameYes.addEventListener('click', () => {
        submitStudioStationRename().catch(error => {
          if (els.studioStationRenameError) {
            els.studioStationRenameError.textContent = error.message || 'Failed to rename station.';
            els.studioStationRenameError.style.display = 'block';
          }
        });
      });
    }
    if (els.studioStationRenameInput) {
      els.studioStationRenameInput.addEventListener('input', () => {
        if (els.studioStationRenameError) {
          els.studioStationRenameError.style.display = 'none';
          els.studioStationRenameError.textContent = '';
        }
      });
      els.studioStationRenameInput.addEventListener('keydown', event => {
        if (event.key === 'Enter') {
          event.preventDefault();
          submitStudioStationRename().catch(error => {
            if (els.studioStationRenameError) {
              els.studioStationRenameError.textContent = error.message || 'Failed to rename station.';
              els.studioStationRenameError.style.display = 'block';
            }
          });
        }
      });
    }
    if (els.studioStationDeletePasswordNo) {
      els.studioStationDeletePasswordNo.addEventListener('click', hideStudioStationDeletePasswordModal);
    }
    if (els.studioStationDeletePasswordClose) {
      els.studioStationDeletePasswordClose.addEventListener('click', hideStudioStationDeletePasswordModal);
    }
    if (els.studioStationDeletePasswordYes) {
      els.studioStationDeletePasswordYes.addEventListener('click', () => {
        if (els.studioStationDeletePasswordBackdrop) els.studioStationDeletePasswordBackdrop.dataset.result = 'yes';
      });
    }
    if (els.studioStationDeletePasswordInput) {
      els.studioStationDeletePasswordInput.addEventListener('input', () => {
        if (els.studioStationDeletePasswordError) {
          els.studioStationDeletePasswordError.style.display = 'none';
          els.studioStationDeletePasswordError.textContent = '';
        }
      });
    }

    if (els.studioStationsList) {
      els.studioStationsList.addEventListener('click', event => {
        const button = event.target.closest('[data-station-action]');
        if (!button) return;
        const action = String(button.dataset.stationAction || '');
        const stationKey = String(button.dataset.stationKey || '').trim();
        const stationName = String(button.dataset.stationName || stationKey || 'Station');
        if (!stationKey) return;
        if (action === 'rename') openStudioStationRenameModal(stationKey, stationName);
        if (action === 'delete') deleteStudioStation(stationKey, stationName).catch(error => setStudioStationsFeedback(error.message || 'Delete failed.', 'error'));
      });
    }
    document.querySelectorAll('[data-settings-section]').forEach(btn => {
      btn.addEventListener('click', () => switchStudioSettingsSection(btn.dataset.settingsSection || 'general'));
    });
    document.addEventListener('keydown', event => {
      if (event.key === 'Escape') {
        if (els.studioAutodjCategoryWindow && els.studioAutodjCategoryWindow.getAttribute('aria-hidden') !== 'true' && els.studioAutodjCategoryWindow.style.display !== 'none') {
          event.preventDefault();
          event.stopPropagation();
          if (typeof event.stopImmediatePropagation === 'function') event.stopImmediatePropagation();
          closeStudioAutodjCategoryWindow();
          return;
        }
        if (els.studioStationRenameBackdrop && els.studioStationRenameBackdrop.getAttribute('aria-hidden') !== 'true' && els.studioStationRenameBackdrop.style.display !== 'none') {
          hideStudioStationRenameModal();
          return;
        }
        if (els.studioStationDeletePasswordBackdrop && els.studioStationDeletePasswordBackdrop.getAttribute('aria-hidden') !== 'true' && els.studioStationDeletePasswordBackdrop.style.display !== 'none') {
          hideStudioStationDeletePasswordModal();
          return;
        }
        if (els.studioStationDeleteConfirmBackdrop && els.studioStationDeleteConfirmBackdrop.getAttribute('aria-hidden') !== 'true' && els.studioStationDeleteConfirmBackdrop.style.display !== 'none') {
          hideStudioStationDeleteConfirmModal();
          return;
        }
        if (els.studioAddStationBackdrop && els.studioAddStationBackdrop.classList.contains('active')) {
          closeStudioAddStationModal();
          return;
        }
        if (win && win.getAttribute('aria-hidden') !== 'true' && win.style.display !== 'none') {
          closeStudioSettings();
        }
        return;
      }
      if (event.key === 'Enter') {
        if (els.studioStationDeletePasswordBackdrop && els.studioStationDeletePasswordBackdrop.getAttribute('aria-hidden') !== 'true' && els.studioStationDeletePasswordBackdrop.style.display !== 'none') {
          if (els.studioStationDeletePasswordBackdrop) els.studioStationDeletePasswordBackdrop.dataset.result = 'yes';
          return;
        }
        if (els.studioStationDeleteConfirmBackdrop && els.studioStationDeleteConfirmBackdrop.getAttribute('aria-hidden') !== 'true' && els.studioStationDeleteConfirmBackdrop.style.display !== 'none') {
          if (els.studioStationDeleteConfirmBackdrop) els.studioStationDeleteConfirmBackdrop.dataset.result = 'yes';
        }
      }
    });
    win.addEventListener('pointerdown', () => bringWindowToFront(win));
    window.addEventListener('resize', () => {
      if (win.getAttribute('aria-hidden') === 'true') return;
      applyFloatingWindowRect(
        win,
        Number.parseInt(win.style.left || '80', 10),
        Number.parseInt(win.style.top || '96', 10),
        win.offsetWidth,
        win.offsetHeight
      );
    });
  }

  function initializeStudioAutodjCategoryWindow(){
    const win = els.studioAutodjCategoryWindow;
    if (!win) return;
    const titlebar = win.querySelector('.studio-floating-window__titlebar');
    const handle = win.querySelector('.panel-resize-handle');
    let dragState = null;
    let resizeState = null;

    function onPointerMove(event){
      if (dragState){
        applyFloatingWindowRect(
          win,
          dragState.startLeft + (event.clientX - dragState.startClientX),
          dragState.startTop + (event.clientY - dragState.startClientY),
          dragState.width,
          dragState.height
        );
      } else if (resizeState){
        applyFloatingWindowRect(
          win,
          resizeState.left,
          resizeState.top,
          resizeState.startWidth + (event.clientX - resizeState.startClientX),
          resizeState.startHeight + (event.clientY - resizeState.startClientY)
        );
      }
    }

    function endPointerInteraction(){
      dragState = null;
      resizeState = null;
      win.classList.remove('is-dragging');
      win.classList.remove('is-resizing');
      document.removeEventListener('pointermove', onPointerMove);
      document.removeEventListener('pointerup', endPointerInteraction);
      document.removeEventListener('pointercancel', endPointerInteraction);
    }

    if (titlebar){
      titlebar.addEventListener('pointerdown', event => {
        if (event.button !== 0) return;
        if (event.target.closest('button, a, input, select, textarea')) return;
        event.preventDefault();
        bringWindowToFront(win);
        dragState = {
          startClientX: event.clientX,
          startClientY: event.clientY,
          startLeft: Number.parseInt(win.style.left || '220', 10),
          startTop: Number.parseInt(win.style.top || '130', 10),
          width: win.offsetWidth,
          height: win.offsetHeight
        };
        win.classList.add('is-dragging');
        document.addEventListener('pointermove', onPointerMove);
        document.addEventListener('pointerup', endPointerInteraction);
        document.addEventListener('pointercancel', endPointerInteraction);
      });
    }

    if (handle){
      handle.addEventListener('pointerdown', event => {
        if (event.button !== 0) return;
        event.preventDefault();
        event.stopPropagation();
        bringWindowToFront(win);
        resizeState = {
          startClientX: event.clientX,
          startClientY: event.clientY,
          left: Number.parseInt(win.style.left || '220', 10),
          top: Number.parseInt(win.style.top || '130', 10),
          startWidth: win.offsetWidth,
          startHeight: win.offsetHeight
        };
        win.classList.add('is-resizing');
        document.addEventListener('pointermove', onPointerMove);
        document.addEventListener('pointerup', endPointerInteraction);
        document.addEventListener('pointercancel', endPointerInteraction);
      });
    }

    win.addEventListener('pointerdown', () => bringWindowToFront(win));
    window.addEventListener('resize', () => {
      if (win.getAttribute('aria-hidden') === 'true') return;
      applyFloatingWindowRect(
        win,
        Number.parseInt(win.style.left || '220', 10),
        Number.parseInt(win.style.top || '130', 10),
        win.offsetWidth,
        win.offsetHeight
      );
    });
  }


  const PLAYLIST_BROWSER_MODES = {
    FILES: 'files',
    DIRECTORIES: 'directories'
  };
  window.PLAYLIST_BROWSER_MODES = PLAYLIST_BROWSER_MODES;

  function getActiveStationKey(){
    const direct = document.body && document.body.dataset ? document.body.dataset.stationKey : '';
    if (direct) return String(direct);
    const shellKey = shell && shell.dataset ? (shell.dataset.stationKey || shell.getAttribute('data-station-key') || '') : '';
    if (shellKey) return String(shellKey);
    const meta = document.querySelector('meta[name="station-key"]');
    if (meta && meta.content) return String(meta.content);
    return '';
  }

  function getPanelStateMap(layoutName){
    if (!panelStateByLayout[layoutName]) panelStateByLayout[layoutName] = {};
    return panelStateByLayout[layoutName];
  }

  function readPanelSizes(layoutName){
    const state = getPanelStateMap(layoutName);
    const sizes = {};
    Object.entries(state).forEach(([panelName, item]) => {
      sizes[panelName] = {
        width: item && item.width ? item.width : null,
        height: item && item.height ? item.height : null
      };
    });
    return sizes;
  }

  function readPanelPositions(layoutName){
    const state = getPanelStateMap(layoutName);
    const positions = {};
    Object.entries(state).forEach(([panelName, item]) => {
      positions[panelName] = {
        x: item && item.x ? item.x : 0,
        y: item && item.y ? item.y : 0
      };
    });
    return positions;
  }

  function updatePanelState(layoutName, panelName, patch){
    const state = getPanelStateMap(layoutName);
    state[panelName] = {
      ...(state[panelName] || {}),
      ...(patch || {})
    };
  }

  async function loadLayoutStateFromServer(){
    try{
      const data = await jsonFetch('/api/studio/layout-state');
      preferredLayoutName = data.preferred_layout || 'layout-1';
      const layouts = data.layouts || {};
      panelStateByLayout['layout-1'] = layouts['layout-1'] || {};
      panelStateByLayout['layout-2'] = layouts['layout-2'] || {};
    }catch(err){
      console.error('Unable to load studio layout state', err);
    }
  }

  async function saveLayoutStateToServer(layoutName){
    try{
      await jsonFetch('/api/studio/layout-state', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({
          layout: layoutName,
          preferred_layout: preferredLayoutName,
          panels: getPanelStateMap(layoutName)
        })
      });
    }catch(err){
      console.error('Unable to save studio layout state', err);
    }
  }

  function scheduleLayoutStateSave(layoutName){
    clearTimeout(saveStateTimer);
    saveStateTimer = setTimeout(() => {
      saveLayoutStateToServer(layoutName);
    }, 250);
  }

  function applySavedPanelSizes(layoutName){
    const sizes = readPanelSizes(layoutName);
    panelElements.forEach(panel => {
      const panelName = panel.dataset.panel;
      const saved = sizes[panelName];
      panel.style.width = saved && saved.width ? `${saved.width}px` : '';
      panel.style.height = saved && saved.height ? `${saved.height}px` : '';
    });
    updateStudioWorkspaceScale();
  }

  function applySavedPanelPositions(layoutName){
    const positions = readPanelPositions(layoutName);
    panelElements.forEach(panel => {
      const saved = positions[panel.dataset.panel];
      if (saved && (saved.x || saved.y)) {
        applyPanelTransform(panel, saved.x || 0, saved.y || 0);
      } else {
        resetPanelTransform(panel);
      }
    });
    updateStudioWorkspaceScale();
  }

  function readPlaylistSplitterBasis(layoutName){
    const state = getPanelStateMap(layoutName);
    const saved = state['playlist-splitter'];
    const height = saved && saved.height ? Number(saved.height) : 0;
    return Number.isFinite(height) && height > 0 ? height : null;
  }

  function applySavedPlaylistSplitter(layoutName){
    if (!els.playlistTreeSection || !els.playlistTracksSection || !els.playlistSplitter) return;
    const savedHeight = readPlaylistSplitterBasis(layoutName);
    if (savedHeight && savedHeight > 0) {
      els.playlistTreeSection.style.flexBasis = `${Math.round(savedHeight)}px`;
    } else {
      els.playlistTreeSection.style.flexBasis = '';
    }
  }

  function persistPlaylistSplitter(){
    if (!els.playlistTreeSection) return;
    const layoutName = shell.dataset.layout || 'layout-1';
    updatePanelState(layoutName, 'playlist-splitter', {
      height: Math.round(els.playlistTreeSection.getBoundingClientRect().height)
    });
    scheduleLayoutStateSave(layoutName);
  }

  function persistPanelSize(panel){
    const layoutName = shell.dataset.layout || 'layout-1';
    updatePanelState(layoutName, panel.dataset.panel, {
      width: Math.round(panel.offsetWidth),
      height: Math.round(panel.offsetHeight)
    });
    scheduleLayoutStateSave(layoutName);
  }

  function persistPanelPosition(panel){
    const layoutName = shell.dataset.layout || 'layout-1';
    updatePanelState(layoutName, panel.dataset.panel, {
      x: Math.round(Number(panel.dataset.translateX || 0)),
      y: Math.round(Number(panel.dataset.translateY || 0))
    });
    scheduleLayoutStateSave(layoutName);
  }

  function getPanelBounds(panel){
    const workspace = panel.parentElement;
    const titlebar = panel.querySelector('.panel-titlebar');
    const minVisibleWidth = Math.min(Math.max(titlebar ? titlebar.offsetWidth : 160, 120), 220);
    const minVisibleHeight = titlebar ? titlebar.offsetHeight : 42;
    const baseLeft = panel.offsetLeft;
    const baseTop = panel.offsetTop;
    const width = panel.offsetWidth;
    const height = panel.offsetHeight;
    const workspaceWidth = workspace ? workspace.clientWidth : width;
    const workspaceHeight = workspace ? workspace.clientHeight : height;

    return {
      minX: Math.min(0, minVisibleWidth - baseLeft - width),
      maxX: Math.max(0, workspaceWidth - baseLeft - minVisibleWidth),
      minY: -baseTop,
      maxY: Math.max(0, workspaceHeight - baseTop - minVisibleHeight)
    };
  }

  function clampPanelPosition(panel, x, y){
    const bounds = getPanelBounds(panel);
    return {
      x: Math.min(bounds.maxX, Math.max(bounds.minX, x)),
      y: Math.min(bounds.maxY, Math.max(bounds.minY, y))
    };
  }

  function snapToPanelGrid(value){
    const numeric = Number(value || 0);
    return Math.round(numeric / PANEL_GRID_SIZE) * PANEL_GRID_SIZE;
  }

  function applyPanelTransform(panel, x, y){
    const snappedX = snapToPanelGrid(x);
    const snappedY = snapToPanelGrid(y);
    const next = clampPanelPosition(panel, snappedX, snappedY);
    panel.dataset.translateX = String(Math.round(next.x));
    panel.dataset.translateY = String(Math.round(next.y));
    panel.style.transform = `translate(${Math.round(next.x)}px, ${Math.round(next.y)}px)`;
  }

  function resetPanelTransform(panel){
    panel.style.transform = '';
    panel.dataset.translateX = '0';
    panel.dataset.translateY = '0';
  }

  function normalizePanelPosition(panel){
    applyPanelTransform(panel, Number(panel.dataset.translateX || 0), Number(panel.dataset.translateY || 0));
  }

  function normalizeVisiblePanelPositions(){
    panelElements.forEach(panel => {
      if (window.getComputedStyle(panel).display === 'none') return;
      normalizePanelPosition(panel);
      persistPanelPosition(panel);
    });
    updateStudioWorkspaceScale();
  }



  let layoutContextMenuLayoutName = null;

  function hideLayoutContextMenu(){
    if (!els.layoutContextMenu) return;
    els.layoutContextMenu.hidden = true;
    els.layoutContextMenu.style.left = '';
    els.layoutContextMenu.style.top = '';
    layoutContextMenuLayoutName = null;
  }

  function showLayoutContextMenu(layoutName, x, y){
    const menu = els.layoutContextMenu;
    if (!menu) return;
    layoutContextMenuLayoutName = layoutName || 'layout-1';
    menu.hidden = false;
    const padding = 8;
    const vw = window.innerWidth || document.documentElement.clientWidth || 0;
    const vh = window.innerHeight || document.documentElement.clientHeight || 0;
    const rect = menu.getBoundingClientRect();
    const left = Math.max(padding, Math.min(x, Math.max(padding, vw - rect.width - padding)));
    const top = Math.max(padding, Math.min(y, Math.max(padding, vh - rect.height - padding)));
    menu.style.left = `${Math.round(left)}px`;
    menu.style.top = `${Math.round(top)}px`;
  }

  async function saveLayoutTemplate(layoutName){
    const normalized = layoutName || layoutContextMenuLayoutName || shell.dataset.layout || 'layout-1';
    await jsonFetch('/api/studio/layout-template/save', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        layout: normalized,
        panels: getPanelStateMap(normalized)
      })
    });
  }

  async function loadLayoutTemplate(layoutName){
    const normalized = layoutName || layoutContextMenuLayoutName || shell.dataset.layout || 'layout-1';
    const data = await jsonFetch('/api/studio/layout-template/load', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        layout: normalized,
        preferred_layout: shell.dataset.layout || normalized
      })
    });
    const layouts = data.layouts || {};
    panelStateByLayout['layout-1'] = layouts['layout-1'] || panelStateByLayout['layout-1'] || {};
    panelStateByLayout['layout-2'] = layouts['layout-2'] || panelStateByLayout['layout-2'] || {};
    if ((shell.dataset.layout || 'layout-1') === normalized){
      applySavedPanelSizes(normalized);
      applySavedPanelPositions(normalized);
      applySavedPlaylistSplitter(normalized);
      requestAnimationFrame(() => normalizeVisiblePanelPositions());
    }
  }

  function initializeLayoutContextMenu(){
    const menu = els.layoutContextMenu;
    const layoutButtons = document.querySelectorAll('.layout-btn');
    if (!menu || !layoutButtons.length) return;

    layoutButtons.forEach(btn => {
      btn.addEventListener('contextmenu', event => {
        event.preventDefault();
        event.stopPropagation();
        showLayoutContextMenu(btn.dataset.layoutTarget || 'layout-1', event.clientX, event.clientY);
      });
    });

    menu.querySelectorAll('[data-layout-menu-action]').forEach(btn => {
      btn.addEventListener('click', async event => {
        event.preventDefault();
        event.stopPropagation();
        const action = btn.dataset.layoutMenuAction;
        const layoutName = layoutContextMenuLayoutName || shell.dataset.layout || 'layout-1';
        hideLayoutContextMenu();
        try{
          if (action === 'save') await saveLayoutTemplate(layoutName);
          if (action === 'default') await loadLayoutTemplate(layoutName);
        }catch(err){
          console.error('Unable to handle layout context menu action', err);
        }
      });
    });

    document.addEventListener('click', event => {
      if (!menu.hidden && !menu.contains(event.target)) hideLayoutContextMenu();
    });
    document.addEventListener('keydown', event => {
      if (event.key === 'Escape') hideLayoutContextMenu();
    });
    window.addEventListener('blur', hideLayoutContextMenu);
    window.addEventListener('resize', hideLayoutContextMenu);
    document.addEventListener('scroll', hideLayoutContextMenu, true);
  }
  function saveLayout(name){
    closePlaylistContextMenu();
    hideLayoutContextMenu();
    shell.dataset.layout = name;
    preferredLayoutName = name;
    document.querySelectorAll('.layout-btn').forEach(btn => {
      const active = btn.dataset.layoutTarget === name;
      btn.classList.toggle('active', active);
      btn.setAttribute('aria-selected', active ? 'true' : 'false');
    });
    applySavedPanelSizes(name);
    applySavedPanelPositions(name);
    applySavedPlaylistSplitter(name);
    requestAnimationFrame(() => normalizeVisiblePanelPositions());
    scheduleLayoutStateSave(name);
  }

  function escapeHtml(value){
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function formatSeconds(totalSeconds){
    const sec = Math.max(0, Math.floor(Number(totalSeconds) || 0));
    const m = Math.floor(sec / 60);
    const s = sec % 60;
    return `${m}:${String(s).padStart(2, '0')}`;
  }

  function formatClockTime(date){
    return date.toLocaleTimeString('en-GB', {hour12: false});
  }

  function formatQueueSummary(totalSeconds, trackCount){
    const sec = Math.max(0, Math.floor(Number(totalSeconds) || 0));
    const days = Math.floor(sec / 86400);
    const hours = Math.floor((sec % 86400) / 3600);
    const minutes = Math.floor((sec % 3600) / 60);
    const seconds = sec % 60;
    const label = trackCount === 1 ? 'track' : 'tracks';
    return `${trackCount} ${label} (${days}d, ${String(hours).padStart(2, '0')}:${String(minutes).padStart(2, '0')}:${String(seconds).padStart(2, '0')})`;
  }

  async function jsonFetch(url, options){
    const resp = await fetch(url, options || {cache: 'no-store'});
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
    return await resp.json();
  }

  function renderList(target, items, emptyText, emptyClassName){
    if (!target) return;
    if (!items || !items.length){
      if (emptyClassName){
        target.innerHTML = `<div class="${escapeHtml(emptyClassName)}">${escapeHtml(emptyText)}</div>`;
      }else{
        target.innerHTML = `<div class="studio-row studio-row--two"><div class="studio-row__title">${escapeHtml(emptyText)}</div></div>`;
      }
      return;
    }
    target.innerHTML = items.join('');
  }

  function selectAllQueueItems(){
    const queueIds = currentQueueItems.map(item => String(item && item.id != null ? item.id : '')).filter(Boolean);
    if (!queueIds.length) return;
    selectedQueueIds.clear();
    queueIds.forEach(queueId => selectedQueueIds.add(queueId));
    lastSelectedQueueId = queueIds[queueIds.length - 1] || null;
    renderQueueTable(currentQueueItems, currentQueueEmptyText, currentQueueItems.reduce((sum, item) => sum + Math.max(0, Number(item && item.cue_duration_seconds) || 0), 0));
  }

  function clearQueueDropIndicator(){
    if (queueDropIndicator && queueDropIndicator.row) {
      queueDropIndicator.row.classList.remove('is-drop-before', 'is-drop-after');
    }
    queueDropIndicator = null;
  }

  function getQueueDropPlacement(row, clientY){
    const rect = row.getBoundingClientRect();
    const midpoint = rect.top + (rect.height / 2);
    return clientY < midpoint ? 'before' : 'after';
  }

  function applyQueueDropIndicator(row, placement, insertIndex){
    if (!row) return;
    if (queueDropIndicator && queueDropIndicator.row === row && queueDropIndicator.placement === placement && queueDropIndicator.insertIndex === insertIndex) return;
    clearQueueDropIndicator();
    row.classList.add(placement === 'before' ? 'is-drop-before' : 'is-drop-after');
    queueDropIndicator = { row, placement, insertIndex };
  }

  function resolveQueueDropDestination(items, sourceId, targetId, placement){
    const list = Array.isArray(items) ? items.slice() : [];
    const fromIndex = list.findIndex(item => String(item.id) === String(sourceId));
    const targetIndex = list.findIndex(item => String(item.id) === String(targetId));
    if (fromIndex === -1 || targetIndex === -1 || fromIndex === targetIndex) return null;

    const reduced = list.filter((_item, index) => index !== fromIndex);
    const reducedTargetIndex = targetIndex - (fromIndex < targetIndex ? 1 : 0);
    let insertIndex = reducedTargetIndex + (placement === 'after' ? 1 : 0);
    insertIndex = Math.max(0, Math.min(reduced.length, insertIndex));

    // Reinserting at the source slot is a no-op. Do not advertise it as a valid drop target.
    if (insertIndex === fromIndex) return null;

    const canonicalItem = insertIndex < reduced.length ? reduced[insertIndex] : reduced[reduced.length - 1];
    if (!canonicalItem) return null;
    return {
      insertIndex,
      targetId: String(canonicalItem.id),
      placement: insertIndex < reduced.length ? 'before' : 'after'
    };
  }

  function findQueueRow(queueId){
    if (!els.queueList) return null;
    return Array.from(els.queueList.querySelectorAll('[data-queue-id]')).find(row => String(row.dataset.queueId || '') === String(queueId)) || null;
  }

  function moveQueueItem(items, sourceId, insertIndex){
    const list = Array.isArray(items) ? items.slice() : [];
    const fromIndex = list.findIndex(item => String(item.id) === String(sourceId));
    if (fromIndex === -1) return list;
    const [moved] = list.splice(fromIndex, 1);
    const safeInsertIndex = Math.max(0, Math.min(list.length, Number(insertIndex)));
    list.splice(safeInsertIndex, 0, moved);
    return list;
  }

  async function persistQueueOrder(){
    const order = currentQueueItems.map(item => item.id);
    queueReorderInFlight = true;
    try {
      await jsonFetch('/api/queue/reorder', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({queue_ids: order})
      });
    } finally {
      queueReorderInFlight = false;
    }
  }

  async function handleQueueDrop(sourceId, insertIndex){
    if (!sourceId || !Number.isInteger(insertIndex)) return;
    const nextItems = moveQueueItem(currentQueueItems, sourceId, insertIndex);
    const changed = nextItems.map(item => String(item.id)).join(',') !== currentQueueItems.map(item => String(item.id)).join(',');
    if (!changed) return;
    currentQueueItems = nextItems;
    const totalSeconds = currentQueueItems.reduce((sum, item) => sum + Math.max(0, Number(item.cue_duration_seconds) || 0), 0);
    renderQueueTable(currentQueueItems, 'Queue is empty', totalSeconds);
    try {
      await persistQueueOrder();
      await loadQueue();
    } catch (err) {
      console.error('Unable to reorder queue', err);
      await loadQueue();
    }
  }

  async function addTracksToQueue(trackIds){
    const ids = Array.isArray(trackIds) ? trackIds.map(id => String(id)).filter(Boolean) : [];
    if (!ids.length) return;
    if (els.queueList) els.queueList.classList.remove('is-track-drop-target');
    try {
      await jsonFetch('/api/queue/add', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({track_ids: ids})
      });
      await loadQueue();
    } finally {
      if (els.queueList) els.queueList.classList.remove('is-track-drop-target');
    }
  }

  function renderQueueTable(items, emptyText, totalSeconds){
    if (!els.queueList) return;
    currentQueueItems = Array.isArray(items) ? items.slice() : [];
    currentQueueEmptyText = emptyText || 'Queue is empty';
    if (!els.queueList.hasAttribute('tabindex')) {
      els.queueList.setAttribute('tabindex', '0');
    }
    clearQueueDropIndicator();

    if (els.queueList && !els.queueList.dataset.trackDropBound) {
      els.queueList.addEventListener('dragenter', event => {
        if (queueDragSourceId || (playlistTrackDragIds && playlistTrackDragIds.length)) {
          event.preventDefault();
          els.queueList.classList.add('is-track-drop-target');
        }
      });
      els.queueList.addEventListener('dragover', event => {
        if (queueDragSourceId || (playlistTrackDragIds && playlistTrackDragIds.length)) {
          event.preventDefault();
          if (playlistTrackDragIds && playlistTrackDragIds.length) els.queueList.classList.add('is-track-drop-target');
        }
      });
      els.queueList.addEventListener('dragleave', event => {
        if (event.target === els.queueList || !els.queueList.contains(event.relatedTarget)) {
          els.queueList.classList.remove('is-track-drop-target');
        }
      });
      els.queueList.addEventListener('drop', async event => {
        const trackDragIds = Array.isArray(playlistTrackDragIds) ? playlistTrackDragIds.slice() : [];
        if (!trackDragIds.length) return;
        event.preventDefault();
        playlistTrackDragIds = [];
        clearQueueDropIndicator();
        els.queueList.classList.remove('is-track-drop-target');
        try {
          await addTracksToQueue(trackDragIds);
        } catch (err) {
          console.error('Unable to add dragged tracks to queue', err);
        }
      });
      els.queueList.dataset.trackDropBound = '1';
    }

    if (!items || !items.length){
      selectedQueueIds.clear();
      lastSelectedQueueId = null;
      els.queueList.innerHTML = `<div class="queue-table__empty">${escapeHtml(emptyText)}</div>`;
      if (els.queueSummary) els.queueSummary.textContent = formatQueueSummary(0, 0);
      updateQueueToolbarState();
      return;
    }

    const validQueueIds = new Set(items.map(item => String(item.id)));
    Array.from(selectedQueueIds).forEach(queueId => {
      if (!validQueueIds.has(String(queueId))) selectedQueueIds.delete(String(queueId));
    });

    const nowMs = Date.now();
    const baseMs = studioQueueEtaBaseMs > 0 ? studioQueueEtaBaseMs : nowMs;
    let runningEtaSeconds = 0;
    els.queueList.innerHTML = items.map(item => {
      const etaDate = new Date(baseMs + (runningEtaSeconds * 1000));
      const queueId = String(item.id);
      const selectedClass = selectedQueueIds.has(queueId) ? ' is-selected' : '';
      const html = `
        <button class="queue-table__row queue-table__row--draggable${selectedClass}" type="button" data-queue-id="${queueId}" draggable="true">
          <div class="queue-table__eta">${formatClockTime(etaDate)}</div>
          <div class="queue-table__title">${escapeHtml(item.filename || item.path || 'Untitled')}</div>
          <div class="queue-table__duration">${formatSeconds(item.cue_duration_seconds)}</div>
        </button>
      `;
      runningEtaSeconds += Math.max(0, Number(item.cue_duration_seconds) || 0);
      return html;
    }).join('');

    const queueIdsInOrder = items.map(item => String(item.id));
    els.queueList.querySelectorAll('[data-queue-id]').forEach(row => {
      row.addEventListener('click', event => {
        if (els.queueList && typeof els.queueList.focus === 'function') {
          try { els.queueList.focus({preventScroll: true}); } catch (_err) { try { els.queueList.focus(); } catch (_err2) {} }
        }
        const queueId = String(row.dataset.queueId || '');
        if (!queueId) return;

        if (event.shiftKey && queueIdsInOrder.length) {
          const anchorId = lastSelectedQueueId && queueIdsInOrder.includes(lastSelectedQueueId)
            ? lastSelectedQueueId
            : (selectedQueueIds.size ? Array.from(selectedQueueIds).find(id => queueIdsInOrder.includes(id)) : null);
          if (anchorId) {
            const startIndex = queueIdsInOrder.indexOf(anchorId);
            const endIndex = queueIdsInOrder.indexOf(queueId);
            if (startIndex !== -1 && endIndex !== -1) {
              const fromIndex = Math.min(startIndex, endIndex);
              const toIndex = Math.max(startIndex, endIndex);
              selectedQueueIds.clear();
              for (let i = fromIndex; i <= toIndex; i += 1) {
                selectedQueueIds.add(queueIdsInOrder[i]);
              }
            } else {
              selectedQueueIds.clear();
              selectedQueueIds.add(queueId);
            }
          } else {
            selectedQueueIds.clear();
            selectedQueueIds.add(queueId);
          }
        } else if (event.ctrlKey || event.metaKey) {
          if (selectedQueueIds.has(queueId)) selectedQueueIds.delete(queueId);
          else selectedQueueIds.add(queueId);
        } else {
          const singleSelected = selectedQueueIds.size === 1 && selectedQueueIds.has(queueId);
          selectedQueueIds.clear();
          if (!singleSelected) selectedQueueIds.add(queueId);
        }

        lastSelectedQueueId = queueId;
        renderQueueTable(currentQueueItems, emptyText, totalSeconds);
      });

      row.addEventListener('dragstart', event => {
        queueDragSourceId = String(row.dataset.queueId || '');
        row.classList.add('is-dragging');
        if (event.dataTransfer) {
          event.dataTransfer.effectAllowed = 'move';
          event.dataTransfer.setData('text/plain', queueDragSourceId);
        }
      });

      row.addEventListener('dragend', () => {
        row.classList.remove('is-dragging');
        queueDragSourceId = null;
        clearQueueDropIndicator();
      });

      row.addEventListener('dragover', event => {
        const hasQueueDrag = Boolean(queueDragSourceId);
        const hasTrackDrag = Array.isArray(playlistTrackDragIds) && playlistTrackDragIds.length > 0;
        if (!hasQueueDrag && !hasTrackDrag) return;
        event.preventDefault();
        const targetId = String(row.dataset.queueId || '');
        if (hasQueueDrag) {
          if (!targetId || targetId === queueDragSourceId) {
            clearQueueDropIndicator();
            return;
          }
          const destination = resolveQueueDropDestination(currentQueueItems, queueDragSourceId, targetId, getQueueDropPlacement(row, event.clientY));
          if (!destination) {
            clearQueueDropIndicator();
            return;
          }
          applyQueueDropIndicator(findQueueRow(destination.targetId), destination.placement, destination.insertIndex);
        } else {
          els.queueList.classList.add('is-track-drop-target');
        }
      });

      row.addEventListener('drop', async event => {
        const trackDragIds = Array.isArray(playlistTrackDragIds) ? playlistTrackDragIds.slice() : [];
        if (!queueDragSourceId && !trackDragIds.length) return;
        event.preventDefault();
        const targetId = String(row.dataset.queueId || '');
        if (trackDragIds.length) {
          playlistTrackDragIds = [];
          clearQueueDropIndicator();
          els.queueList.classList.remove('is-track-drop-target');
          try {
            await addTracksToQueue(trackDragIds);
          } catch (err) {
            console.error('Unable to add dragged tracks to queue', err);
          }
          return;
        }
        if (!targetId || targetId === queueDragSourceId) return;
        const destination = resolveQueueDropDestination(currentQueueItems, queueDragSourceId, targetId, getQueueDropPlacement(row, event.clientY));
        const sourceId = queueDragSourceId;
        queueDragSourceId = null;
        clearQueueDropIndicator();
        if (!destination) return;
        await handleQueueDrop(sourceId, destination.insertIndex);
      });
    });

    if (els.queueSummary) els.queueSummary.textContent = formatQueueSummary(totalSeconds, items.length);
    updateQueueToolbarState();
  }


  function renderHistoryTable(items, emptyText, totalSeconds){
    if (!els.historyList) return;
    if (!items || !items.length){
      els.historyList.innerHTML = `<div class="queue-table__empty">${escapeHtml(emptyText)}</div>`;
      if (els.historySummary) els.historySummary.textContent = formatQueueSummary(0, 0);
      return;
    }

    els.historyList.innerHTML = items.map(item => `
      <div class="queue-table__row queue-table__row--static" data-history-id="${escapeHtml(item.id)}">
        <div class="queue-table__eta">${escapeHtml(item.played_at || '')}</div>
        <div class="queue-table__title">${escapeHtml(item.filename || 'Unknown track')}</div>
        <div class="queue-table__duration">${formatSeconds(item.cue_duration_seconds)}</div>
      </div>
    `).join('');

    if (els.historySummary) els.historySummary.textContent = formatQueueSummary(totalSeconds, items.length);
  }

  function renderPlaylistTree(items, emptyText){
    if (!els.playlistCategories) return;
    if (!items || !items.length){
      els.playlistCategories.innerHTML = `<div class="playlist-tree__empty">${escapeHtml(emptyText)}</div>`;
      return;
    }

    els.playlistCategories.innerHTML = items.map(item => {
      const categoryId = String(item.id);
      const selectedClass = categoryId === String(selectedCategoryId) ? ' is-selected' : '';
      return `
        <button class="playlist-tree__node${selectedClass}" type="button" data-category-id="${categoryId}">
          <span class="playlist-tree__caret"></span>
          <span class="playlist-tree__folder"></span>
          <span class="playlist-tree__label">${escapeHtml(item.name || 'Untitled category')}</span>
        </button>
      `;
    }).join('');
  }

  function closePlaylistContextMenu(){
    if (!els.playlistContextMenu) return;
    els.playlistContextMenu.style.display = 'none';
    els.playlistContextMenu.setAttribute('aria-hidden', 'true');
    els.playlistContextMenu.querySelectorAll('[data-submenu-open]').forEach(item => item.setAttribute('data-submenu-open', 'false'));
    playlistContextTargetId = null;
  }

  function openPlaylistContextMenu(x, y, categoryId){
    const menu = els.playlistContextMenu;
    if (!menu) return;
    playlistContextTargetId = categoryId ? String(categoryId) : null;
    menu.style.display = 'block';
    menu.setAttribute('aria-hidden', 'false');
    menu.style.left = '0px';
    menu.style.top = '0px';
    const viewportWidth = window.innerWidth || document.documentElement.clientWidth || 0;
    const viewportHeight = window.innerHeight || document.documentElement.clientHeight || 0;
    const rect = menu.getBoundingClientRect();
    const nextLeft = Math.max(4, Math.min(x, viewportWidth - rect.width - 4));
    const nextTop = Math.max(4, Math.min(y, viewportHeight - rect.height - 4));
    menu.style.left = `${Math.round(nextLeft)}px`;
    menu.style.top = `${Math.round(nextTop)}px`;
  }

  function closeTracksAddMenu(){
    if (!els.tracksAddMenu) return;
    els.tracksAddMenu.style.display = 'none';
    els.tracksAddMenu.setAttribute('aria-hidden', 'true');
    tracksAddMenuOpen = false;
  }

  function openTracksAddMenu(x, y, target = "playlist") {
    const menu = els.tracksAddMenu;
    if (!menu) return;
    tracksAddMenuTarget = target === "queue" ? "queue" : "playlist";
    menu.style.display = 'block';
    menu.setAttribute('aria-hidden', 'false');
    menu.style.left = '0px';
    menu.style.top = '0px';
    const viewportWidth = window.innerWidth || document.documentElement.clientWidth || 0;
    const viewportHeight = window.innerHeight || document.documentElement.clientHeight || 0;
    const rect = menu.getBoundingClientRect();
    const nextLeft = Math.max(4, Math.min(x, viewportWidth - rect.width - 4));
    const nextTop = Math.max(4, Math.min(y, viewportHeight - rect.height - 4));
    menu.style.left = `${Math.round(nextLeft)}px`;
    menu.style.top = `${Math.round(nextTop)}px`;
    tracksAddMenuOpen = true;
  }

  function initializeTracksAddMenu(){
    const menu = els.tracksAddMenu;
    if (!menu) return;
    menu.addEventListener('contextmenu', event => event.preventDefault());

    document.addEventListener('click', event => {
      if (menu.style.display === 'block' && !menu.contains(event.target) && !event.target.closest('[data-track-action="add"]')) {
        closeTracksAddMenu();
      }
    });

    document.addEventListener('keydown', event => {
      if (event.key === 'Escape') closeTracksAddMenu();
    });

    document.addEventListener('scroll', () => {
      if (menu.style.display === 'block') closeTracksAddMenu();
    }, true);

    menu.querySelectorAll('[data-track-add-action]').forEach(btn => {
      btn.addEventListener('click', async event => {
        event.preventDefault();
        event.stopPropagation();
        const action = btn.dataset.trackAddAction;
        const menuTarget = tracksAddMenuTarget === 'queue' ? 'queue' : 'playlist';
        const targetCategoryId = selectedCategoryId;
        closeTracksAddMenu();
        if (menuTarget === 'playlist' && !targetCategoryId) return;
        if (action === 'add-files') {
          await openPlaylistBrowserWindow(targetCategoryId, PLAYLIST_BROWSER_MODES.FILES, menuTarget);
        } else if (action === 'add-directory') {
          await openPlaylistBrowserWindow(targetCategoryId, PLAYLIST_BROWSER_MODES.DIRECTORIES, menuTarget);
        } else if (action === 'add-url') {
          await openPlaylistAddUrlWindow(targetCategoryId, menuTarget);
        }
      });
    });
  }


  function closeScriptsAddMenu(){
    if (!els.scriptsAddMenu) return;
    els.scriptsAddMenu.style.display = 'none';
    els.scriptsAddMenu.setAttribute('aria-hidden', 'true');
    scriptsAddMenuOpen = false;
  }

  function openScriptsAddMenu(x, y){
    const menu = els.scriptsAddMenu;
    if (!menu) return;
    menu.style.display = 'block';
    menu.setAttribute('aria-hidden', 'false');
    menu.style.left = '0px';
    menu.style.top = '0px';
    const viewportWidth = window.innerWidth || document.documentElement.clientWidth || 0;
    const viewportHeight = window.innerHeight || document.documentElement.clientHeight || 0;
    const rect = menu.getBoundingClientRect();
    const nextLeft = Math.max(4, Math.min(x, viewportWidth - rect.width - 4));
    const nextTop = Math.max(4, Math.min(y, viewportHeight - rect.height - 4));
    menu.style.left = `${Math.round(nextLeft)}px`;
    menu.style.top = `${Math.round(nextTop)}px`;
    scriptsAddMenuOpen = true;
  }


  function getScriptBrowserEntryKey(entry){
    if (!entry) return '';
    if (entry.type === 'parent') return `parent:${entry.relative_path || ''}`;
    if (entry.type === 'dir') return `dir:${entry.relative_path || ''}`;
    return `file:${entry.relative_path || entry.filename || ''}`;
  }

  function renderScriptsBrowserBreadcrumb(currentSub){
    if (!els.scriptsBrowserBreadcrumb) return;
    const normalized = String(currentSub || '').replace(/\\/g, '/').replace(/^\/+|\/+$/g, '');
    els.scriptsBrowserBreadcrumb.textContent = normalized ? normalized.split('/').join(' / ') : '/';
  }

  function updateScriptsBrowserSummary(){
    if (!els.scriptsBrowserSummary || !scriptsBrowserState) return;
    const selectedCount = scriptsBrowserState.selectedKeys ? scriptsBrowserState.selectedKeys.size : 0;
    els.scriptsBrowserSummary.textContent = `${selectedCount} file${selectedCount === 1 ? '' : 's'} selected`;
    if (els.scriptsAddScriptOk) els.scriptsAddScriptOk.disabled = selectedCount === 0;
  }

  function renderScriptsBrowserRows(){
    if (!els.scriptsBrowserRows || !scriptsBrowserState) return;
    const rows = scriptsBrowserState.entries || [];
    if (!rows.length) {
      els.scriptsBrowserRows.innerHTML = '<div class="browser-table__empty">No .wbs files found</div>';
      updateScriptsBrowserSummary();
      return;
    }
    els.scriptsBrowserRows.innerHTML = rows.map((entry, index) => {
      const key = getScriptBrowserEntryKey(entry);
      const isSelected = scriptsBrowserState.selectedKeys.has(key);
      const icon = entry.type === 'dir' ? '📁' : '📄';
      const name = entry.type === 'parent' ? '..' : (entry.filename || entry.name || entry.relative_path || 'Untitled');
      const typeLabel = entry.type === 'dir' ? 'Folder' : (entry.type === 'parent' ? '' : 'WBS');
      return `
        <button type="button" class="browser-table__row${isSelected ? ' is-selected' : ''}" data-scripts-browser-key="${escapeHtml(key)}" data-scripts-browser-index="${index}" data-scripts-browser-type="${escapeHtml(entry.type)}">
          <div class="browser-table__name"><span class="browser-table__icon">${icon}</span><span>${escapeHtml(name)}</span></div>
          <div class="browser-table__duration">${escapeHtml(typeLabel)}</div>
        </button>
      `;
    }).join('');
    updateScriptsBrowserSummary();
  }

  async function loadScriptsBrowserDirectory(subPath){
    if (!scriptsBrowserState) return;
    const normalizedSub = String(subPath || '');
    const data = await jsonFetch(`/api/studio/scripts/browser?sub=${encodeURIComponent(normalizedSub)}`);
    scriptsBrowserState.currentSub = data.current_sub || '';
    scriptsBrowserState.parentSub = data.parent_sub || '';
    scriptsBrowserState.selectedKeys.clear();
    scriptsBrowserState.lastSelectedIndex = null;
    const parentEntry = (scriptsBrowserState.currentSub || '')
      ? [{type: 'parent', name: '..', relative_path: scriptsBrowserState.parentSub || ''}]
      : [];
    const dirs = (data.dirs || []).map(item => ({...item, type: 'dir'}));
    const files = (data.files || []).map(item => ({...item, type: 'file'}));
    scriptsBrowserState.entries = parentEntry.concat(dirs, files);
    renderScriptsBrowserBreadcrumb(scriptsBrowserState.currentSub || '');
    renderScriptsBrowserRows();
  }

  function handleScriptsBrowserRowClick(event, row){
    if (!scriptsBrowserState || !row) return;
    const index = Number(row.dataset.scriptsBrowserIndex || -1);
    const entry = scriptsBrowserState.entries[index];
    if (!entry) return;

    if (entry.type === 'parent' || entry.type === 'dir') {
      scriptsBrowserState.selectedKeys.clear();
      scriptsBrowserState.lastSelectedIndex = index;
      return;
    }

    const key = getScriptBrowserEntryKey(entry);
    scriptsBrowserState.selectedKeys.clear();
    scriptsBrowserState.selectedKeys.add(key);
    scriptsBrowserState.lastSelectedIndex = index;
    renderScriptsBrowserRows();
  }

  async function openAddScriptWindow(){
    const win = els.scriptsAddScriptWindow;
    const okBtn = els.scriptsAddScriptOk;
    const cancelBtn = els.scriptsAddScriptCancel;
    const closeBtn = els.scriptsAddScriptClose;
    const rowsEl = els.scriptsBrowserRows;
    if (!win || !okBtn || !cancelBtn || !closeBtn || !rowsEl) return null;

    scriptsBrowserState = {
      currentSub: '',
      parentSub: '',
      entries: [],
      selectedKeys: new Set(),
      lastSelectedIndex: null
    };

    if (els.scriptsAutoStart) els.scriptsAutoStart.checked = false;
    openFloatingWindow(win);
    okBtn.disabled = true;
    renderScriptsBrowserBreadcrumb('');
    rowsEl.innerHTML = '<div class="browser-table__empty">Loading files…</div>';
    updateScriptsBrowserSummary();

    try {
      await loadScriptsBrowserDirectory('');
    } catch (err) {
      console.error('Failed to load script browser', err);
      rowsEl.innerHTML = '<div class="browser-table__empty">Files unavailable</div>';
      okBtn.disabled = true;
    }

    const completed = await new Promise(resolve => {
      let done = false;

      const cleanup = () => {
        okBtn.removeEventListener('click', onOk);
        cancelBtn.removeEventListener('click', onCancel);
        closeBtn.removeEventListener('click', onCancel);
        rowsEl.removeEventListener('click', onRowsClick);
        rowsEl.removeEventListener('dblclick', onRowsDblClick);
        document.removeEventListener('keydown', onKeydown);
      };

      const finish = result => {
        if (done) return;
        done = true;
        cleanup();
        closeFloatingWindow(win);
        scriptsBrowserState = null;
        resolve(result);
      };

      function onCancel(event){
        if (event) {
          event.preventDefault();
          event.stopPropagation();
        }
        finish(null);
      }

      async function onOk(event){
        if (event) {
          event.preventDefault();
          event.stopPropagation();
        }
        const selectedEntry = ((scriptsBrowserState && scriptsBrowserState.entries) || []).find(
          entry => scriptsBrowserState.selectedKeys.has(getScriptBrowserEntryKey(entry))
        );
        if (!selectedEntry) {
          finish(null);
          return;
        }

        const scriptPath = String(selectedEntry.relative_path || '').trim();
        const autoStart = !!(els.scriptsAutoStart && els.scriptsAutoStart.checked);

        try {
          const data = await jsonFetch('/api/studio/scripts', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
              script_path: scriptPath,
              auto_start: autoStart
            })
          });

          const existingIndex = studioScriptsData.findIndex(item => String(item.script_path || '').trim() === scriptPath);
          const nextItem = {
            id: Number(data.id || 0) || (existingIndex >= 0 ? Number(studioScriptsData[existingIndex].id || 0) : (studioScriptsData.length + 1)),
            script_path: scriptPath,
            auto_start: autoStart ? 1 : 0,
            status: String(data.status || 'Stopped') || 'Stopped'
          };
          if (existingIndex >= 0) studioScriptsData[existingIndex] = nextItem;
          else studioScriptsData.push(nextItem);

          selectedStudioScriptId = Number(nextItem.id || 0) || selectedStudioScriptId;
          if (selectedStudioScriptId) {
            selectedStudioEntryType = 'script';
            selectedStudioEntryKey = String(selectedStudioScriptId);
          }
          renderStudioScriptsList();
          setTimeout(() => { loadScheduler(); }, 100);
        } catch (err) {
          console.error('Failed to add script', err);
        }

        finish({
          relative_path: scriptPath,
          auto_start: autoStart
        });
      }

      function onRowsClick(event){
        const row = event.target.closest('[data-scripts-browser-key]');
        if (!row) return;
        event.preventDefault();
        handleScriptsBrowserRowClick(event, row);
      }

      function onRowsDblClick(event){
        const row = event.target.closest('[data-scripts-browser-key]');
        if (!row) return;
        event.preventDefault();
        const index = Number(row.dataset.scriptsBrowserIndex || -1);
        const entry = scriptsBrowserState && scriptsBrowserState.entries ? scriptsBrowserState.entries[index] : null;
        if (!entry) return;
        if (entry.type === 'parent') {
          loadScriptsBrowserDirectory(scriptsBrowserState.parentSub || '');
          return;
        }
        if (entry.type === 'dir') {
          loadScriptsBrowserDirectory(entry.relative_path || '');
          return;
        }
        handleScriptsBrowserRowClick(event, row);
        if (entry.type === 'file') onOk(event);
      }

      function onKeydown(event){
        if (event.key === 'Escape') onCancel(event);
      }

      okBtn.addEventListener('click', onOk);
      cancelBtn.addEventListener('click', onCancel);
      closeBtn.addEventListener('click', onCancel);
      rowsEl.addEventListener('click', onRowsClick);
      rowsEl.addEventListener('dblclick', onRowsDblClick);
      document.addEventListener('keydown', onKeydown);
    });

    return completed;
  }

  function initializeScriptsAddMenu(){
    const menu = els.scriptsAddMenu;
    const addBtn = els.scriptsAddBtn;
    if (!menu || !addBtn) return;
    menu.addEventListener('contextmenu', event => event.preventDefault());

    addBtn.addEventListener('click', event => {
      event.preventDefault();
      event.stopPropagation();
      if (scriptsAddMenuOpen) {
        closeScriptsAddMenu();
        return;
      }
      const rect = addBtn.getBoundingClientRect();
      openScriptsAddMenu(rect.left, rect.bottom + 4);
    });

    document.addEventListener('click', event => {
      if (menu.style.display === 'block' && !menu.contains(event.target) && !event.target.closest('#scripts-add-btn')) {
        closeScriptsAddMenu();
      }
    });

    document.addEventListener('keydown', event => {
      if (event.key === 'Escape') closeScriptsAddMenu();
    });

    document.addEventListener('scroll', () => {
      if (menu.style.display === 'block') closeScriptsAddMenu();
    }, true);

    menu.querySelectorAll('[data-scripts-add-action]').forEach(btn => {
      btn.addEventListener('click', async event => {
        event.preventDefault();
        event.stopPropagation();
        const action = btn.getAttribute('data-scripts-add-action') || '';
        closeScriptsAddMenu();
        if (action === 'script') {
          await openAddScriptWindow();
          return;
        }
        if (action === 'scheduler') {
          if (typeof window.openSchedulerRuleModal === 'function') {
            window.openSchedulerRuleModal();
          }
        }
      });
    });
  }

  function initializePlaylistContextMenu(){
    const menu = els.playlistContextMenu;
    if (!menu) return;

    menu.addEventListener('contextmenu', event => event.preventDefault());
    menu.addEventListener('click', event => {
      event.preventDefault();
      event.stopPropagation();
      const submenuItem = event.target.closest('.context-menu__item--submenu');
      if (submenuItem) return;
      closePlaylistContextMenu();
    });

    menu.querySelectorAll('.context-menu__item--submenu').forEach(item => {
      item.addEventListener('mouseenter', () => item.setAttribute('data-submenu-open', 'true'));
      item.addEventListener('mouseleave', () => item.setAttribute('data-submenu-open', 'false'));
    });

    document.addEventListener('click', event => {
      if (!menu.contains(event.target)) closePlaylistContextMenu();
    });

    document.addEventListener('keydown', event => {
      if (event.key === 'Escape') closePlaylistContextMenu();
    });

    document.addEventListener('scroll', () => {
      if (menu.style.display === 'block') closePlaylistContextMenu();
    }, true);

    if (els.playlistCategories) {
      els.playlistCategories.addEventListener('contextmenu', event => {
        const node = event.target.closest('[data-category-id]');
        if (!node) return;
        event.preventDefault();
        event.stopPropagation();
        selectedCategoryId = node.dataset.categoryId;
        renderPlaylistTreeFromDomSelection();
        openPlaylistContextMenu(event.clientX, event.clientY, node.dataset.categoryId);
      });
    }

    if (els.playlistRootToggle) {
      els.playlistRootToggle.addEventListener('contextmenu', event => {
        event.preventDefault();
        event.stopPropagation();
        openPlaylistContextMenu(event.clientX, event.clientY, null);
      });
    }
  }

  function renderPlaylistTreeFromDomSelection(){
    if (!els.playlistCategories) return;
    els.playlistCategories.querySelectorAll('[data-category-id]').forEach(btn => {
      btn.classList.toggle('is-selected', btn.dataset.categoryId === String(selectedCategoryId));
    });
  }

  function initializePlaylistSplitter(){
    const splitter = els.playlistSplitter;
    const treeSection = els.playlistTreeSection;
    const tracksSection = els.playlistTracksSection;
    const playlistPanel = document.querySelector('.panel-playlist');
    if (!splitter || !treeSection || !tracksSection || !playlistPanel) return;
    let startY = 0;
    let startHeight = 0;
    const onMove = (event) => {
      const panelRect = playlistPanel.getBoundingClientRect();
      const headerHeight = 29;
      const totalAvailable = Math.max(280, panelRect.height - headerHeight - splitter.offsetHeight);
      const delta = event.clientY - startY;
      const minTop = 110;
      const minBottom = 150;
      const nextHeight = Math.max(minTop, Math.min(totalAvailable - minBottom, startHeight + delta));
      treeSection.style.flexBasis = `${nextHeight}px`;
    };
    const onUp = () => {
      document.removeEventListener('mousemove', onMove);
      document.removeEventListener('mouseup', onUp);
      document.body.classList.remove('is-resizing-playlist-split');
      persistPlaylistSplitter();
    };
    splitter.addEventListener('mousedown', event => {
      event.preventDefault();
      startY = event.clientY;
      startHeight = treeSection.getBoundingClientRect().height;
      document.addEventListener('mousemove', onMove);
      document.addEventListener('mouseup', onUp);
      document.body.classList.add('is-resizing-playlist-split');
    });
  }

  function updateQueueToolbarState(){
    document.querySelectorAll('[data-queue-action="remove"]').forEach(btn => {
      btn.disabled = selectedQueueIds.size === 0;
    });
  }

  function getPlaylistCategoryButton(categoryId){
    if (!els.playlistCategories) return null;
    return els.playlistCategories.querySelector(`[data-category-id="${String(categoryId)}"]`);
  }

  function cancelPlaylistRename(){
    if (!playlistRenameState) return;
    const {labelEl, originalName} = playlistRenameState;
    if (labelEl) {
      labelEl.textContent = originalName;
      labelEl.classList.remove('is-renaming');
    }
    playlistRenameState = null;
  }

  async function commitPlaylistRename(nextName){
    if (!playlistRenameState) return;
    const state = playlistRenameState;
    const trimmedName = String(nextName || '').trim();
    if (!trimmedName) {
      cancelPlaylistRename();
      return;
    }
    if (trimmedName === state.originalName) {
      cancelPlaylistRename();
      return;
    }
    try {
      const payload = await jsonFetch(`/api/library/category/${encodeURIComponent(String(state.categoryId))}/rename`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({name: trimmedName})
      });
      const savedName = (payload && payload.category && payload.category.name) ? String(payload.category.name) : trimmedName;
      state.labelEl.textContent = savedName;
      state.labelEl.classList.remove('is-renaming');
      playlistRenameState = null;
      await loadCategories();
    } catch (err) {
      console.error('Failed to rename playlist category', err);
      cancelPlaylistRename();
    }
  }

  function startPlaylistRename(categoryId){
    const button = getPlaylistCategoryButton(categoryId);
    const labelEl = button ? button.querySelector('.playlist-tree__label') : null;
    if (!button || !labelEl) return;
    cancelPlaylistRename();
    const originalName = (labelEl.textContent || '').trim();
    playlistRenameState = {categoryId: String(categoryId), labelEl, originalName};
    labelEl.classList.add('is-renaming');
    labelEl.innerHTML = '';
    const input = document.createElement('input');
    input.type = 'text';
    input.className = 'playlist-tree__rename-input';
    input.value = originalName;
    input.setAttribute('aria-label', 'Rename category');
    input.addEventListener('click', event => event.stopPropagation());
    input.addEventListener('mousedown', event => event.stopPropagation());
    labelEl.appendChild(input);

    let finishing = false;
    const finish = async (mode) => {
      if (finishing) return;
      finishing = true;
      if (mode === 'save') await commitPlaylistRename(input.value);
      else cancelPlaylistRename();
    };

    input.addEventListener('keydown', async event => {
      if (event.key === 'Enter') {
        event.preventDefault();
        await finish('save');
      } else if (event.key === 'Escape') {
        event.preventDefault();
        await finish('cancel');
      }
    });
    input.addEventListener('blur', async () => { await finish('save'); });

    window.requestAnimationFrame(() => {
      input.focus();
      input.select();
    });
  }

  async function promptWithPlaylistModal({title, label, placeholder, defaultValue, okText, cancelText}){
    const backdrop = els.playlistPromptBackdrop;
    const titleEl = els.playlistPromptTitle;
    const labelEl = els.playlistPromptLabel;
    const inputEl = els.playlistPromptInput;
    const okBtn = els.playlistPromptOk;
    const cancelBtn = els.playlistPromptCancel;
    const closeBtn = els.playlistPromptClose;
    if (!backdrop || !titleEl || !labelEl || !inputEl || !okBtn || !cancelBtn) return null;

    const previous = {
      title: titleEl.textContent,
      label: labelEl.textContent,
      placeholder: inputEl.getAttribute('placeholder') || '',
      value: inputEl.value,
      ok: okBtn.textContent,
      cancel: cancelBtn.textContent
    };

    titleEl.textContent = title || previous.title;
    labelEl.textContent = label || previous.label;
    inputEl.setAttribute('placeholder', placeholder || '');
    inputEl.value = defaultValue || '';
    okBtn.textContent = okText || previous.ok;
    cancelBtn.textContent = cancelText || previous.cancel;

    openFloatingWindow(backdrop);

    const result = await new Promise(resolve => {
      function finish(value){
        closeFloatingWindow(backdrop);
        titleEl.textContent = previous.title;
        labelEl.textContent = previous.label;
        inputEl.setAttribute('placeholder', previous.placeholder);
        inputEl.value = previous.value;
        okBtn.textContent = previous.ok;
        cancelBtn.textContent = previous.cancel;
        cleanup();
        resolve(value);
      }

      function onOk(){ finish(inputEl.value); }
      function onCancel(){ finish(null); }
      function onKeyDown(event){
        if (event.key === 'Escape') finish(null);
        if (event.key === 'Enter') {
          event.preventDefault();
          finish(inputEl.value);
        }
      }
      function cleanup(){
        okBtn.removeEventListener('click', onOk);
        cancelBtn.removeEventListener('click', onCancel);
        if (closeBtn) closeBtn.removeEventListener('click', onCancel);
        document.removeEventListener('keydown', onKeyDown, true);
      }

      okBtn.addEventListener('click', onOk);
      cancelBtn.addEventListener('click', onCancel);
      if (closeBtn) closeBtn.addEventListener('click', onCancel);
      document.addEventListener('keydown', onKeyDown, true);
      window.requestAnimationFrame(() => {
        inputEl.focus();
        inputEl.select();
      });
    });

    return result;
  }

  async function createPlaylistCategory(){
    const raw = await promptWithPlaylistModal({
      title: 'New Category',
      label: 'New category name',
      placeholder: '',
      defaultValue: '',
      okText: 'Add',
      cancelText: 'Cancel'
    });
    const name = String(raw || '').trim();
    if (!name) return;
    try{
      const data = await jsonFetch('/api/library/categories', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({name})
      });
      if (data && data.created_category && data.created_category.id != null) {
        selectedCategoryId = String(data.created_category.id);
      }
      await loadCategories();
    }catch(err){
      console.error('Failed to create playlist category', err);
      setStudioSettingsFeedback(err.message || 'Failed to create playlist category.', 'error');
    }
  }

  async function confirmPlaylistDeleteModal({title, body, yesText, noText}){
    const win = els.playlistDeleteWindow;
    const titleEl = els.playlistDeleteTitle;
    const bodyEl = els.playlistDeleteBody;
    const yesBtn = els.playlistDeleteYes;
    const noBtn = els.playlistDeleteNo;
    if (!win || !titleEl || !bodyEl || !yesBtn || !noBtn) return false;

    const previous = {
      title: titleEl.textContent,
      body: bodyEl.textContent,
      yes: yesBtn.textContent,
      no: noBtn.textContent
    };

    titleEl.textContent = title || previous.title;
    bodyEl.textContent = body || previous.body;
    yesBtn.textContent = yesText || previous.yes;
    noBtn.textContent = noText || previous.no;

    const closeBtn = els.playlistDeleteClose;
    openFloatingWindow(win);

    return await new Promise(resolve => {
      let done = false;

      const cleanup = () => {
        yesBtn.removeEventListener('click', onYes);
        noBtn.removeEventListener('click', onNo);
        if (closeBtn) closeBtn.removeEventListener('click', onNo);
        document.removeEventListener('keydown', onKeyDown, true);
        titleEl.textContent = previous.title;
        bodyEl.textContent = previous.body;
        yesBtn.textContent = previous.yes;
        noBtn.textContent = previous.no;
        closeFloatingWindow(win);
      };

      const finish = result => {
        if (done) return;
        done = true;
        cleanup();
        resolve(result);
      };

      const onYes = () => finish(true);
      const onNo = () => finish(false);
      const onKeyDown = event => {
        if (event.key === 'Escape') {
          event.preventDefault();
          finish(false);
        }
      };

      yesBtn.addEventListener('click', onYes);
      noBtn.addEventListener('click', onNo);
      if (closeBtn) closeBtn.addEventListener('click', onNo);
      document.addEventListener('keydown', onKeyDown, true);
      noBtn.focus();
    });
  }

  async function deletePlaylistCategory(){
    const categoryId = playlistContextTargetId || selectedCategoryId;
    if (!categoryId) return;
    const activeNode = els.playlistCategories
      ? els.playlistCategories.querySelector(`[data-category-id="${String(categoryId)}"] .playlist-tree__label`)
      : null;
    const categoryName = (activeNode && activeNode.textContent) ? activeNode.textContent.trim() : 'this category';
    const confirmed = await confirmPlaylistDeleteModal({
      title: 'Delete Category',
      body: `Delete category "${categoryName}" and remove all its track links?`,
      yesText: 'Delete',
      noText: 'Cancel'
    });
    if (!confirmed) return;

    try{
      await jsonFetch(`/api/library/category/${encodeURIComponent(String(categoryId))}/delete`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'}
      });
      if (String(selectedCategoryId) === String(categoryId)) {
        selectedCategoryId = null;
      }
      await loadCategories();
    }catch(err){
      console.error('Failed to delete playlist category', err);
    }
  }

  async function confirmQueueDeleteModal({title, body, yesText, noText}){
    const win = els.queueDeleteWindow;
    const titleEl = els.queueDeleteTitle;
    const bodyEl = els.queueDeleteBody;
    const yesBtn = els.queueDeleteYes;
    const noBtn = els.queueDeleteNo;
    const closeBtn = els.queueDeleteClose;
    if (!win || !titleEl || !bodyEl || !yesBtn || !noBtn) return false;

    const previous = {
      title: titleEl.textContent,
      body: bodyEl.textContent,
      yes: yesBtn.textContent,
      no: noBtn.textContent
    };

    titleEl.textContent = title || previous.title;
    bodyEl.textContent = body || previous.body;
    yesBtn.textContent = yesText || previous.yes;
    noBtn.textContent = noText || previous.no;

    openFloatingWindow(win);

    return await new Promise(resolve => {
      let done = false;

      const cleanup = () => {
        yesBtn.removeEventListener('click', onYes);
        noBtn.removeEventListener('click', onNo);
        if (closeBtn) closeBtn.removeEventListener('click', onNo);
        document.removeEventListener('keydown', onKeyDown, true);
        titleEl.textContent = previous.title;
        bodyEl.textContent = previous.body;
        yesBtn.textContent = previous.yes;
        noBtn.textContent = previous.no;
        closeFloatingWindow(win);
      };

      const finish = result => {
        if (done) return;
        done = true;
        cleanup();
        resolve(result);
      };

      function onYes(event){
        if (event) {
          event.preventDefault();
          event.stopPropagation();
        }
        finish(true);
      }

      function onNo(event){
        if (event) {
          event.preventDefault();
          event.stopPropagation();
        }
        finish(false);
      }

      function onKeyDown(event){
        if (event.key !== 'Escape') return;
        if (win.style.display !== 'flex' && win.style.display !== 'block') return;
        event.preventDefault();
        event.stopPropagation();
        finish(false);
      }

      yesBtn.addEventListener('click', onYes);
      noBtn.addEventListener('click', onNo);
      if (closeBtn) closeBtn.addEventListener('click', onNo);
      document.addEventListener('keydown', onKeyDown, true);
      window.requestAnimationFrame(() => {
        try { noBtn.focus({preventScroll: true}); } catch (_err) { noBtn.focus(); }
      });
    });
  }

  async function confirmCategoryTracksDeleteModal({title, body, yesText, noText}){
    const win = els.categoryTracksDeleteWindow;
    const titleEl = els.categoryTracksDeleteTitle;
    const bodyEl = els.categoryTracksDeleteBody;
    const yesBtn = els.categoryTracksDeleteYes;
    const noBtn = els.categoryTracksDeleteNo;
    if (!win || !titleEl || !bodyEl || !yesBtn || !noBtn) return false;

    const previous = {
      title: titleEl.textContent,
      body: bodyEl.textContent,
      yes: yesBtn.textContent,
      no: noBtn.textContent
    };

    titleEl.textContent = title || previous.title;
    bodyEl.textContent = body || previous.body;
    yesBtn.textContent = yesText || previous.yes;
    noBtn.textContent = noText || previous.no;

    const closeBtn = els.categoryTracksDeleteClose;
    openFloatingWindow(win);

    return await new Promise(resolve => {
      let done = false;

      const cleanup = () => {
        yesBtn.removeEventListener('click', onYes);
        noBtn.removeEventListener('click', onNo);
        if (closeBtn) closeBtn.removeEventListener('click', onNo);
        document.removeEventListener('keydown', onKeydown, true);
        titleEl.textContent = previous.title;
        bodyEl.textContent = previous.body;
        yesBtn.textContent = previous.yes;
        noBtn.textContent = previous.no;
        closeFloatingWindow(win);
      };

      const finish = result => {
        if (done) return;
        done = true;
        cleanup();
        resolve(result);
      };

      function onYes(event){
        if (event) {
          event.preventDefault();
          event.stopPropagation();
        }
        finish(true);
      }

      function onNo(event){
        if (event) {
          event.preventDefault();
          event.stopPropagation();
        }
        finish(false);
      }

      function onKeydown(event){
        if (event.key !== 'Escape') return;
        if (win.style.display !== 'flex' && win.style.display !== 'block') return;
        event.preventDefault();
        event.stopPropagation();
        if (typeof event.stopImmediatePropagation === 'function') event.stopImmediatePropagation();
        finish(false);
      }

      yesBtn.addEventListener('click', onYes);
      noBtn.addEventListener('click', onNo);
      if (closeBtn) closeBtn.addEventListener('click', onNo);
      document.addEventListener('keydown', onKeydown, true);
      noBtn.focus();
    });
  }


  function confirmStudioStopModal(){
    const win = els.studioStopConfirmBackdrop;
    if (win && win.classList.contains('studio-floating-window')) {
      const yesBtn = els.studioStopConfirmYes;
      const noBtn = els.studioStopConfirmNo;
      const closeBtn = els.studioStopConfirmClose;
      if (!yesBtn || !noBtn) return Promise.resolve(true);

      openFloatingWindow(win);
      return new Promise(resolve => {
        let finished = false;

        function cleanup(result){
          if (finished) return;
          finished = true;
          if (yesBtn) yesBtn.removeEventListener('click', onYes, true);
          if (noBtn) noBtn.removeEventListener('click', onNo, true);
          if (closeBtn) closeBtn.removeEventListener('click', onNo, true);
          document.removeEventListener('keydown', onKeyDown, true);
          closeFloatingWindow(win);
          resolve(result);
        }

        function onYes(event){
          if (event) {
            event.preventDefault();
            event.stopPropagation();
            if (typeof event.stopImmediatePropagation === 'function') event.stopImmediatePropagation();
          }
          cleanup(true);
        }

        function onNo(event){
          if (event) {
            event.preventDefault();
            event.stopPropagation();
            if (typeof event.stopImmediatePropagation === 'function') event.stopImmediatePropagation();
          }
          cleanup(false);
        }

        function onKeyDown(event){
          if (event.key !== 'Escape') return;
          if (win.getAttribute('aria-hidden') === 'true' || win.style.display === 'none') return;
          event.preventDefault();
          event.stopPropagation();
          if (typeof event.stopImmediatePropagation === 'function') event.stopImmediatePropagation();
          cleanup(false);
        }

        if (yesBtn) yesBtn.addEventListener('click', onYes, true);
        if (noBtn) noBtn.addEventListener('click', onNo, true);
        if (closeBtn) closeBtn.addEventListener('click', onNo, true);
        document.addEventListener('keydown', onKeyDown, true);

        window.setTimeout(() => {
          try {
            if (noBtn) noBtn.focus();
            else if (yesBtn) yesBtn.focus();
          } catch (error) {
            console.error('Unable to focus studio stop confirm button', error);
          }
        }, 0);
      });
    }

    const backdrop = document.getElementById('stop-confirm-backdrop');
    if (!backdrop) return Promise.resolve(true);

    const yesBtn = document.getElementById('stop-confirm-yes');
    const noBtn = document.getElementById('stop-confirm-no');
    const modal = backdrop.querySelector('.modal');

    backdrop.dataset.result = '';
    backdrop.style.display = 'flex';
    backdrop.style.zIndex = '2147483646';
    if (modal) {
      modal.style.position = 'relative';
      modal.style.zIndex = '2147483647';
    }
    document.body.classList.add('modal-open');
    backdrop.classList.add('active');
    backdrop.setAttribute('aria-hidden', 'false');

    return new Promise(resolve => {
      let finished = false;

      function cleanup(result){
        if (finished) return;
        finished = true;
        backdrop.classList.remove('active');
        backdrop.setAttribute('aria-hidden', 'true');
        backdrop.style.display = 'none';
        backdrop.dataset.result = '';
        document.body.classList.remove('modal-open');
        backdrop.removeEventListener('click', onBackdropClick, true);
        document.removeEventListener('keydown', onKeyDown, true);
        if (yesBtn) yesBtn.removeEventListener('click', onYes, true);
        if (noBtn) noBtn.removeEventListener('click', onNo, true);
        resolve(result);
      }

      function onYes(event){
        event.preventDefault();
        event.stopImmediatePropagation();
        cleanup(true);
      }

      function onNo(event){
        event.preventDefault();
        event.stopImmediatePropagation();
        cleanup(false);
      }

      function onBackdropClick(event){
        if (event.target === backdrop) cleanup(false);
      }

      function onKeyDown(event){
        if (event.key === 'Escape') {
          event.preventDefault();
          cleanup(false);
        }
      }

      if (yesBtn) yesBtn.addEventListener('click', onYes, true);
      if (noBtn) noBtn.addEventListener('click', onNo, true);
      backdrop.addEventListener('click', onBackdropClick, true);
      document.addEventListener('keydown', onKeyDown, true);

      setTimeout(() => {
        try {
          if (yesBtn) yesBtn.focus();
          else if (noBtn) noBtn.focus();
        } catch (error) {
          console.error('Unable to focus studio stop modal button', error);
        }
      }, 0);
    });
  }

  async function removeSelectedQueueItems(){
    const queueIds = Array.from(selectedQueueIds);
    if (!queueIds.length) return;

    const trackLabel = queueIds.length === 1 ? 'selected track' : `${queueIds.length} selected tracks`;
    const confirmed = await confirmQueueDeleteModal({
      title: 'Remove Queue Items',
      body: `Are you sure you want to remove ${trackLabel} from the queue?`,
      yesText: 'Yes',
      noText: 'No'
    });
    if (!confirmed) return;

    try{
      await jsonFetch('/api/queue/remove', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({queue_ids: queueIds})
      });
      selectedQueueIds.clear();
      lastSelectedQueueId = null;
      await loadQueue();
    }catch(err){
      console.error('Unable to remove queue items', err);
    }
  }

  function setDeckOnAirButtonState(running, disabled){
    const button = els.deckOnAirButton;
    if (!button) return;
    const isRunning = Boolean(running);
    const stateLabel = button.querySelector('.deck-onair-button__state');
    const hintLabel = button.querySelector('.deck-onair-button__hint');
    if (stateLabel) stateLabel.textContent = isRunning ? 'ON AIR' : 'OFF AIR';
    if (hintLabel) hintLabel.textContent = isRunning ? 'Push to OFF' : 'Push to ON';
    button.classList.toggle('is-on', isRunning);
    button.classList.toggle('is-off', !isRunning);
    button.setAttribute('aria-pressed', isRunning ? 'true' : 'false');
    button.disabled = Boolean(disabled);
  }

  async function isStudioOnAir(){
    const classicToggle = document.getElementById('audio-engine-toggle');
    if (classicToggle) return classicToggle.dataset.state === 'running';
    try {
      const response = await fetch('/api/audio-engine/status', {cache: 'no-store'});
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      return ((data && data.status && String(data.status).toLowerCase() === 'running') || Boolean(data && data.pid));
    } catch (_error) {
      const studioButton = els.deckOnAirButton;
      return !!(studioButton && studioButton.getAttribute('aria-pressed') === 'true');
    }
  }

  async function autoStartStudioEntriesForOnAir(){
    if (studioOnAirAutoStartInFlight) return;
    studioOnAirAutoStartInFlight = true;
    try {
      const data = await jsonFetch('/api/studio/scripts/auto-start-on-air', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'}
      });
      const items = Array.isArray(data && data.items) ? data.items : [];
      if (items.length) {
        const statusById = new Map(items.map(item => [Number(item.id || 0), String(item.status || 'Stopped')]));
        studioScriptsData = studioScriptsData.map(item => {
          const scriptId = Number(item && item.id || 0);
          if (!scriptId || !statusById.has(scriptId)) return item;
          return Object.assign({}, item, {status: statusById.get(scriptId) || 'Stopped'});
        });
      }
      const rules = Array.isArray(data && data.rules) ? data.rules : [];
      if (rules.length) {
        const ruleMap = new Map(rules.map(rule => [String(rule.id || '').trim(), rule]));
        studioSchedulerRulesData = studioSchedulerRulesData.map(rule => {
          const ruleId = String(rule && rule.id || '').trim();
          if (!ruleId || !ruleMap.has(ruleId)) return rule;
          const latest = ruleMap.get(ruleId) || {};
          return Object.assign({}, rule, {
            is_enabled: Number(latest.is_enabled || 0) ? 1 : 0,
            next_run_at: String(latest.next_run_at || '')
          });
        });
      }
      renderStudioScriptsList();
      setTimeout(() => { loadScheduler(); }, 50);
    } catch (error) {
      console.error('Failed to auto-start studio entries after ON AIR transition', error);
    } finally {
      studioOnAirAutoStartInFlight = false;
    }
  }

  async function stopStudioScriptsForOffAir(){
    if (studioOffAirStopInFlight) return;
    studioOffAirStopInFlight = true;
    try {
      const data = await jsonFetch('/api/studio/scripts/stop-active-off-air', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'}
      });
      const items = Array.isArray(data && data.items) ? data.items : [];
      if (items.length) {
        const statusById = new Map(items.map(item => [Number(item.id || 0), String(item.status || 'Stopped')]));
        studioScriptsData = studioScriptsData.map(item => {
          const scriptId = Number(item && item.id || 0);
          if (!scriptId || !statusById.has(scriptId)) return item;
          return Object.assign({}, item, {status: statusById.get(scriptId) || 'Stopped'});
        });
      }
      const rules = Array.isArray(data && data.rules) ? data.rules : [];
      if (rules.length) {
        const ruleMap = new Map(rules.map(rule => [String(rule.id || '').trim(), rule]));
        studioSchedulerRulesData = studioSchedulerRulesData.map(rule => {
          const ruleId = String(rule && rule.id || '').trim();
          if (!ruleId || !ruleMap.has(ruleId)) return rule;
          const latest = ruleMap.get(ruleId) || {};
          return Object.assign({}, rule, {
            is_enabled: Number(latest.is_enabled || 0) ? 1 : 0,
            next_run_at: String(latest.next_run_at || '')
          });
        });
      }
      renderStudioScriptsList();
      setTimeout(() => { loadScheduler(); }, 50);
    } catch (error) {
      console.error('Failed to stop studio scripts after OFF AIR transition', error);
    } finally {
      studioOffAirStopInFlight = false;
    }
  }

  async function applyStudioOnAirState(isRunning, disabled){
    const onAir = Boolean(isRunning);
    const previous = studioLastKnownOnAir;
    studioLastKnownOnAir = onAir;
    setDeckOnAirButtonState(onAir, disabled);
    if (onAir && previous !== true) {
      await autoStartStudioEntriesForOnAir();
    }
    // Do not stop studio scripts/scheduler rules from a browser-side status refresh.
    // The backend already stops them when the station is actually turned off;
    // calling stop-active-off-air here can falsely stop running automation during
    // Dashboard/Station navigation when the UI briefly observes an off-air state.
  }

  function openScriptStartBlockedModal(){
    const backdrop = els.scriptsStartBlockedBackdrop;
    const okBtn = els.scriptsStartBlockedOk;
    if (!backdrop || !okBtn) return Promise.resolve(false);
    return new Promise(resolve => {
      let finished = false;
      backdrop.style.display = 'flex';
      backdrop.classList.add('active');
      backdrop.setAttribute('aria-hidden', 'false');
      backdrop.style.zIndex = '2147483646';
      const modal = backdrop.querySelector('.modal');
      if (modal) modal.style.zIndex = '2147483647';
      document.body.classList.add('modal-open');
      function cleanup(result){
        if (finished) return;
        finished = true;
        backdrop.classList.remove('active');
        backdrop.setAttribute('aria-hidden', 'true');
        backdrop.style.display = 'none';
        backdrop.style.zIndex = '';
        if (modal) modal.style.zIndex = '';
        document.body.classList.remove('modal-open');
        okBtn.removeEventListener('click', onOk, true);
        backdrop.removeEventListener('click', onBackdropClick, true);
        document.removeEventListener('keydown', onKeyDown, true);
        resolve(result);
      }
      function onOk(){ cleanup(true); }
      function onBackdropClick(event){ if (event.target === backdrop) cleanup(true); }
      function onKeyDown(event){ if (event.key === 'Escape' || event.key === 'Enter') { event.preventDefault(); cleanup(true); } }
      okBtn.addEventListener('click', onOk, true);
      backdrop.addEventListener('click', onBackdropClick, true);
      document.addEventListener('keydown', onKeyDown, true);
      window.requestAnimationFrame(() => {
        try{ okBtn.focus(); }catch(_err){}
      });
    });
  }

  async function syncDeckOnAirButton(){
    const classicToggle = document.getElementById('audio-engine-toggle');
    if (classicToggle) {
      const running = classicToggle.dataset.state === 'running';
      await applyStudioOnAirState(running, classicToggle.disabled);
      return;
    }

    try {
      const response = await fetch('/api/audio-engine/status', {cache: 'no-store'});
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      const running = (data && data.status && String(data.status).toLowerCase() === 'running') || Boolean(data && data.pid);
      await applyStudioOnAirState(running, false);
    } catch (error) {
      await applyStudioOnAirState(false, false);
    }
  }

  async function toggleDeckOnAirButton(){
    const studioButton = els.deckOnAirButton;
    if (!studioButton || studioButton.disabled) return false;

    const classicToggle = document.getElementById('audio-engine-toggle');
    const running = classicToggle
      ? classicToggle.dataset.state === 'running'
      : studioButton.getAttribute('aria-pressed') === 'true';
    const cmd = running ? 'stop' : 'start';

    if (cmd === 'stop') {
      const ok = await confirmStudioStopModal();
      if (!ok) return false;
    }

    setDeckOnAirButtonState(running, true);
    if (classicToggle) classicToggle.disabled = true;

    try {
      await fetch(`/audio-engine/${cmd}`, {method: 'POST'});
    } catch (error) {
      console.error('Unable to toggle audio engine from studio', error);
    }

    if (window.webBroadcasterAudioEngine && typeof window.webBroadcasterAudioEngine.refresh === 'function' && classicToggle) {
      await window.webBroadcasterAudioEngine.refresh(classicToggle);
    }

    await syncDeckOnAirButton();
    return true;
  }


  function isHttpStreamPath(path){
    return (typeof path === 'string') && (path.startsWith('http://') || path.startsWith('https://'));
  }

  let deckHoverSeekSeconds = null;

  function getDeckProgressPointerData(event){
    const progressBar = els.deckProgressBar;
    if (!progressBar || !event) return null;

    const rect = progressBar.getBoundingClientRect();
    const barWidth = Number(rect.width || progressBar.clientWidth || 0);
    if (!(barWidth > 0)) return null;

    let localX = Number(event.clientX) - Number(rect.left || 0);
    if (!Number.isFinite(localX)) return null;
    localX = Math.max(0, Math.min(barWidth, localX));

    const percent = localX / barWidth;
    const rawHoverSeconds = (studioCurrentDuration > 0) ? (percent * studioCurrentDuration) : 0;
    const hoverSeconds = Math.max(0, Math.floor(rawHoverSeconds));

    return { localX, percent, hoverSeconds };
  }

  function updateDeckProgressTooltip(event){
    const tooltip = els.deckProgressTooltip;
    if (!tooltip) return;

    const pointer = getDeckProgressPointerData(event);
    if (!pointer) return;

    deckHoverSeekSeconds = pointer.hoverSeconds;
    tooltip.textContent = formatSeconds(pointer.hoverSeconds);
    tooltip.style.left = `${pointer.localX}px`;
    tooltip.classList.add('is-visible');
    tooltip.setAttribute('aria-hidden', 'false');
  }

  function hideDeckProgressTooltip(){
    const tooltip = els.deckProgressTooltip;
    deckHoverSeekSeconds = null;
    if (!tooltip) return;
    tooltip.classList.remove('is-visible');
    tooltip.setAttribute('aria-hidden', 'true');
  }

  async function handleDeckProgressSeek(event){
    if (!els.deckProgressBar) return;
    if (isHttpStreamPath(studioLastSongFile)) return;

    const pointer = getDeckProgressPointerData(event);
    const target = Number.isFinite(deckHoverSeekSeconds) ? deckHoverSeekSeconds : (pointer ? pointer.hoverSeconds : null);

    if (target === null || Number.isNaN(target)) return;


    try{
      const response = await fetch('/api/seek', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ target_pos: target, target_label: formatSeconds(target) })
      });
      const result = await response.json().catch(() => ({}));
        if (!response.ok || !result || result.success === false) {
        throw new Error((result && result.error) || `HTTP ${response.status}`);
      }

      const nextElapsed = Number.isFinite(Number(result.elapsed)) ? Number(result.elapsed) : target;
      const nextDuration = Number.isFinite(Number(result.duration)) ? Number(result.duration) : studioCurrentDuration;
      const nextDurationDisplay = ((result.duration_display || '') + '').trim() || formatSeconds(nextDuration);
      const pct = nextDuration > 0 ? Math.min(100, Math.max(0, (nextElapsed / nextDuration) * 100)) : 0;

      setStudioProgressAnchor(nextElapsed);
      if (nextDuration > 0) {
        studioCurrentDuration = nextDuration;
      }
      studioCurrentDurationDisplay = nextDurationDisplay;

      if (els.deckProgressFill) els.deckProgressFill.style.width = `${pct}%`;
      if (els.deckTime) {
        els.deckTime.textContent = `${formatSeconds(nextElapsed)} / ${nextDurationDisplay || formatSeconds(nextDuration)}`;
      }

      setTimeout(loadStatus, 150);
      setTimeout(loadStatus, 600);
    }catch(err){
        console.error('Failed to seek:', err);
    }
  }

  function initializeDeckProgressSeek(){
    if (!els.deckProgressBar) return;
    els.deckProgressBar.removeEventListener('click', handleDeckProgressSeek);
    els.deckProgressBar.addEventListener('click', handleDeckProgressSeek);
    els.deckProgressBar.removeEventListener('mousemove', updateDeckProgressTooltip);
    els.deckProgressBar.removeEventListener('mouseenter', updateDeckProgressTooltip);
    els.deckProgressBar.removeEventListener('mouseleave', hideDeckProgressTooltip);
    els.deckProgressBar.addEventListener('mousemove', updateDeckProgressTooltip);
    els.deckProgressBar.addEventListener('mouseenter', updateDeckProgressTooltip);
    els.deckProgressBar.addEventListener('mouseleave', hideDeckProgressTooltip);
  }

  function initializeDeckOnAirButton(){
    const studioButton = els.deckOnAirButton;
    if (!studioButton) return;

    syncDeckOnAirButton();

    const classicToggle = document.getElementById('audio-engine-toggle');
    if (classicToggle) {
      const observer = new MutationObserver(() => {
        syncDeckOnAirButton();
      });
      observer.observe(classicToggle, {
        attributes: true,
        attributeFilter: ['data-state', 'disabled', 'class', 'title', 'aria-label']
      });
    }

    studioButton.addEventListener('click', async event => {
      event.preventDefault();
      event.stopPropagation();
      event.stopImmediatePropagation();
      await toggleDeckOnAirButton();
    }, true);
  }

  function setStudioManualNextPending(active, pendingCount){
    const nextButton = els.nextControl;
    studioManualNextPending = Boolean(active);
    if (studioManualNextPending) {
      if (!studioManualNextPendingSince) studioManualNextPendingSince = Date.now();
    } else {
      studioManualNextPendingSince = 0;
    }
    if (!nextButton) return;
    const count = Math.max(0, Number(pendingCount) || 0);
    if (!nextButton.dataset.defaultLabel) {
      nextButton.dataset.defaultLabel = (nextButton.textContent || 'Next').trim() || 'Next';
    }
    nextButton.disabled = studioManualNextPending;
    nextButton.classList.toggle('is-pending', studioManualNextPending);
    nextButton.setAttribute('aria-busy', studioManualNextPending ? 'true' : 'false');
    nextButton.textContent = studioManualNextPending
      ? (count > 1 ? `Next… (${count})` : 'Next…')
      : nextButton.dataset.defaultLabel;
    nextButton.title = studioManualNextPending
      ? 'The next track is being prepared and committed.'
      : '';
  }

  async function loadStatus(){
    try{
      const data = await jsonFetch('/api/status?with_progress=1');
      const manualNext = data.manual_next || {};
      setStudioManualNextPending(Boolean(manualNext.in_progress), Number(manualNext.pending_count || 0));
      const song = data.song || {};
      const rawTitle = song.title || '';
      const rawArtist = song.artist || '';
      const rawYear = song.year || '';
      let statusValue = (data.status || 'stopped').toString();
      const pauseActive = Boolean(data.paused || data.pause_active || statusValue === 'pause');
      if (studioStopUiOverride && statusValue !== 'play') {
        statusValue = 'stopped';
      }
      const isOffAir = statusValue === 'stopped';
      const title = isOffAir ? 'No active track' : (rawTitle || '—');
      const artist = isOffAir ? 'No active track' : (rawArtist || '—');
      const year = rawYear || '';
      const serverElapsed = Number(song.elapsed || 0);
      const serverDuration = Number(song.duration || 0);
      const serverDurationDisplay = (song.duration_display || '').toString();
      const songFile = song.file || null;
      const songProgressIdentity = studioSongProgressIdentity(song);
      let effectivePauseActive = pauseActive;
      const effectiveStopActive = Boolean(studioStopUiOverride || statusValue === 'stopped');

      // Keep a local pause latch so an older in-flight status poll cannot clear the
      // blinking/frozen UI immediately after the user clicks Pause. The backend can
      // still confirm resume by returning paused=false after a resume command.
      if (studioPauseUiOverride === true) {
        effectivePauseActive = true;
      } else if (studioPauseUiOverride === false && !pauseActive) {
        studioPauseUiOverride = null;
      }

      let duration = serverDuration;
      let elapsed = effectiveStopActive ? 0 : serverElapsed;
      let durationDisplay = serverDurationDisplay;
      const resumeHoldActive = !effectiveStopActive && !effectivePauseActive && studioResumeHoldUntil > Date.now();
      if (resumeHoldActive) {
        duration = Number(studioCurrentDuration) > 0 ? Number(studioCurrentDuration) : serverDuration;
        durationDisplay = studioCurrentDurationDisplay || serverDurationDisplay;
        elapsed = readStudioProgressElapsed(duration);
      }
      if (!effectiveStopActive && effectivePauseActive) {
        if (!studioIsPaused) {
          const liveElapsed = readStudioProgressElapsed(duration);
          studioPauseFrozenElapsed = Number.isFinite(liveElapsed) ? liveElapsed : serverElapsed;
          studioPauseFrozenDuration = Number.isFinite(studioCurrentDuration) ? studioCurrentDuration : serverDuration;
          studioPauseFrozenDurationDisplay = studioCurrentDurationDisplay || serverDurationDisplay;
        }
        elapsed = Number.isFinite(studioPauseFrozenElapsed) ? studioPauseFrozenElapsed : studioCurrentElapsed;
        duration = Number.isFinite(studioPauseFrozenDuration) && studioPauseFrozenDuration > 0 ? studioPauseFrozenDuration : serverDuration;
        durationDisplay = studioPauseFrozenDurationDisplay || serverDurationDisplay;
      }
      if (effectiveStopActive) {
        elapsed = 0;
        setStudioProgressAnchor(0, '');
      } else if (effectivePauseActive) {
        elapsed = setStudioProgressAnchor(elapsed, songProgressIdentity);
      } else if (!resumeHoldActive) {
        const forceHardSync = (
          songProgressIdentity !== studioProgressIdentity
          || !studioIsPlaying
          || studioIsPaused
        );
        elapsed = syncStudioProgressFromServer(
          serverElapsed,
          duration,
          songProgressIdentity,
          forceHardSync
        );
      } else {
        setStudioProgressAnchor(elapsed, songProgressIdentity);
      }

      const pct = duration > 0 ? Math.min(100, (elapsed / duration) * 100) : 0;
      studioCurrentDuration = duration;
      studioCurrentDurationDisplay = durationDisplay;
      studioIsPlaying = statusValue === 'play' && !effectivePauseActive && !effectiveStopActive;
      studioIsPaused = effectivePauseActive && !effectiveStopActive;
      studioLastSongFile = songFile;
      const queueEtaDuration = Number(duration) || 0;
      const queueEtaElapsed = Number(elapsed) || 0;
      if (statusValue === 'play' && !effectiveStopActive && queueEtaDuration > 0) {
        const remainingSeconds = Math.max(0, queueEtaDuration - queueEtaElapsed);
        const nextQueueEtaBaseMs = Date.now() + (remainingSeconds * 1000);
        const nextQueueEtaSignature = [songFile || '', Math.round(queueEtaDuration * 10) / 10, effectivePauseActive ? 'pause' : 'play'].join('|');
        if (studioQueueEtaSignature !== nextQueueEtaSignature || effectivePauseActive) {
          studioQueueEtaBaseMs = nextQueueEtaBaseMs;
          studioQueueEtaSignature = nextQueueEtaSignature;
        } else if (Math.abs(nextQueueEtaBaseMs - studioQueueEtaBaseMs) > 1500) {
          studioQueueEtaBaseMs = nextQueueEtaBaseMs;
        }
      } else {
        studioQueueEtaBaseMs = 0;
        studioQueueEtaSignature = '';
      }
      if (els.deckArtist) els.deckArtist.textContent = `Artist: ${artist}`;
      if (els.deckTitle) els.deckTitle.textContent = `Title: ${title}`;
      if (els.deckYear) els.deckYear.textContent = `Year: ${year}`;
      if (els.deckProgressFill) els.deckProgressFill.style.width = `${pct}%`;
      if (els.deckTime) {
        els.deckTime.textContent = `${formatSeconds(elapsed)} / ${durationDisplay || formatSeconds(duration)}`;
        els.deckTime.classList.toggle('is-paused', effectivePauseActive && !effectiveStopActive);
      }
      if (els.statusMeta) els.statusMeta.textContent = artist ? `${artist} — ${title}` : title;
      if (els.statusPill) {
        els.statusPill.textContent = (effectiveStopActive ? 'stopped' : (effectivePauseActive ? 'pause' : statusValue)).toUpperCase();
        els.statusPill.classList.toggle('is-live', statusValue === 'play' && !effectivePauseActive && !effectiveStopActive);
        els.statusPill.classList.toggle('is-paused', effectivePauseActive && !effectiveStopActive);
      }
      const autodjNotice = data.autodj_notice || null;
      if (autodjNotice && autodjNotice.code === 'NO_MATCHING_TRACKS') {
        showStudioAutodjToast(
          autodjNotice.message || 'AutoDJ could not find matching tracks.',
          data.station_id || '',
          autodjNotice.ts || 0
        );
      } else {
        hideStudioAutodjToast();
      }
      // The separate on-air button sync is throttled below. Do not call
      // /api/audio-engine/status after every status tick.
    }catch(err){
      if (studioManualNextPendingSince && (Date.now() - studioManualNextPendingSince) > 35000) {
        setStudioManualNextPending(false, 0);
      }
      setStudioProgressAnchor(0, '');
      studioCurrentDuration = 0;
      studioCurrentDurationDisplay = '';
      studioIsPlaying = false;
      studioIsPaused = false;
      studioLastSongFile = null;
      if (els.statusMeta) els.statusMeta.textContent = 'Status unavailable';
      if (els.deckTitle) els.deckTitle.textContent = 'Status unavailable';
      if (els.deckTime) els.deckTime.classList.remove('is-paused');
      studioQueueEtaBaseMs = 0;
      studioQueueEtaSignature = '';
      hideStudioAutodjToast();
    }
  }

  function updateDeckProgressLocalClock(){
    if (!studioIsPlaying || studioIsPaused) return;
    const duration = Number(studioCurrentDuration) || 0;
    if (!(duration > 0)) return;
    const elapsed = readStudioProgressElapsed(duration);
    studioCurrentElapsed = elapsed;
    const pct = Math.min(100, Math.max(0, (elapsed / duration) * 100));
    if (els.deckProgressFill) els.deckProgressFill.style.width = `${pct}%`;
    if (els.deckTime) {
      els.deckTime.textContent = `${formatSeconds(elapsed)} / ${studioCurrentDurationDisplay || formatSeconds(duration)}`;
      els.deckTime.classList.remove('is-paused');
    }
  }

  function updateQueueEtaLocalClock(){
    if (!els.queueList || !Array.isArray(currentQueueItems) || !currentQueueItems.length) return;
    const etaCells = els.queueList.querySelectorAll('.queue-table__eta');
    if (!etaCells || !etaCells.length) return;
    const baseMs = studioQueueEtaBaseMs > 0 ? studioQueueEtaBaseMs : Date.now();
    let runningEtaSeconds = 0;
    currentQueueItems.forEach((item, index) => {
      const cell = etaCells[index];
      if (cell) cell.textContent = formatClockTime(new Date(baseMs + (runningEtaSeconds * 1000)));
      runningEtaSeconds += Math.max(0, Number(item && item.cue_duration_seconds) || 0);
    });
  }



  async function loadQueue(){
    if (queueDragSourceId || queueReorderInFlight) return;
    try{
      const data = await jsonFetch('/api/queue');
      const items = data.queue || [];
      const totalSeconds = items.reduce((sum, item) => sum + Math.max(0, Number(item.cue_duration_seconds) || 0), 0);
      renderQueueTable(items, 'Queue is empty', totalSeconds);
    }catch(err){
      renderQueueTable([], 'Queue unavailable', 0);
    }
  }

  async function loadHistory(){
    try{
      const data = await jsonFetch('/api/history');
      const items = (data.items || []).slice(0, 200);
      const totalSeconds = items.reduce((sum, item) => sum + Math.max(0, Number(item.cue_duration_seconds) || 0), 0);
      renderHistoryTable(items, 'No history yet', totalSeconds);
    }catch(err){ renderHistoryTable([], 'History unavailable', 0); }
  }

  function scheduleQueueHistoryRefresh(reason){
    if (studioQueueHistoryRefreshTimer) {
      clearTimeout(studioQueueHistoryRefreshTimer);
      studioQueueHistoryRefreshTimer = 0;
    }
    studioQueueHistoryRefreshTimer = setTimeout(async () => {
      studioQueueHistoryRefreshTimer = 0;
      if (studioQueueHistoryRefreshInFlight) {
        scheduleQueueHistoryRefresh(reason || 'coalesced');
        return;
      }
      studioQueueHistoryRefreshInFlight = true;
      try {
        await Promise.all([loadQueue(), loadHistory()]);
      } catch (_) {
        // Individual loaders already handle their own UI fallback.
      } finally {
        studioQueueHistoryRefreshInFlight = false;
      }
    }, 80);
  }

  function rememberStudioUiEventSequence(event, payload){
    try {
      if (event && event.lastEventId) {
        const seq = Number(event.lastEventId);
        if (Number.isFinite(seq) && seq > 0) studioLastUiEventSeq = seq;
      }
      const payloadSeq = Number(payload && payload.seq);
      if (Number.isFinite(payloadSeq) && payloadSeq > 0) studioLastUiEventSeq = payloadSeq;
    } catch (_) {}
  }

  function parseStudioUiEvent(event){
    let payload = null;
    try { payload = JSON.parse((event && event.data) || '{}'); } catch (_) { payload = {}; }
    rememberStudioUiEventSequence(event, payload);
    return payload || {};
  }

  function clearStudioRefreshTimers(timers){
    while (timers.length) {
      const timerId = timers.pop();
      try { clearTimeout(timerId); } catch (_) {}
    }
  }

  function scheduleEncoderEventRefresh(){
    clearStudioRefreshTimers(studioEncoderEventRefreshTimers);
    loadEncoders();
    [500, 1500, 3000, 6000].forEach(delay => {
      studioEncoderEventRefreshTimers.push(setTimeout(() => { loadEncoders(); }, delay));
    });
  }

  async function refreshOnAirUiFromEvent(){
    const classicToggle = document.getElementById('audio-engine-toggle');
    if (window.webBroadcasterAudioEngine && typeof window.webBroadcasterAudioEngine.refresh === 'function' && classicToggle) {
      try { await window.webBroadcasterAudioEngine.refresh(classicToggle); } catch (_) {}
    }
    await syncDeckOnAirButton();
    loadStatus();
  }

  function scheduleOnAirEventRefresh(){
    clearStudioRefreshTimers(studioOnAirEventRefreshTimers);
    refreshOnAirUiFromEvent();
    [350, 1000, 2500].forEach(delay => {
      studioOnAirEventRefreshTimers.push(setTimeout(() => { refreshOnAirUiFromEvent(); }, delay));
    });
    // Autostart encoders can connect shortly after the native audio engine becomes ready.
    scheduleEncoderEventRefresh();
  }

  function initializeStudioUiEvents(){
    if (!window.EventSource) return;
    try {
      if (studioUiEventsSource) {
        studioUiEventsSource.close();
        studioUiEventsSource = null;
      }
      studioUiEventsSource = new EventSource(`/api/ui/events?last=${encodeURIComponent(String(studioLastUiEventSeq || 0))}`);
      studioUiEventsSource.addEventListener('queue_history_changed', event => {
        const payload = parseStudioUiEvent(event);
        const reason = payload && payload.reason ? String(payload.reason) : 'queue_history_changed';
        scheduleQueueHistoryRefresh(reason);
      });
      studioUiEventsSource.addEventListener('on_air_state_changed', event => {
        parseStudioUiEvent(event);
        scheduleOnAirEventRefresh();
      });
      studioUiEventsSource.addEventListener('encoders_changed', event => {
        parseStudioUiEvent(event);
        scheduleEncoderEventRefresh();
      });
      window.addEventListener('beforeunload', () => {
        clearStudioRefreshTimers(studioEncoderEventRefreshTimers);
        clearStudioRefreshTimers(studioOnAirEventRefreshTimers);
        try {
          if (studioUiEventsSource) studioUiEventsSource.close();
        } catch (_) {}
      }, { once: true });
    } catch (_) {
      try {
        if (studioUiEventsSource) studioUiEventsSource.close();
      } catch (__){ }
      studioUiEventsSource = null;
    }
  }

  async function loadCategories(){
    try{
      const data = await jsonFetch('/api/library/categories');
      const categories = data.categories || [];
      if (!selectedCategoryId && categories.length) selectedCategoryId = categories[0].id;
      renderPlaylistTree(categories, 'No categories');
      els.playlistCategories.querySelectorAll('[data-category-id]').forEach(btn => {
        btn.addEventListener('click', event => {
          if (event.target.closest('.playlist-tree__rename-input')) return;
          selectedCategoryId = btn.dataset.categoryId;
          renderPlaylistTreeFromDomSelection();
          closePlaylistContextMenu();
          loadTracks();
        });
      });
      await loadTracks();
    }catch(err){ renderList(els.playlistCategories, [], 'Categories unavailable'); }
  }

  function selectAllTracksInSelectedCategory(){
    const trackIds = currentRenderedTracks.map(track => String(track && track.id != null ? track.id : '')).filter(Boolean);
    if (!trackIds.length) return;
    selectedTrackIds.clear();
    trackIds.forEach(trackId => selectedTrackIds.add(trackId));
    lastSelectedTrackId = trackIds[trackIds.length - 1] || null;
    renderTracksTable(currentRenderedTracks, currentRenderedTracksEmptyText);
  }

  function renderTracksTable(tracks, emptyText){
    if (!els.tracksList) return;
    currentRenderedTracks = Array.isArray(tracks) ? tracks.slice() : [];
    currentRenderedTracksEmptyText = emptyText || 'No tracks in category';
    if (els.playlistTracksSection && !els.playlistTracksSection.hasAttribute('tabindex')) {
      els.playlistTracksSection.setAttribute('tabindex', '0');
    }
    if (!tracks || !tracks.length) {
      selectedTrackIds.clear();
      lastSelectedTrackId = null;
      els.tracksList.innerHTML = `<div class="queue-table__empty">${escapeHtml(emptyText || 'No tracks in category')}</div>`;
      if (els.tracksSummary) els.tracksSummary.textContent = formatQueueSummary(0, 0);
      return;
    }

    const sortedTracks = [...tracks].sort((a, b) => {
      const aLabel = String(a.filename || a.path || '').toLocaleLowerCase();
      const bLabel = String(b.filename || b.path || '').toLocaleLowerCase();
      return aLabel.localeCompare(bLabel, undefined, {numeric: true, sensitivity: 'base'});
    });

    const validTrackIds = new Set(sortedTracks.map(track => String(track.id)));
    Array.from(selectedTrackIds).forEach(trackId => {
      if (!validTrackIds.has(String(trackId))) selectedTrackIds.delete(String(trackId));
    });

    els.tracksList.innerHTML = sortedTracks.map((track, idx) => {
      const trackId = String(track.id);
      const selectedClass = selectedTrackIds.has(trackId) ? ' is-selected' : '';
      return `
        <button class="queue-table__row queue-table__row--draggable${selectedClass}" type="button" data-track-id="${trackId}" draggable="true">
          <div class="queue-table__eta">${idx + 1}</div>
          <div class="queue-table__title">${escapeHtml(track.filename || track.path || 'Untitled')}</div>
          <div class="queue-table__duration">${formatSeconds(track.cue_duration_seconds)}</div>
        </button>
      `;
    }).join('');

    const trackIdsInOrder = sortedTracks.map(track => String(track.id));
    els.tracksList.querySelectorAll('[data-track-id]').forEach(row => {
      row.addEventListener('click', event => {
        if (els.playlistTracksSection && typeof els.playlistTracksSection.focus === 'function') {
          try { els.playlistTracksSection.focus({preventScroll: true}); } catch (_err) { try { els.playlistTracksSection.focus(); } catch (_err2) {} }
        }
        const trackId = String(row.dataset.trackId || '');
        if (!trackId) return;
        const isDoubleClick = event.detail >= 2;

        if (event.shiftKey && trackIdsInOrder.length) {
          const anchorId = lastSelectedTrackId && trackIdsInOrder.includes(lastSelectedTrackId)
            ? lastSelectedTrackId
            : (selectedTrackIds.size ? Array.from(selectedTrackIds).find(id => trackIdsInOrder.includes(id)) : null);
          if (anchorId) {
            const startIndex = trackIdsInOrder.indexOf(anchorId);
            const endIndex = trackIdsInOrder.indexOf(trackId);
            if (startIndex !== -1 && endIndex !== -1) {
              const fromIndex = Math.min(startIndex, endIndex);
              const toIndex = Math.max(startIndex, endIndex);
              selectedTrackIds.clear();
              for (let i = fromIndex; i <= toIndex; i += 1) {
                selectedTrackIds.add(trackIdsInOrder[i]);
              }
            } else {
              selectedTrackIds.clear();
              selectedTrackIds.add(trackId);
            }
          } else {
            selectedTrackIds.clear();
            selectedTrackIds.add(trackId);
          }
        } else if (event.ctrlKey || event.metaKey) {
          if (selectedTrackIds.has(trackId)) selectedTrackIds.delete(trackId);
          else selectedTrackIds.add(trackId);
        } else {
          const singleSelected = selectedTrackIds.size === 1 && selectedTrackIds.has(trackId);
          selectedTrackIds.clear();
          if (!singleSelected) selectedTrackIds.add(trackId);
        }

        lastSelectedTrackId = trackId;
        renderTracksTable(sortedTracks, emptyText);
        if (isDoubleClick) {
          window.setTimeout(() => {
            addTracksToQueue([trackId]).catch(err => {
              console.error('Unable to add double-clicked track to queue', err);
            });
          }, 0);
        }
      });

      row.addEventListener('dragstart', event => {
        const trackId = String(row.dataset.trackId || '');
        if (!trackId) return;
        let dragIds = [];
        if (selectedTrackIds.has(trackId) && selectedTrackIds.size > 0) {
          dragIds = trackIdsInOrder.filter(id => selectedTrackIds.has(id));
        } else {
          selectedTrackIds.clear();
          selectedTrackIds.add(trackId);
          lastSelectedTrackId = trackId;
          dragIds = [trackId];
          renderTracksTable(sortedTracks, emptyText);
        }
        playlistTrackDragIds = dragIds.slice();
        row.classList.add('is-dragging');
        if (event.dataTransfer) {
          event.dataTransfer.effectAllowed = 'copy';
          event.dataTransfer.setData('text/plain', dragIds.join(','));
        }
      });

      row.addEventListener('dragend', () => {
        row.classList.remove('is-dragging');
        playlistTrackDragIds = [];
        if (els.queueList) els.queueList.classList.remove('is-track-drop-target');
      });

    });

    const totalSeconds = sortedTracks.reduce((sum, track) => sum + Math.max(0, Number(track.cue_duration_seconds) || 0), 0);
    if (els.tracksSummary) els.tracksSummary.textContent = formatQueueSummary(totalSeconds, sortedTracks.length);
  }

  async function loadTracks(){
    if (!selectedCategoryId){
      renderTracksTable([], 'No category selected');
      if (els.tracksCategoryName) els.tracksCategoryName.textContent = 'No category selected';
      return;
    }
    try{
      const activeLabel = (els.playlistCategories.querySelector('[data-category-id].is-selected .playlist-tree__label') || {}).textContent || 'Selected category';
      if (els.tracksCategoryName) els.tracksCategoryName.textContent = activeLabel;
      const data = await jsonFetch(`/api/library/category/${selectedCategoryId}/tracks`);
      renderTracksTable((data.tracks || []), 'No tracks in category');
    }catch(err){ renderTracksTable([], 'Tracks unavailable'); }
  }

  document.addEventListener('keydown', event => {
    if (!(event.ctrlKey || event.metaKey)) return;
    if (String(event.key || '').toLowerCase() !== 'a') return;
    const active = document.activeElement;
    const tracksSection = els.playlistTracksSection;
    const tracksList = els.tracksList;
    const insideTracks = !!(
      (tracksSection && active && tracksSection.contains(active)) ||
      (tracksList && active && tracksList.contains(active)) ||
      (active === tracksSection) ||
      (active === tracksList)
    );
    if (!insideTracks) return;
    event.preventDefault();
    selectAllTracksInSelectedCategory();
  });

  document.addEventListener('keydown', event => {
    if (!(event.ctrlKey || event.metaKey)) return;
    if (String(event.key || '').toLowerCase() !== 'a') return;
    const active = document.activeElement;
    const queueList = els.queueList;
    const insideQueue = !!(
      (queueList && active && queueList.contains(active)) ||
      (active === queueList)
    );
    if (!insideQueue) return;
    event.preventDefault();
    selectAllQueueItems();
  });

  function updateEncoderToolbarState(){
    const hasSelection = Boolean(selectedEncoderId);
    if (els.encodersStartBtn) els.encodersStartBtn.disabled = !hasSelection;
    if (els.encodersStopBtn) els.encodersStopBtn.disabled = !hasSelection;
    if (els.encodersConfigBtn) els.encodersConfigBtn.disabled = !hasSelection;
    if (els.encodersRemoveBtn) els.encodersRemoveBtn.disabled = !hasSelection;
  }

  function formatEncoderElapsedSeconds(elapsedSeconds){
    const totalSeconds = Math.max(0, Math.floor(Number(elapsedSeconds) || 0));
    const days = Math.floor(totalSeconds / 86400);
    const hours = Math.floor((totalSeconds % 86400) / 3600);
    const minutes = Math.floor((totalSeconds % 3600) / 60);
    const seconds = totalSeconds % 60;
    const timeText = [hours, minutes, seconds].map(value => String(value).padStart(2, '0')).join(':');
    return days > 0 ? `${days}d, ${timeText}` : timeText;
  }

  function getStudioEncoderNowMs(){
    return Date.now() + (Number(studioEncoderServerOffsetMs) || 0);
  }

  function isEncoderRuntimeStreaming(stream){
    if (!stream) return false;
    const runtimeStatus = String(stream.runtime_status || '').trim().toLowerCase();
    return Boolean(
      stream.running &&
      stream.station_running &&
      stream.connected &&
      runtimeStatus === 'streaming'
    );
  }

  function getEncoderElapsedSeconds(stream){
    if (!isEncoderRuntimeStreaming(stream)) return 0;
    const startedEpoch = Number(stream.started_at_epoch);
    if (Number.isFinite(startedEpoch) && startedEpoch > 0) {
      return Math.max(0, Math.floor((getStudioEncoderNowMs() - startedEpoch * 1000) / 1000));
    }
    return Math.max(0, Math.floor(Number(stream.elapsed_seconds) || 0));
  }

  function resetEncoderUiCounter(streamId){
    studioEncoderStreamsData = (studioEncoderStreamsData || []).map(stream => {
      if (String(stream && stream.id) !== String(streamId)) return stream;
      return Object.assign({}, stream, {
        running: false,
        connected: false,
        station_running: true,
        state: 'Starting',
        runtime_status: 'starting',
        started_at_epoch: null,
        elapsed_seconds: 0
      });
    });
    renderEncoders(studioEncoderStreamsData);
  }

  function clearEncoderUiCounter(streamId){
    studioEncoderStreamsData = (studioEncoderStreamsData || []).map(stream => {
      if (String(stream && stream.id) !== String(streamId)) return stream;
      return Object.assign({}, stream, {
        running: false,
        state: 'Stopped',
        started_at_epoch: null,
        elapsed_seconds: 0
      });
    });
    renderEncoders(studioEncoderStreamsData);
  }

  function updateEncoderElapsedDisplays(){
    if (!els.encodersList || !studioEncoderStreamsData || !studioEncoderStreamsData.length) return;
    studioEncoderStreamsData.forEach(stream => {
      const streamId = String(stream && stream.id || '');
      if (!streamId) return;
      const row = Array.from(els.encodersList.querySelectorAll('[data-encoder-id]'))
        .find(item => String(item.dataset.encoderId || '') === streamId);
      if (!row) return;
      const desc = row.querySelector('[data-encoder-elapsed]');
      if (!desc) return;
      desc.textContent = isEncoderRuntimeStreaming(stream)
        ? `Encoded (${formatEncoderElapsedSeconds(getEncoderElapsedSeconds(stream))})`
        : (String(stream.state || '') === 'Starting' ? 'Encoder starting' : 'Encoder not started');
    });
  }

  function renderEncoders(streams){
    const list = Array.isArray(streams) ? streams : [];
    const validEncoderIds = new Set(list.map(stream => String(stream && stream.id)));
    if (selectedEncoderId && !validEncoderIds.has(String(selectedEncoderId))) selectedEncoderId = null;
    const rows = list.map((stream, index) => {
      const codec = String(stream.codec || '').trim();
      const bitrate = stream.bitrate != null && String(stream.bitrate).trim() !== '' ? `${String(stream.bitrate).trim()} kbps` : '';
      const formatParts = [codec, bitrate].filter(Boolean).join(' / ');
      const streamId = String(stream.id || '');
      const isStreaming = isEncoderRuntimeStreaming(stream);
      const isStarting = !isStreaming && String(stream.state || '') === 'Starting';
      const stateText = isStreaming ? 'Encoding' : (isStarting ? 'Starting' : 'Idle');
      const stateClass = isStreaming ? 'is-running' : 'is-stopped';
      const descriptionText = isStreaming
        ? `Encoded (${formatEncoderElapsedSeconds(getEncoderElapsedSeconds(stream))})`
        : (isStarting ? 'Encoder starting' : 'Encoder not started');
      const selectedClass = selectedEncoderId === streamId ? ' is-selected' : '';
      return `
        <button class="queue-table__row queue-table__row--static encoder-table__row${selectedClass}" type="button" data-encoder-id="${escapeHtml(streamId)}">
          <div class="encoder-table__index">${index + 1}</div>
          <div class="encoder-table__format">${escapeHtml(formatParts || 'Unknown')}</div>
          <div class="encoder-table__status"><span class="${stateClass}">${escapeHtml(stateText)}</span></div>
          <div class="encoder-table__description" data-encoder-elapsed="1">${escapeHtml(descriptionText)}</div>
        </button>
      `;
    });
    renderList(els.encodersList, rows, 'No encoders', 'queue-table__empty');
    if (els.encodersList) {
      els.encodersList.querySelectorAll('[data-encoder-id]').forEach(row => {
        row.addEventListener('click', () => {
          const streamId = String(row.dataset.encoderId || '');
          if (!streamId) return;
          selectedEncoderId = selectedEncoderId === streamId ? null : streamId;
          renderEncoders(studioEncoderStreamsData);
        });
      });
    }
    updateEncoderToolbarState();
  }

  async function postEncoderAction(streamId, action){
    if (!streamId || (action !== 'start' && action !== 'stop')) return false;
    try{
      await jsonFetch(`/api/encoders/${encodeURIComponent(String(streamId))}/${action}`, {
        method: 'POST'
      });
      return true;
    }catch(_err){
      return false;
    }
  }

  async function waitForEncoderState(streamId, wantRunning, timeoutMs){
    const deadline = Date.now() + (timeoutMs || 12000);
    while (Date.now() < deadline) {
      try{
        const data = await jsonFetch('/api/encoders');
        const stream = (data.streams || []).find(item => String(item.id) === String(streamId));
        if (stream && Boolean(stream.running) === Boolean(wantRunning)) return true;
      }catch(_err){}
      await new Promise(resolve => window.setTimeout(resolve, 700));
    }
    return false;
  }

  async function handleEncoderToolbarAction(action){
    const streamId = selectedEncoderId;
    if (!streamId) return;
    const button = action === 'start' ? els.encodersStartBtn : els.encodersStopBtn;
    const otherButton = action === 'start' ? els.encodersStopBtn : els.encodersStartBtn;
    if (button) button.disabled = true;
    if (otherButton) otherButton.disabled = true;
    const submitted = await postEncoderAction(streamId, action);
    if (submitted && action === 'start') resetEncoderUiCounter(streamId);
    if (submitted && action === 'stop') clearEncoderUiCounter(streamId);
    if (submitted) await waitForEncoderState(streamId, action === 'start', 12000);
    await loadEncoders();
  }


  async function fetchEncoderConfig(streamId){
    if (!streamId) return null;
    try{
      const data = await jsonFetch(`/api/encoders/${encodeURIComponent(String(streamId))}`);
      return data && data.stream ? data.stream : null;
    }catch(_err){
      return null;
    }
  }

  function setEncoderConfigFormValues(stream){
    if (!stream) return;
    if (els.encoderConfigHost) els.encoderConfigHost.value = stream.host || '';
    if (els.encoderConfigPort) els.encoderConfigPort.value = stream.port != null ? String(stream.port) : '8000';
    if (els.encoderConfigPassword) els.encoderConfigPassword.value = stream.password || '';
    if (els.encoderConfigMount) els.encoderConfigMount.value = stream.mount || '';
    if (els.encoderConfigCodec) els.encoderConfigCodec.value = String(stream.codec || 'mp3').toLowerCase() === 'aacplusv2' ? 'aacplusv2' : 'mp3';
    if (els.encoderConfigBitrate) els.encoderConfigBitrate.value = stream.bitrate != null ? String(stream.bitrate) : '128';
    if (els.encoderConfigName) els.encoderConfigName.value = stream.name || '';
    if (els.encoderConfigDescription) els.encoderConfigDescription.value = stream.station_description || '';
    if (els.encoderConfigGenre) els.encoderConfigGenre.value = stream.genre || '';
    if (els.encoderConfigWebsite) els.encoderConfigWebsite.value = stream.website_url || '';
    if (els.encoderConfigAutostart) els.encoderConfigAutostart.checked = !!stream.autostart;
    if (els.encoderConfigAddYearToIcecastMeta) els.encoderConfigAddYearToIcecastMeta.checked = !!stream.add_year_to_icecast_meta;
  }

  function resetEncoderConfigFormValues(){
    setEncoderConfigFormValues({
      host: '',
      port: '8000',
      password: '',
      mount: '',
      codec: 'mp3',
      bitrate: '128',
      name: '',
      station_description: '',
      genre: '',
      website_url: '',
      autostart: false,
      add_year_to_icecast_meta: false
    });
  }

  function readEncoderConfigFormValues(){
    return {
      host: els.encoderConfigHost ? els.encoderConfigHost.value.trim() : '',
      port: els.encoderConfigPort ? els.encoderConfigPort.value.trim() : '8000',
      password: els.encoderConfigPassword ? els.encoderConfigPassword.value : '',
      mount: els.encoderConfigMount ? els.encoderConfigMount.value.trim() : '',
      codec: els.encoderConfigCodec ? els.encoderConfigCodec.value : 'mp3',
      bitrate: els.encoderConfigBitrate ? els.encoderConfigBitrate.value : '128',
      name: els.encoderConfigName ? els.encoderConfigName.value.trim() : '',
      station_description: els.encoderConfigDescription ? els.encoderConfigDescription.value.trim() : '',
      genre: els.encoderConfigGenre ? els.encoderConfigGenre.value.trim() : '',
      website_url: els.encoderConfigWebsite ? els.encoderConfigWebsite.value.trim() : '',
      autostart: !!(els.encoderConfigAutostart && els.encoderConfigAutostart.checked),
      add_year_to_icecast_meta: !!(els.encoderConfigAddYearToIcecastMeta && els.encoderConfigAddYearToIcecastMeta.checked)
    };
  }

  function openEncoderConfigModal(options){
    const backdrop = els.encoderConfigBackdrop;
    const okBtn = els.encoderConfigOk;
    const cancelBtn = els.encoderConfigCancel;
    const closeBtn = els.encoderConfigClose;
    const opts = options && typeof options === 'object' ? options : { streamId: options };
    const streamId = opts && opts.streamId != null ? String(opts.streamId) : '';
    const isCreateMode = !!opts.isCreate;
    if (!backdrop || !okBtn || !cancelBtn) return Promise.resolve(false);
    if (!isCreateMode && !streamId) return Promise.resolve(false);
    return new Promise(async resolve => {
      if (isCreateMode) {
        resetEncoderConfigFormValues();
      } else {
        const stream = await fetchEncoderConfig(streamId);
        if (!stream) { resolve(false); return; }
        setEncoderConfigFormValues(stream);
      }
      let finished = false;
      const modal = backdrop.querySelector('.modal');
      const title = backdrop.querySelector('#encoder-config-title');
      const titlebar = backdrop.querySelector('#encoder-config-titlebar');
      let dragState = null;
      let resizeState = null;
      let suppressBackdropClickUntil = 0;
      if (title) title.textContent = isCreateMode ? 'Add Encoder' : 'Encoder Configuration';
      if (okBtn) okBtn.textContent = isCreateMode ? 'Add' : 'OK';
      okBtn.disabled = false;
      cancelBtn.disabled = false;
      backdrop.style.display = 'flex';
      backdrop.classList.add('active');
      backdrop.setAttribute('aria-hidden', 'false');
      document.body.classList.add('modal-open');
      backdrop.style.zIndex = '2147483646';
      if (modal) {
        modal.style.zIndex = '2147483647';
        modal.style.right = 'auto';
        modal.style.bottom = 'auto';
        modal.style.margin = '0';
        modal.style.transform = 'none';
        const viewportWidth = window.innerWidth || document.documentElement.clientWidth || 0;
        const viewportHeight = window.innerHeight || document.documentElement.clientHeight || 0;
        const modalWidth = modal.offsetWidth || modal.getBoundingClientRect().width || 560;
        const defaultHeight = 780;
        const centeredLeft = Math.round((viewportWidth - modalWidth) / 2);
        const centeredTop = Math.round((viewportHeight - defaultHeight) / 2);
        const initial = clampModalPosition(centeredLeft, centeredTop);
        applyModalRect(initial.left, initial.top, modalWidth, defaultHeight);
      }

      function clampModalPosition(left, top){
        if (!modal) return { left, top };
        const minLeft = 8;
        const minTop = 8;
        const maxLeft = Math.max(minLeft, window.innerWidth - modal.offsetWidth - 8);
        const maxTop = Math.max(minTop, window.innerHeight - modal.offsetHeight - 8);
        return {
          left: Math.max(minLeft, Math.min(maxLeft, left)),
          top: Math.max(minTop, Math.min(maxTop, top))
        };
      }

      function applyModalRect(left, top, width, height){
        if (!modal) return;
        const minWidth = 520;
        const minHeight = 420;
        const viewportWidth = window.innerWidth || document.documentElement.clientWidth || 0;
        const viewportHeight = window.innerHeight || document.documentElement.clientHeight || 0;
        const safeWidth = Math.max(minWidth, Math.min(width, Math.max(minWidth, viewportWidth - 16)));
        const safeHeight = Math.max(minHeight, Math.min(height, Math.max(minHeight, viewportHeight - 16)));
        modal.style.width = `${Math.round(safeWidth)}px`;
        modal.style.height = `${Math.round(safeHeight)}px`;
        const clamped = clampModalPosition(left, top);
        modal.style.left = `${clamped.left}px`;
        modal.style.top = `${clamped.top}px`;
        modal.style.transform = 'none';
      }

      function onPointerMove(event){
        if (dragState && modal) {
          const nextLeft = dragState.startLeft + (event.clientX - dragState.startClientX);
          const nextTop = dragState.startTop + (event.clientY - dragState.startClientY);
          applyModalRect(nextLeft, nextTop, dragState.width, dragState.height);
          dragState.moved = true;
          return;
        }
        if (resizeState && modal) {
          const nextWidth = resizeState.startWidth + (event.clientX - resizeState.startClientX);
          const nextHeight = resizeState.startHeight + (event.clientY - resizeState.startClientY);
          applyModalRect(resizeState.left, resizeState.top, nextWidth, nextHeight);
          resizeState.moved = true;
        }
      }

      function endPointerInteraction(){
        const moved = !!((dragState && dragState.moved) || (resizeState && resizeState.moved));
        dragState = null;
        resizeState = null;
        if (modal) {
          modal.classList.remove('is-dragging');
          modal.classList.remove('is-resizing');
        }
        document.removeEventListener('pointermove', onPointerMove);
        document.removeEventListener('pointerup', endPointerInteraction);
        document.removeEventListener('pointercancel', endPointerInteraction);
        if (moved) suppressBackdropClickUntil = Date.now() + 250;
      }

      function onTitlePointerDown(event){
        if (!modal || event.button !== 0) return;
        if (event.target.closest('button, a, input, select, textarea')) return;
        event.preventDefault();
        event.stopPropagation();
        const rect = modal.getBoundingClientRect();
        const inlineLeft = Number.parseFloat(modal.style.left);
        const inlineTop = Number.parseFloat(modal.style.top);
        dragState = {
          startClientX: event.clientX,
          startClientY: event.clientY,
          startLeft: Number.isFinite(inlineLeft) ? inlineLeft : rect.left,
          startTop: Number.isFinite(inlineTop) ? inlineTop : rect.top,
          width: modal.offsetWidth || rect.width,
          height: modal.offsetHeight || rect.height,
          moved: false
        };
        modal.classList.add('is-dragging');
        document.addEventListener('pointermove', onPointerMove);
        document.addEventListener('pointerup', endPointerInteraction);
        document.addEventListener('pointercancel', endPointerInteraction);
      }

      function cleanup(result){
        if (finished) return;
        finished = true;
        endPointerInteraction();
        backdrop.classList.remove('active');
        backdrop.setAttribute('aria-hidden', 'true');
        backdrop.style.display = 'none';
        okBtn.disabled = false;
        cancelBtn.disabled = false;
        document.body.classList.remove('modal-open');
        backdrop.removeEventListener('click', onBackdropClick, true);
        document.removeEventListener('keydown', onKeyDown, true);
        cancelBtn.removeEventListener('click', onCancel, true);
        okBtn.removeEventListener('click', onOk, true);
        if (titlebar) titlebar.removeEventListener('pointerdown', onTitlePointerDown, true);
        if (closeBtn) closeBtn.removeEventListener('click', onCancel, true);
        if (resizeHandle) resizeHandle.removeEventListener('pointerdown', onResizePointerDown, true);
        window.removeEventListener('resize', onViewportResize);
        if (title) title.textContent = 'Encoder Configuration';
        if (okBtn) okBtn.textContent = 'OK';
        resolve(result);
      }

      function onBackdropClick(event){
        if (event.target !== backdrop) return;
        if (dragState || resizeState || Date.now() < suppressBackdropClickUntil) return;
        cleanup(false);
      }
      function onCancel(){ cleanup(false); }
      async function onOk(){
        okBtn.disabled = true;
        cancelBtn.disabled = true;
        try{
          const payload = readEncoderConfigFormValues();
          const url = isCreateMode
            ? '/api/encoders/create'
            : `/api/encoders/${encodeURIComponent(String(streamId))}/configure`;
          await jsonFetch(url, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(payload)
          });
          cleanup({ saved: true, isCreate: isCreateMode, streamId: streamId || null });
        }catch(_err){
          okBtn.disabled = false;
          cancelBtn.disabled = false;
        }
      }
      function onKeyDown(event){
        if (event.key === 'Escape') { event.preventDefault(); cleanup(false); }
        if (event.key === 'Enter' && !event.shiftKey) {
          const tagName = String((event.target && event.target.tagName) || '').toLowerCase();
          if (tagName !== 'textarea') {
            event.preventDefault();
            onOk();
          }
        }
      }

      const resizeHandle = modal ? modal.querySelector('.panel-resize-handle') : null;
      function onResizePointerDown(event){
        if (!modal || event.button !== 0) return;
        event.preventDefault();
        event.stopPropagation();
        const rect = modal.getBoundingClientRect();
        resizeState = {
          startClientX: event.clientX,
          startClientY: event.clientY,
          startWidth: modal.offsetWidth || rect.width,
          startHeight: modal.offsetHeight || rect.height,
          left: Number.parseFloat(modal.style.left) || rect.left,
          top: Number.parseFloat(modal.style.top) || rect.top,
          moved: false
        };
        modal.classList.add('is-resizing');
        document.addEventListener('pointermove', onPointerMove);
        document.addEventListener('pointerup', endPointerInteraction);
        document.addEventListener('pointercancel', endPointerInteraction);
      }
      function onViewportResize(){
        if (!backdrop.classList.contains('active') || !modal) return;
        const rect = modal.getBoundingClientRect();
        applyModalRect(
          Number.parseFloat(modal.style.left) || rect.left,
          Number.parseFloat(modal.style.top) || rect.top,
          modal.offsetWidth || rect.width,
          modal.offsetHeight || rect.height
        );
      }

      backdrop.addEventListener('click', onBackdropClick, true);
      document.addEventListener('keydown', onKeyDown, true);
      if (resizeHandle) resizeHandle.addEventListener('pointerdown', onResizePointerDown, true);
      window.addEventListener('resize', onViewportResize);
      cancelBtn.addEventListener('click', onCancel, true);
      okBtn.addEventListener('click', onOk, true);
      if (titlebar) titlebar.addEventListener('pointerdown', onTitlePointerDown, true);
      if (closeBtn) closeBtn.addEventListener('click', onCancel, true);
      window.setTimeout(() => {
        try{ if (els.encoderConfigHost) els.encoderConfigHost.focus(); }catch(_err){}
      }, 30);
    });
  }

  async function handleEncoderConfigAction(){
    const streamId = selectedEncoderId;
    if (!streamId) return;
    const result = await openEncoderConfigModal({ streamId });
    if (!(result && result.saved)) return;
    await loadEncoders();
  }

  async function handleEncoderAddAction(){
    const result = await openEncoderConfigModal({ isCreate: true });
    if (!(result && result.saved)) return;
    await loadEncoders();
  }

  async function confirmEncoderDeleteModal({title, body, yesText, noText}){
    const win = els.encodersDeleteWindow;
    const titleEl = els.encodersDeleteTitle;
    const bodyEl = els.encodersDeleteBody;
    const yesBtn = els.encodersDeleteYes;
    const noBtn = els.encodersDeleteNo;
    const closeBtn = els.encodersDeleteClose;
    if (!win || !titleEl || !bodyEl || !yesBtn || !noBtn) return false;

    const previous = {
      title: titleEl.textContent,
      body: bodyEl.textContent,
      yes: yesBtn.textContent,
      no: noBtn.textContent
    };

    titleEl.textContent = title || previous.title;
    bodyEl.textContent = body || previous.body;
    yesBtn.textContent = yesText || previous.yes;
    noBtn.textContent = noText || previous.no;

    openFloatingWindow(win);

    return await new Promise(resolve => {
      let done = false;

      const cleanup = () => {
        yesBtn.removeEventListener('click', onYes);
        noBtn.removeEventListener('click', onNo);
        if (closeBtn) closeBtn.removeEventListener('click', onNo);
        document.removeEventListener('keydown', onKeyDown, true);
        titleEl.textContent = previous.title;
        bodyEl.textContent = previous.body;
        yesBtn.textContent = previous.yes;
        noBtn.textContent = previous.no;
        closeFloatingWindow(win);
      };

      const finish = result => {
        if (done) return;
        done = true;
        cleanup();
        resolve(result);
      };

      function onYes(event){
        if (event) {
          event.preventDefault();
          event.stopPropagation();
        }
        finish(true);
      }

      function onNo(event){
        if (event) {
          event.preventDefault();
          event.stopPropagation();
        }
        finish(false);
      }

      function onKeyDown(event){
        if (event.key !== 'Escape') return;
        if (win.style.display !== 'flex' && win.style.display !== 'block') return;
        event.preventDefault();
        event.stopPropagation();
        finish(false);
      }

      yesBtn.addEventListener('click', onYes);
      noBtn.addEventListener('click', onNo);
      if (closeBtn) closeBtn.addEventListener('click', onNo);
      document.addEventListener('keydown', onKeyDown, true);
      window.requestAnimationFrame(() => {
        try { noBtn.focus({preventScroll: true}); } catch (_err) { noBtn.focus(); }
      });
    });
  }

  async function handleEncoderRemoveAction(){
    const streamId = selectedEncoderId;
    if (!streamId) return;
    const confirmed = await confirmEncoderDeleteModal({
      title: 'Delete Encoder',
      body: 'Are you sure you want to delete the selected encoder?',
      yesText: 'Delete',
      noText: 'Cancel'
    });
    if (!confirmed) return;
    if (els.encodersRemoveBtn) els.encodersRemoveBtn.disabled = true;
    if (els.encodersStartBtn) els.encodersStartBtn.disabled = true;
    if (els.encodersStopBtn) els.encodersStopBtn.disabled = true;
    if (els.encodersConfigBtn) els.encodersConfigBtn.disabled = true;
    try{
      await jsonFetch(`/api/encoders/${encodeURIComponent(String(streamId))}`, {
        method: 'DELETE'
      });
      selectedEncoderId = null;
    }catch(_err){}
    await loadEncoders();
  }

  async function loadEncoders(){
    try{
      const data = await jsonFetch('/api/encoders');
      if (Number.isFinite(Number(data.server_now_epoch))) {
        studioEncoderServerOffsetMs = (Number(data.server_now_epoch) * 1000) - Date.now();
      }
      studioEncoderStreamsData = Array.isArray(data.streams) ? data.streams : [];
      renderEncoders(studioEncoderStreamsData);
    }catch(err){
      selectedEncoderId = null;
      studioEncoderStreamsData = [];
      renderList(els.encodersList, [], 'Encoders unavailable', 'queue-table__empty');
      updateEncoderToolbarState();
    }
  }

  if (els.encodersAddBtn) els.encodersAddBtn.addEventListener('click', () => { handleEncoderAddAction(); });
  if (els.encodersRemoveBtn) els.encodersRemoveBtn.addEventListener('click', () => { handleEncoderRemoveAction(); });
  if (els.encodersStartBtn) els.encodersStartBtn.addEventListener('click', () => { handleEncoderToolbarAction('start'); });
  if (els.encodersStopBtn) els.encodersStopBtn.addEventListener('click', () => { handleEncoderToolbarAction('stop'); });
  if (els.encodersConfigBtn) els.encodersConfigBtn.addEventListener('click', () => { handleEncoderConfigAction(); });
  if (els.scriptsStartBtn) {
    els.scriptsStartBtn.addEventListener('click', async () => {
      const stationOnAir = await isStudioOnAir();
      if (!stationOnAir) {
        await openScriptStartBlockedModal();
        return;
      }

      if (selectedStudioEntryType === 'scheduler') {
        const ruleId = String(selectedStudioEntryKey || '').trim();
        if (!ruleId) return;
        try {
          await jsonFetch(`/api/scheduler/rules/${encodeURIComponent(ruleId)}/toggle`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({is_enabled: 1})
          });
          const idx = studioSchedulerRulesData.findIndex(rule => String(rule.id || '').trim() === ruleId);
          if (idx >= 0) {
            studioSchedulerRulesData[idx].is_enabled = 1;
          }
          await loadScheduler();
        } catch (err) {
          console.error('Failed to start scheduler rule', err);
        }
        return;
      }

      const scriptId = Number(selectedStudioScriptId || 0);
      if (!scriptId) return;
      try {
        const data = await jsonFetch(`/api/studio/scripts/${encodeURIComponent(String(scriptId))}/start`, {
          method: 'POST',
          headers: {'Content-Type': 'application/json'}
        });
        console.log('[ScriptEngine] start response', data);
        const idx = studioScriptsData.findIndex(item => Number(item.id || 0) === scriptId);
        if (idx >= 0) studioScriptsData[idx].status = String(data.status || 'Running');
        renderStudioScriptsList();
        setTimeout(() => { loadScheduler(); }, 100);
      } catch (err) {
        if (err && err.code === 'station_off_air') {
          await openScriptStartBlockedModal();
          return;
        }
        console.error('Failed to start script', err);
      }
    });
  }

  if (els.scriptsStopBtn) {
    els.scriptsStopBtn.addEventListener('click', async () => {
      if (selectedStudioEntryType === 'scheduler') {
        const ruleId = String(selectedStudioEntryKey || '').trim();
        if (!ruleId) return;
        try {
          await jsonFetch(`/api/scheduler/rules/${encodeURIComponent(ruleId)}/toggle`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({is_enabled: 0})
          });
          const idx = studioSchedulerRulesData.findIndex(rule => String(rule.id || '').trim() === ruleId);
          if (idx >= 0) {
            studioSchedulerRulesData[idx].is_enabled = 0;
            studioSchedulerRulesData[idx].next_run_at = '';
          }
          await loadScheduler();
        } catch (err) {
          console.error('Failed to stop scheduler rule', err);
        }
        return;
      }

      const scriptId = Number(selectedStudioScriptId || 0);
      if (!scriptId) return;
      try {
        const data = await jsonFetch(`/api/studio/scripts/${encodeURIComponent(String(scriptId))}/stop`, {
          method: 'POST',
          headers: {'Content-Type': 'application/json'}
        });
        console.log('[ScriptEngine] stop response', data);
        const idx = studioScriptsData.findIndex(item => Number(item.id || 0) === scriptId);
        if (idx >= 0) studioScriptsData[idx].status = String(data.status || 'Stopped');
        renderStudioScriptsList();
        setTimeout(() => { loadScheduler(); }, 100);
      } catch (err) {
        console.error('Failed to stop script', err);
      }
    });
  }

  if (els.scriptsRemoveBtn) {
    els.scriptsRemoveBtn.addEventListener('click', async () => {
      if (selectedStudioEntryType === 'scheduler') {
        const ruleId = String(selectedStudioEntryKey || '').trim();
        if (!ruleId) {
          console.warn('No scheduler row selected for delete');
          return;
        }
        const ruleItem = studioSchedulerRulesData.find(rule => String(rule.id || '').trim() === ruleId) || null;
        const confirmed = await confirmDeleteSelectedScript(ruleItem ? { ...ruleItem, entry_type: 'scheduler' } : { entry_type: 'scheduler' });
        if (!confirmed) return;
        try {
          await jsonFetch(`/api/scheduler/rules/${encodeURIComponent(ruleId)}`, {
            method: 'DELETE',
            headers: {'Content-Type': 'application/json'}
          });
          studioSchedulerRulesData = studioSchedulerRulesData.filter(rule => String(rule.id || '').trim() !== ruleId);
          if (String(selectedStudioEntryKey || '').trim() === ruleId) {
            if (studioScriptsData.length) {
              selectedStudioEntryType = 'script';
              selectedStudioScriptId = Number(studioScriptsData[0].id || 0) || null;
              selectedStudioEntryKey = selectedStudioScriptId != null ? String(selectedStudioScriptId) : null;
            } else if (studioSchedulerRulesData.length) {
              selectedStudioEntryType = 'scheduler';
              selectedStudioEntryKey = String(studioSchedulerRulesData[0].id || '').trim() || null;
              selectedStudioScriptId = null;
            } else {
              selectedStudioEntryType = 'script';
              selectedStudioScriptId = null;
              selectedStudioEntryKey = null;
            }
          }
          renderStudioScriptsList();
          setTimeout(() => { loadScheduler(); }, 100);
        } catch (err) {
          console.error('Failed to delete scheduler rule', err);
        }
        return;
      }

      const scriptId = selectedStudioEntryType === 'script' ? (Number(selectedStudioScriptId || 0)) : 0;
      if (!scriptId) {
        console.warn('No script row selected for delete');
        return;
      }
      const scriptItem = studioScriptsData.find(item => Number(item.id || 0) === scriptId) || null;
      const confirmed = await confirmDeleteSelectedScript(scriptItem);
      if (!confirmed) return;
      try {
        await jsonFetch(`/api/studio/scripts/${encodeURIComponent(String(scriptId))}`, {
          method: 'DELETE',
          headers: {'Content-Type': 'application/json'}
        });
        studioScriptsData = studioScriptsData.filter(item => Number(item.id || 0) !== scriptId);
        if (Number(selectedStudioScriptId || 0) === scriptId) {
          selectedStudioScriptId = studioScriptsData.length ? Number(studioScriptsData[0].id || 0) : null;
          if (selectedStudioScriptId) {
            selectedStudioEntryType = 'script';
            selectedStudioEntryKey = String(selectedStudioScriptId);
          } else if (studioSchedulerRulesData.length) {
            selectedStudioEntryType = 'scheduler';
            selectedStudioEntryKey = String(studioSchedulerRulesData[0].id || '').trim() || null;
          } else {
            selectedStudioEntryKey = null;
          }
        }
        renderStudioScriptsList();
        setTimeout(() => { loadScheduler(); }, 100);
      } catch (err) {
        console.error('Failed to delete script', err);
      }
    });
  }

  if (els.scriptsConfigBtn) {
    els.scriptsConfigBtn.addEventListener('click', async () => {
      if (selectedStudioEntryType === 'scheduler') {
        const ruleId = String(selectedStudioEntryKey || '').trim();
        if (!ruleId) return;
        const ruleItem = studioSchedulerRulesData.find(rule => String(rule.id || '').trim() === ruleId) || null;
        if (!ruleItem) return;
        if (typeof window.openSchedulerRuleEditModal === 'function') {
          window.openSchedulerRuleEditModal(ruleItem);
        }
        return;
      }

      const scriptId = selectedStudioEntryType === 'script' ? (Number(selectedStudioScriptId || 0)) : 0;
      if (!scriptId) return;
      const scriptItem = studioScriptsData.find(item => Number(item.id || 0) === scriptId) || null;
      if (!scriptItem) return;

      const result = await openScriptConfigurationWindow(scriptItem);
      if (!result) return;

      try {
        await jsonFetch(`/api/studio/scripts/${encodeURIComponent(String(scriptId))}/config`, {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({
            auto_start: !!result.auto_start,
            content: String(result.content || ''),
            content_changed: !!result.content_changed
          })
        });
        const idx = studioScriptsData.findIndex(item => Number(item.id || 0) === scriptId);
        if (idx >= 0) studioScriptsData[idx].auto_start = result.auto_start ? 1 : 0;
        renderStudioScriptsList();
        setTimeout(() => { loadScheduler(); }, 100);
      } catch (err) {
        console.error('Failed to update script config', err);
      }
    });
  }

  if (els.palList && !els.palList.__studioScriptsBound) {
    els.palList.__studioScriptsBound = true;
    els.palList.addEventListener('click', event => {
      const row = event.target.closest('[data-entry-type][data-entry-key]');
      if (!row) return;
      event.preventDefault();
      const entryType = String(row.getAttribute('data-entry-type') || 'script');
      const entryKeyRaw = row.getAttribute('data-entry-key') || '';
      selectedStudioEntryType = entryType === 'scheduler' ? 'scheduler' : 'script';
      selectedStudioEntryKey = entryKeyRaw || null;
      if (selectedStudioEntryType === 'script') {
        selectedStudioScriptId = Number(row.getAttribute('data-script-id') || 0) || null;
      }
      renderStudioScriptsList();
    });
  }



  async function openScriptConfigurationWindow(scriptItem){
    const win = els.scriptsConfigWindow;
    const filePathEl = els.scriptsConfigFilePath;
    const autoStartEl = els.scriptsConfigAutoStart;
    const editorEl = els.scriptsConfigEditor;
    const okBtn = els.scriptsConfigOk;
    const cancelBtn = els.scriptsConfigCancel;
    const closeBtn = els.scriptsConfigClose;
    if (!win || !filePathEl || !autoStartEl || !editorEl || !okBtn || !cancelBtn || !closeBtn || !scriptItem) return null;

    filePathEl.textContent = String(scriptItem.script_path || '');
    autoStartEl.checked = !!Number(scriptItem.auto_start || 0);
    editorEl.value = 'Loading script file...';
    editorEl.scrollTop = 0;
    let initialContent = '';

    try {
      const data = await jsonFetch(`/api/studio/scripts/${encodeURIComponent(String(scriptItem.id || 0))}/content`);
      initialContent = String((data && data.content) || '');
      editorEl.value = initialContent;
      editorEl.scrollTop = 0;
    } catch (err) {
      console.error('Failed to load script file content', err);
      editorEl.value = '';
      initialContent = '';
    }

    openFloatingWindow(win);

    return await new Promise(resolve => {
      let done = false;

      const cleanup = () => {
        okBtn.removeEventListener('click', onOk);
        cancelBtn.removeEventListener('click', onCancel);
        closeBtn.removeEventListener('click', onCancel);
        document.removeEventListener('keydown', onKeydown);
        closeFloatingWindow(win);
      };

      const finish = result => {
        if (done) return;
        done = true;
        cleanup();
        resolve(result);
      };

      function onOk(event){
        if (event) {
          event.preventDefault();
          event.stopPropagation();
        }
        const currentContent = String(editorEl.value || '');
        finish({
          auto_start: !!autoStartEl.checked,
          content: currentContent,
          content_changed: currentContent !== initialContent
        });
      }

      function onCancel(event){
        if (event) {
          event.preventDefault();
          event.stopPropagation();
        }
        finish(null);
      }

      function onKeydown(event){
        if (event.key === 'Escape') onCancel(event);
      }

      okBtn.addEventListener('click', onOk);
      cancelBtn.addEventListener('click', onCancel);
      closeBtn.addEventListener('click', onCancel);
      document.addEventListener('keydown', onKeydown);
    });
  }

  async function confirmDeleteSelectedScript(scriptItem){
    const isScheduler = !!(scriptItem && String(scriptItem.entry_type || '').trim().toLowerCase() === 'scheduler');
    const name = isScheduler
      ? String(scriptItem && scriptItem.name ? scriptItem.name : '').trim()
      : getBaseName(scriptItem && scriptItem.script_path ? scriptItem.script_path : '');
    const bodyText = name
      ? `Are you sure you want to delete "${name}"?`
      : (isScheduler
        ? 'Are you sure you want to delete the selected scheduler rule?'
        : 'Are you sure you want to delete the selected script?');

    if (isScheduler && els.schedulerRuleDeleteWindow && els.schedulerRuleDeleteYes && els.schedulerRuleDeleteNo) {
      const win = els.schedulerRuleDeleteWindow;
      const titleEl = els.schedulerRuleDeleteTitle;
      const bodyEl = els.schedulerRuleDeleteBody;
      const yesBtn = els.schedulerRuleDeleteYes;
      const noBtn = els.schedulerRuleDeleteNo;
      const closeBtn = els.schedulerRuleDeleteClose;
      const previousTitle = titleEl ? titleEl.textContent : '';
      const previousBody = bodyEl ? bodyEl.textContent : '';
      const previousYes = yesBtn ? yesBtn.textContent : '';
      const previousNo = noBtn ? noBtn.textContent : '';

      if (titleEl) titleEl.textContent = 'Delete rule';
      if (bodyEl) bodyEl.textContent = bodyText;
      if (yesBtn) yesBtn.textContent = 'Delete';
      if (noBtn) noBtn.textContent = 'Cancel';

      openFloatingWindow(win);

      return await new Promise(resolve => {
        let done = false;

        const cleanup = () => {
          yesBtn.removeEventListener('click', onYes);
          noBtn.removeEventListener('click', onNo);
          if (closeBtn) closeBtn.removeEventListener('click', onNo);
          document.removeEventListener('keydown', onKeydown, true);
          if (titleEl) titleEl.textContent = previousTitle;
          if (bodyEl) bodyEl.textContent = previousBody;
          if (yesBtn) yesBtn.textContent = previousYes;
          if (noBtn) noBtn.textContent = previousNo;
          closeFloatingWindow(win);
        };

        const finish = result => {
          if (done) return;
          done = true;
          cleanup();
          resolve(result);
        };

        function onYes(event){
          if (event) {
            event.preventDefault();
            event.stopPropagation();
          }
          finish(true);
        }

        function onNo(event){
          if (event) {
            event.preventDefault();
            event.stopPropagation();
          }
          finish(false);
        }

        function onKeydown(event){
          if (event.key !== 'Escape') return;
          if (win.style.display !== 'flex' && win.style.display !== 'block') return;
          event.preventDefault();
          event.stopPropagation();
          finish(false);
        }

        yesBtn.addEventListener('click', onYes);
        noBtn.addEventListener('click', onNo);
        if (closeBtn) closeBtn.addEventListener('click', onNo);
        document.addEventListener('keydown', onKeydown, true);
      });
    }

    const backdrop = els.scriptsDeleteBackdrop;
    const bodyEl = els.scriptsDeleteBody;
    const yesBtn = els.scriptsDeleteYes;
    const noBtn = els.scriptsDeleteNo;
    if (!backdrop || !yesBtn || !noBtn) return false;

    if (bodyEl) bodyEl.textContent = bodyText;

    const closeBtn = els.scriptsDeleteClose;
    openFloatingWindow(backdrop);

    return await new Promise(resolve => {
      let done = false;

      const cleanup = () => {
        yesBtn.removeEventListener('click', onYes);
        noBtn.removeEventListener('click', onNo);
        if (closeBtn) closeBtn.removeEventListener('click', onNo);
        document.removeEventListener('keydown', onKeydown, true);
        closeFloatingWindow(backdrop);
      };

      const finish = result => {
        if (done) return;
        done = true;
        cleanup();
        resolve(result);
      };

      function onYes(event){
        if (event) {
          event.preventDefault();
          event.stopPropagation();
        }
        finish(true);
      }

      function onNo(event){
        if (event) {
          event.preventDefault();
          event.stopPropagation();
        }
        finish(false);
      }

      function onKeydown(event){
        if (event.key !== 'Escape') return;
        if (backdrop.style.display !== 'flex' && backdrop.style.display !== 'block') return;
        event.preventDefault();
        event.stopPropagation();
        finish(false);
      }

      yesBtn.addEventListener('click', onYes);
      noBtn.addEventListener('click', onNo);
      if (closeBtn) closeBtn.addEventListener('click', onNo);
      document.addEventListener('keydown', onKeydown, true);
    });
  }


  function parseSchedulerNextRunDate(value){
    const raw = String(value || '').trim();
    if (!raw) return null;
    const isoCandidate = raw.includes('T') ? raw : raw.replace(' ', 'T');
    try {
      const parsed = new Date(isoCandidate);
      if (!Number.isNaN(parsed.getTime())) return parsed;
    } catch (err) {}
    return null;
  }

  function formatSchedulerNextRunTime(value){
    const dt = parseSchedulerNextRunDate(value);
    if (!dt) return '';
    const hour = String(dt.getHours()).padStart(2, '0');
    const minute = String(dt.getMinutes()).padStart(2, '0');
    const second = String(dt.getSeconds()).padStart(2, '0');
    return `${hour}:${minute}:${second}`;
  }


  function formatSchedulerEtaCompact(totalSeconds){
    let total = Number(totalSeconds);
    if (!Number.isFinite(total) || total < 0) total = 0;
    total = Math.floor(total);
    const days = Math.floor(total / 86400);
    total -= days * 86400;
    const hours = Math.floor(total / 3600);
    total -= hours * 3600;
    const minutes = Math.floor(total / 60);
    total -= minutes * 60;
    const seconds = total;
    const parts = [];
    if (days > 0) parts.push(`${days}d`);
    if (hours > 0 || days > 0) parts.push(`${hours}h`);
    if (minutes > 0 || hours > 0 || days > 0) parts.push(`${minutes}m`);
    parts.push(`${seconds}s`);
    return parts.join(' ');
  }

  function getSchedulerRowStatus(rule){
    const enabled = !!Number(rule && rule.is_enabled ? rule.is_enabled : 0);
    if (!enabled) return 'Stopped';
    const nextRunAtRaw = rule && rule.next_run_at ? rule.next_run_at : '';
    const nextRunTime = formatSchedulerNextRunTime(nextRunAtRaw);
    const nextRunDate = parseSchedulerNextRunDate(nextRunAtRaw);
    if (!nextRunTime || !nextRunDate) return 'Stopped';
    const etaSecs = Math.max(0, Math.floor((nextRunDate.getTime() - Date.now()) / 1000));
    return `Waiting for time ${nextRunTime} (ETA: -${formatSchedulerEtaCompact(etaSecs)})`;
  }

  function getScriptRowStatus(item){
    const rawStatus = String(item && item.status ? item.status : 'Stopped').trim() || 'Stopped';
    const upperStatus = rawStatus.toUpperCase();
    if (upperStatus === 'STOPPED' || upperStatus === 'ERROR') return rawStatus;

    const nextRunAtRaw = item && item.next_run_at ? item.next_run_at : '';
    const nextRunTime = item && item.next_run_time
      ? String(item.next_run_time || '').trim()
      : formatSchedulerNextRunTime(nextRunAtRaw);
    const nextRunDate = parseSchedulerNextRunDate(nextRunAtRaw);
    if (nextRunTime && nextRunDate) {
      const etaSecs = Math.max(0, Math.floor((nextRunDate.getTime() - Date.now()) / 1000));
      return `Waiting for time ${nextRunTime} (ETA: -${formatSchedulerEtaCompact(etaSecs)})`;
    }

    return rawStatus;
  }

  function renderStudioScriptsList(){
    if (!els.palList) return;
    const scriptItems = Array.isArray(studioScriptsData) ? studioScriptsData.slice() : [];
    const schedulerItems = Array.isArray(studioSchedulerRulesData) ? studioSchedulerRulesData.slice() : [];
    const rowMarkup = [];

    if (!scriptItems.length && !schedulerItems.length) {
      els.palList.innerHTML = '<div class="queue-table__empty">No scripts or scheduler entries configured</div>';
      return;
    }

    const availableScriptIds = new Set(
      scriptItems
        .map(item => Number(item.id || 0))
        .filter(id => Number.isFinite(id) && id > 0)
    );
    const availableSchedulerIds = new Set(
      schedulerItems
        .map(rule => String(rule.id || '').trim())
        .filter(Boolean)
    );

    const currentEntryKey = String(selectedStudioEntryKey || '').trim();
    const currentScriptId = Number(selectedStudioScriptId || 0);
    const hasSelectedScript = selectedStudioEntryType === 'script' && availableScriptIds.has(currentScriptId);
    const hasSelectedScheduler = selectedStudioEntryType === 'scheduler' && availableSchedulerIds.has(currentEntryKey);
    if (!hasSelectedScript && !hasSelectedScheduler) {
      if (scriptItems.length) {
        selectedStudioEntryType = 'script';
        selectedStudioScriptId = Number(scriptItems[0].id || 0) || null;
        selectedStudioEntryKey = selectedStudioScriptId != null ? String(selectedStudioScriptId) : null;
      } else if (schedulerItems.length) {
        selectedStudioEntryType = 'scheduler';
        selectedStudioEntryKey = String(schedulerItems[0].id || '').trim() || null;
        selectedStudioScriptId = null;
      } else {
        selectedStudioEntryType = 'script';
        selectedStudioEntryKey = null;
        selectedStudioScriptId = null;
      }
    }

    let rowIndex = 0;
    scriptItems.forEach(item => {
      rowIndex += 1;
      const itemId = Number(item.id || 0) || rowIndex;
      const entryKey = String(itemId);
      const isSelected = selectedStudioEntryType === 'script' && String(selectedStudioEntryKey || '') === entryKey;
      rowMarkup.push(`
        <button type="button" class="queue-table__row encoder-table__row script-table__row${isSelected ? ' is-selected' : ''}" data-entry-type="script" data-entry-key="${entryKey}" data-script-id="${itemId}">
          <div class="encoder-table__index script-table__index">${rowIndex}</div>
          <div class="encoder-table__format script-table__autostart">${item.auto_start ? 'Yes' : 'No'}</div>
          <div class="encoder-table__format script-table__type">Script</div>
          <div class="encoder-table__description script-table__description" title="${escapeHtml(item.script_path || '')}">${escapeHtml(getBaseName(item.script_path || ''))}</div>
          <div class="encoder-table__status script-table__status">${escapeHtml(getScriptRowStatus(item))}</div>
        </button>
      `);
    });

    schedulerItems.forEach(rule => {
      rowIndex += 1;
      const ruleId = String(rule.id || '').trim() || String(rowIndex);
      const ruleName = String(rule.name || '').trim() || `Rule ${Number(rule.id || 0) || rowIndex}`;
      const runWhen = String(rule.run_when || '').trim();
      const autoStart = !!Number(rule.auto_start || 0);
      const titleParts = [ruleName];
      if (runWhen) titleParts.push(runWhen);
      const isSelected = selectedStudioEntryType === 'scheduler' && String(selectedStudioEntryKey || '') === ruleId;
      const statusText = getSchedulerRowStatus(rule);
      rowMarkup.push(`
        <button type="button" class="queue-table__row encoder-table__row script-table__row script-table__row--scheduler${isSelected ? ' is-selected' : ''}" data-entry-type="scheduler" data-entry-key="${escapeHtml(ruleId)}" title="${escapeHtml(titleParts.join(' — '))}">
          <div class="encoder-table__index script-table__index">${rowIndex}</div>
          <div class="encoder-table__format script-table__autostart">${autoStart ? 'Yes' : 'No'}</div>
          <div class="encoder-table__format script-table__type">Scheduler</div>
          <div class="encoder-table__description script-table__description">${escapeHtml(ruleName)}</div>
          <div class="encoder-table__status script-table__status">${escapeHtml(statusText)}</div>
        </button>
      `);
    });

    els.palList.innerHTML = rowMarkup.join('');
  }

  async function loadScheduler(){
    try{
      const [scriptsResult, rulesResult] = await Promise.allSettled([
        jsonFetch('/api/studio/scripts'),
        jsonFetch('/api/scheduler/rules')
      ]);

      studioScriptsData = (scriptsResult.status === 'fulfilled' && scriptsResult.value && Array.isArray(scriptsResult.value.scripts))
        ? scriptsResult.value.scripts.slice()
        : [];
      studioSchedulerRulesData = (rulesResult.status === 'fulfilled' && rulesResult.value && Array.isArray(rulesResult.value.rules))
        ? rulesResult.value.rules.slice()
        : [];
      renderStudioScriptsList();
    }catch(err){
      if (els.palList) els.palList.innerHTML = '<div class="queue-table__empty">Scripts unavailable</div>';
    }
  }

  async function sendControl(action){
    const isManualNext = action === 'next' || action === 'skip';
    if (isManualNext && studioManualNextPending) return;
    if (isManualNext) setStudioManualNextPending(true, 1);
    try{
      const result = await jsonFetch('/api/control', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({action})
      });
      if (isManualNext) {
        if (!result || result.success === false || result.accepted === false) {
          setStudioManualNextPending(false, 0);
          throw new Error((result && result.error) || 'Manual Next was not accepted.');
        }
        setStudioManualNextPending(true, Number(result.pending_count || 1));
      }
      if (action === 'play') {
        studioStopUiOverride = false;
      }
      if (action === 'stop') {
        studioStopUiOverride = true;
        studioPauseUiOverride = false;
        studioPauseFrozenElapsed = 0;
        setStudioProgressAnchor(0, '');
        studioResumeHoldUntil = 0;
        studioIsPaused = false;
        studioIsPlaying = false;
        if (els.deckProgressFill) els.deckProgressFill.style.width = '0%';
        if (els.deckTime) {
          els.deckTime.textContent = `0:00 / ${studioCurrentDurationDisplay || formatSeconds(studioCurrentDuration)}`;
          els.deckTime.classList.remove('is-paused');
        }
        if (els.statusPill) {
          els.statusPill.textContent = 'STOPPED';
          els.statusPill.classList.remove('is-live');
          els.statusPill.classList.remove('is-paused');
        }
      }
      if (action === 'pause') {
        const pauseActive = Boolean(result && (result.paused || result.pause_active));
        studioPauseUiOverride = pauseActive;
        studioPauseUiOverrideIssuedAt = Date.now();
        if (pauseActive) {
          const liveElapsed = readStudioProgressElapsed(studioCurrentDuration);
          studioPauseFrozenElapsed = Number.isFinite(liveElapsed) ? liveElapsed : 0;
          studioPauseFrozenDuration = Number.isFinite(studioCurrentDuration) ? studioCurrentDuration : 0;
          studioPauseFrozenDurationDisplay = studioCurrentDurationDisplay || '';
          setStudioProgressAnchor(studioPauseFrozenElapsed);
        } else {
          const resumedElapsed = Number(result && result.elapsed);
          const resumedDuration = Number(result && result.duration);
          if (Number.isFinite(resumedElapsed) && resumedElapsed >= 0) {
            studioCurrentElapsed = resumedElapsed;
          } else if (Number.isFinite(studioPauseFrozenElapsed) && studioPauseFrozenElapsed >= 0) {
            studioCurrentElapsed = studioPauseFrozenElapsed;
          }
          if (Number.isFinite(resumedDuration) && resumedDuration > 0) {
            studioCurrentDuration = resumedDuration;
            studioCurrentDurationDisplay = (result.duration_display || '').toString() || formatSeconds(resumedDuration);
          } else if (Number.isFinite(studioPauseFrozenDuration) && studioPauseFrozenDuration > 0) {
            studioCurrentDuration = studioPauseFrozenDuration;
            studioCurrentDurationDisplay = studioPauseFrozenDurationDisplay || formatSeconds(studioPauseFrozenDuration);
          }
          setStudioProgressAnchor(studioCurrentElapsed);
          studioResumeHoldUntil = Date.now() + 1500;
        }
        studioIsPaused = pauseActive;
        studioIsPlaying = !pauseActive;
        if (els.deckTime) {
          els.deckTime.textContent = `${formatSeconds(studioCurrentElapsed)} / ${studioCurrentDurationDisplay || formatSeconds(studioCurrentDuration)}`;
          els.deckTime.classList.toggle('is-paused', pauseActive);
        }
        if (els.statusPill) {
          els.statusPill.textContent = pauseActive ? 'PAUSE' : 'PLAY';
          els.statusPill.classList.toggle('is-live', !pauseActive);
          els.statusPill.classList.toggle('is-paused', pauseActive);
        }
      }
      setTimeout(loadStatus, 120);
      setTimeout(loadStatus, 650);
    }catch(err){
      if (isManualNext) setStudioManualNextPending(false, 0);
      console.error(err);
    }
  }



  function formatBrowserDuration(totalSeconds){
    const sec = Math.max(0, Math.floor(Number(totalSeconds) || 0));
    if (!sec) return '';
    const minutes = Math.floor(sec / 60);
    const seconds = sec % 60;
    return `${minutes}:${String(seconds).padStart(2, '0')}`;
  }

  function getBaseName(filePath){
    const value = String(filePath || '').trim();
    if (!value) return '';
    const parts = value.split(/[/\\]+/);
    return parts[parts.length - 1] || value;
  }

  function getPlaylistBrowserEntryKey(entry){
    return `${entry.type}:${entry.relative_path || entry.filename || entry.name || ''}`;
  }

  function getPlaylistBrowserSelectionType(){
    return playlistBrowserState && playlistBrowserState.selectionType === 'directories' ? 'directories' : 'files';
  }

  function getPlaylistBrowserSelectedEntries(){
    if (!playlistBrowserState) return [];
    const selectionType = getPlaylistBrowserSelectionType();
    return (playlistBrowserState.entries || []).filter(entry => {
      if (!playlistBrowserState.selectedKeys.has(getPlaylistBrowserEntryKey(entry))) return false;
      if (selectionType === 'directories') return entry.type === 'dir';
      return entry.type === 'file';
    });
  }

  function updatePlaylistBrowserSummary(){
    if (!els.playlistBrowserSummary) return;
    const selectedEntries = getPlaylistBrowserSelectedEntries();
    const selectedCount = selectedEntries.length;
    const label = getPlaylistBrowserSelectionType() === 'directories'
      ? (selectedCount === 1 ? 'folder' : 'folders')
      : (selectedCount === 1 ? 'file' : 'files');
    els.playlistBrowserSummary.textContent = `${selectedCount} ${label} selected`;
    if (els.playlistAddFilesOk) els.playlistAddFilesOk.disabled = selectedCount === 0;
  }

  function renderPlaylistBrowserBreadcrumb(currentSub){
    if (!els.playlistBrowserBreadcrumb) return;
    const normalized = String(currentSub || '').replace(/\\/g, '/').replace(/^\/+|\/+$/g, '');
    els.playlistBrowserBreadcrumb.textContent = normalized ? normalized.split('/').join(' / ') : '/';
  }

  function renderPlaylistBrowserRows(){
    if (!els.playlistBrowserRows || !playlistBrowserState) return;
    const rows = playlistBrowserState.entries || [];
    if (!rows.length) {
      els.playlistBrowserRows.innerHTML = '<div class="browser-table__empty">No files found</div>';
      updatePlaylistBrowserSummary();
      return;
    }
    els.playlistBrowserRows.innerHTML = rows.map((entry, index) => {
      const key = getPlaylistBrowserEntryKey(entry);
      const isSelected = playlistBrowserState.selectedKeys.has(key);
      const icon = entry.type === 'dir' ? '📁' : '♪';
      const name = entry.type === 'parent' ? '..' : (entry.filename || entry.name || entry.relative_path || 'Untitled');
      const duration = entry.type === 'file' ? formatBrowserDuration(entry.cue_duration_seconds) : '';
      return `
        <button type="button" class="browser-table__row${isSelected ? ' is-selected' : ''}" data-browser-key="${escapeHtml(key)}" data-browser-index="${index}" data-browser-type="${escapeHtml(entry.type)}">
          <div class="browser-table__name"><span class="browser-table__icon">${icon}</span><span>${escapeHtml(name)}</span></div>
          <div class="browser-table__duration">${escapeHtml(duration)}</div>
        </button>
      `;
    }).join('');
    updatePlaylistBrowserSummary();
  }

  function handlePlaylistBrowserRowClick(event, row){
    if (!playlistBrowserState || !row) return;
    const index = Number(row.dataset.browserIndex || -1);
    const entry = playlistBrowserState.entries[index];
    if (!entry) return;
    const selectionType = getPlaylistBrowserSelectionType();
    const selectableType = selectionType === 'directories' ? 'dir' : 'file';
    if (entry.type === 'parent') {
      loadPlaylistBrowserDirectory(playlistBrowserState.parentSub || '');
      return;
    }
    if (entry.type !== selectableType) {
      return;
    }

    const key = getPlaylistBrowserEntryKey(entry);
    const isShift = event.shiftKey;
    const isCtrl = event.ctrlKey || event.metaKey;
    if (isShift && playlistBrowserState.lastSelectedIndex != null) {
      const [from, to] = playlistBrowserState.lastSelectedIndex < index
        ? [playlistBrowserState.lastSelectedIndex, index]
        : [index, playlistBrowserState.lastSelectedIndex];
      if (!isCtrl) playlistBrowserState.selectedKeys.clear();
      for (let i = from; i <= to; i += 1) {
        const current = playlistBrowserState.entries[i];
        if (current && current.type === selectableType) playlistBrowserState.selectedKeys.add(getPlaylistBrowserEntryKey(current));
      }
    } else if (isCtrl) {
      if (playlistBrowserState.selectedKeys.has(key)) playlistBrowserState.selectedKeys.delete(key);
      else playlistBrowserState.selectedKeys.add(key);
      playlistBrowserState.lastSelectedIndex = index;
    } else {
      const singleSelected = playlistBrowserState.selectedKeys.size === 1 && playlistBrowserState.selectedKeys.has(key);
      if (selectionType === 'directories' && entry.type === 'dir' && singleSelected) {
        loadPlaylistBrowserDirectory(entry.relative_path || '');
        return;
      }
      playlistBrowserState.selectedKeys.clear();
      if (!singleSelected) playlistBrowserState.selectedKeys.add(key);
      playlistBrowserState.lastSelectedIndex = index;
    }
    renderPlaylistBrowserRows();
  }

  async function loadPlaylistBrowserDirectory(subPath){
    if (!playlistBrowserState) return;
    const normalizedSub = String(subPath || '');
    const data = await jsonFetch(`/api/library/files?sub=${encodeURIComponent(normalizedSub)}`);
    playlistBrowserState.currentSub = data.current_sub || '';
    playlistBrowserState.parentSub = data.parent_sub || '';
    playlistBrowserState.selectedKeys.clear();
    playlistBrowserState.lastSelectedIndex = null;
    const parentEntry = (playlistBrowserState.currentSub || '')
      ? [{type: 'parent', name: '..', relative_path: playlistBrowserState.parentSub || ''}]
      : [];
    playlistBrowserState.entries = parentEntry.concat((data.dirs || []).map(item => ({...item, type: 'dir'})), (data.files || []).map(item => ({...item, type: 'file'})));
    renderPlaylistBrowserBreadcrumb(playlistBrowserState.currentSub || '');
    renderPlaylistBrowserRows();
  }

  async function openPlaylistBrowserWindow(categoryId, mode, target = "playlist", options = {}){
    const win = els.playlistAddFilesWindow;
    const okBtn = els.playlistAddFilesOk;
    const cancelBtn = els.playlistAddFilesCancel;
    const closeBtn = els.playlistAddFilesClose;
    const rowsEl = els.playlistBrowserRows;
    const titleEl = els.playlistAddFilesTitle;
    if (!win || !okBtn || !cancelBtn || !closeBtn || !rowsEl) return;

    const windowTarget = target === 'queue' ? 'queue' : (target === 'scheduler' ? 'scheduler' : 'playlist');
    const previousInlineZIndex = win.style.zIndex || '';
    const previousDatasetTopmost = win.dataset.schedulerTopmost || '';
    if (windowTarget === 'scheduler') {
      win.style.zIndex = '2147483647';
      win.dataset.schedulerTopmost = '1';
    } else {
      delete win.dataset.schedulerTopmost;
      if (!previousInlineZIndex) win.style.removeProperty('z-index');
    }
    const onConfirm = options && typeof options.onConfirm === 'function' ? options.onConfirm : null;
    if (windowTarget === 'playlist' && !categoryId) return;

    const selectionType = mode === PLAYLIST_BROWSER_MODES.DIRECTORIES ? 'directories' : 'files';
    if (titleEl) {
      if (windowTarget === 'queue') titleEl.textContent = selectionType === 'directories' ? 'Add Directory to Queue' : 'Add Files to Queue';
      else if (windowTarget === 'scheduler') titleEl.textContent = selectionType === 'directories' ? 'Add Directory' : 'Add Files';
      else titleEl.textContent = selectionType === 'directories' ? 'Add Directory to Category' : 'Add Files to Category';
    }
    okBtn.textContent = selectionType === 'directories' ? 'Add Directory' : 'Add Files';

    playlistBrowserState = {
      categoryId: categoryId ? String(categoryId) : '',
      target: windowTarget,
      currentSub: '',
      parentSub: '',
      entries: [],
      selectedKeys: new Set(),
      lastSelectedIndex: null,
      selectionType
    };

    openFloatingWindow(win);
    const existingDirLoading = document.getElementById('dir-loading-indicator');
    if (existingDirLoading) existingDirLoading.remove();
    okBtn.disabled = true;
    renderPlaylistBrowserBreadcrumb('');
    rowsEl.innerHTML = '<div class="browser-table__empty">Loading files…</div>';
    updatePlaylistBrowserSummary();

    try {
      await loadPlaylistBrowserDirectory('');
    } catch (err) {
      console.error('Failed to load file browser', err);
      rowsEl.innerHTML = '<div class="browser-table__empty">Files unavailable</div>';
      okBtn.disabled = true;
    }

    const completed = await new Promise(resolve => {
      async function onOk(){
        const selectedEntries = getPlaylistBrowserSelectedEntries();
        if (!selectedEntries.length) return;

        const loadingIndicatorId = selectionType === 'directories' ? 'dir-loading-indicator' : 'files-loading-indicator';
        let loading = document.getElementById(loadingIndicatorId);
        if (!loading) {
          loading = document.createElement('div');
          loading.id = loadingIndicatorId;
          loading.className = 'add-dir-loading';
          loading.innerHTML = '<span class="spinner"></span> Adding files... please wait';
          const host = rowsEl.parentNode || rowsEl;
          host.appendChild(loading);
        }
        loading.style.display = 'block';
        okBtn.disabled = true;
        cancelBtn.disabled = true;
        closeBtn.disabled = true;

        try {
          const trackIds = selectionType === 'files'
            ? selectedEntries.map(entry => Number(entry.id)).filter(id => Number.isFinite(id))
            : [];
          const filePaths = selectionType === 'files'
            ? selectedEntries.map(entry => String(entry.relative_path || '')).filter(Boolean)
            : [];
          const directoryPaths = selectionType === 'directories'
            ? selectedEntries.map(entry => String(entry.relative_path || '')).filter(Boolean)
            : [];
          if (windowTarget === 'queue') {
            await jsonFetch('/api/queue/add', {
              method: 'POST',
              headers: {'Content-Type': 'application/json'},
              body: JSON.stringify({track_ids: trackIds, file_paths: filePaths, directory_paths: directoryPaths})
            });
          } else if (windowTarget === 'scheduler') {
            if (onConfirm) {
              await onConfirm({
                selectionType,
                selectedEntries: selectedEntries.map(entry => ({...entry})),
                trackIds,
                filePaths,
                directoryPaths
              });
            }
          } else {
            await jsonFetch(`/api/library/category/${encodeURIComponent(String(categoryId))}/assign`, {
              method: 'POST',
              headers: {'Content-Type': 'application/json'},
              body: JSON.stringify({track_ids: trackIds, file_paths: filePaths, directory_paths: directoryPaths})
            });
          }
          finish(true);
        } catch (err) {
          console.error('Failed to add browser selection to playlist category', err);
        } finally {
          const existingDirLoading = document.getElementById('dir-loading-indicator');
          if (existingDirLoading) existingDirLoading.remove();
          const existingFilesLoading = document.getElementById('files-loading-indicator');
          if (existingFilesLoading) existingFilesLoading.remove();
          okBtn.disabled = false;
          cancelBtn.disabled = false;
          closeBtn.disabled = false;
        }
      }
      function onCancel(){
        const existingDirLoading = document.getElementById('dir-loading-indicator');
        if (existingDirLoading) existingDirLoading.remove();
        const existingFilesLoading = document.getElementById('files-loading-indicator');
        if (existingFilesLoading) existingFilesLoading.remove();
        okBtn.disabled = false;
        cancelBtn.disabled = false;
        closeBtn.disabled = false;
        finish(false);
      }
      function onKeyDown(event){
        if (event.key === 'Escape') finish(false);
        if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'a') {
          if (!playlistBrowserState) return;
          event.preventDefault();
          const selectableType = getPlaylistBrowserSelectionType() === 'directories' ? 'dir' : 'file';
          playlistBrowserState.selectedKeys.clear();
          (playlistBrowserState.entries || []).forEach(entry => {
            if (entry.type === selectableType) playlistBrowserState.selectedKeys.add(getPlaylistBrowserEntryKey(entry));
          });
          renderPlaylistBrowserRows();
        }
      }
      function onRowsClick(event){
        const row = event.target.closest('[data-browser-index]');
        if (!row) return;
        event.preventDefault();
        handlePlaylistBrowserRowClick(event, row);
      }
      function onRowsDoubleClick(event){
        const row = event.target.closest('[data-browser-index]');
        if (!row || !playlistBrowserState) return;
        const entry = playlistBrowserState.entries[Number(row.dataset.browserIndex || -1)];
        if (!entry) return;
        if (entry.type === 'dir') {
          event.preventDefault();
          loadPlaylistBrowserDirectory(entry.relative_path || '');
        }
      }
      function cleanup(){
        const existingDirLoading = document.getElementById('dir-loading-indicator');
        if (existingDirLoading) existingDirLoading.remove();
        const existingFilesLoading = document.getElementById('files-loading-indicator');
        if (existingFilesLoading) existingFilesLoading.remove();
        okBtn.disabled = false;
        cancelBtn.disabled = false;
        closeBtn.disabled = false;
        if (windowTarget === 'scheduler') {
          if (previousInlineZIndex) win.style.zIndex = previousInlineZIndex;
          else win.style.removeProperty('z-index');
          if (previousDatasetTopmost) win.dataset.schedulerTopmost = previousDatasetTopmost;
          else delete win.dataset.schedulerTopmost;
        }
        okBtn.removeEventListener('click', onOk);
        cancelBtn.removeEventListener('click', onCancel);
        closeBtn.removeEventListener('click', onCancel);
        document.removeEventListener('keydown', onKeyDown, true);
        rowsEl.removeEventListener('click', onRowsClick);
        rowsEl.removeEventListener('dblclick', onRowsDoubleClick);
      }
      function finish(value){
        cleanup();
        closeFloatingWindow(win);
        playlistBrowserState = null;
        resolve(value);
      }
      okBtn.addEventListener('click', onOk);
      cancelBtn.addEventListener('click', onCancel);
      closeBtn.addEventListener('click', onCancel);
      document.addEventListener('keydown', onKeyDown, true);
      rowsEl.addEventListener('click', onRowsClick);
      rowsEl.addEventListener('dblclick', onRowsDoubleClick);
      window.requestAnimationFrame(() => {
        if (typeof rowsEl.focus === 'function') {
          try {
            rowsEl.focus({preventScroll: true});
          } catch (_err) {
            rowsEl.focus();
          }
        }
      });
    });

    
    const loading = document.getElementById('dir-loading-indicator');
    if (loading) loading.remove();
    okBtn.disabled = false;
if (completed) {
      if (windowTarget === 'queue') {
        await loadQueue();
      } else if (windowTarget === 'playlist') {
        selectedCategoryId = String(categoryId);
        renderPlaylistTreeFromDomSelection();
        await loadTracks();
      }
    }
  }


  window.openPlaylistBrowserWindow = openPlaylistBrowserWindow;



  async function openPlaylistAddUrlWindow(categoryId, target = 'playlist'){
    const windowTarget = target === 'queue' ? 'queue' : (target === 'scheduler' ? 'scheduler' : 'playlist');
    if (windowTarget === 'queue' && typeof window.openSchedulerUrlModal === 'function') {
      const result = await window.openSchedulerUrlModal({
        title: 'Add URL to Queue',
        defaultDuration: 60,
        defaultInfinite: false
      });
      if (!result || !result.url) return;
      await jsonFetch('/api/queue/add-url', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({url: result.url, duration: result.duration})
      });
      await loadQueue();
      return;
    }

    const win = els.playlistAddUrlWindow;
    const input = els.playlistAddUrlInput;
    const okBtn = els.playlistAddUrlOk;
    const cancelBtn = els.playlistAddUrlCancel;
    const closeBtn = els.playlistAddUrlClose;
    if (!win || !input || !okBtn || !cancelBtn || !closeBtn) return;

    if (windowTarget === 'playlist' && !categoryId) return;
    const titleEl = els.playlistAddUrlTitle || document.getElementById('playlist-add-url-title');
    if (titleEl) titleEl.textContent = windowTarget === 'queue' ? 'Add URL to Queue' : 'Add URL to Category';
    okBtn.textContent = windowTarget === 'queue' ? 'Add URL' : 'Add URL';

    openFloatingWindow(win);
    const viewportWidth = window.innerWidth || document.documentElement.clientWidth || 1280;
    const viewportHeight = window.innerHeight || document.documentElement.clientHeight || 720;
    applyFloatingWindowRect(
      win,
      Math.round((viewportWidth - 560) / 2),
      Math.round((viewportHeight - 184) / 2),
      560,
      184
    );
    input.value = '';

    const completed = await new Promise(resolve => {
      async function onOk(){
        const url = String(input.value || '').trim();
        if (!/^https?:\/\//i.test(url)) {
          input.focus();
          input.select();
          return;
        }
        okBtn.disabled = true;
        try {
          if (windowTarget === 'queue') {
            await jsonFetch('/api/queue/add-url', {
              method: 'POST',
              headers: {'Content-Type': 'application/json'},
              body: JSON.stringify({url})
            });
          } else {
            if (!categoryId) {
              okBtn.disabled = false;
              input.focus();
              input.select();
              return;
            }
            await jsonFetch(`/api/library/category/${encodeURIComponent(categoryId)}/add-url`, {
              method: 'POST',
              headers: {'Content-Type': 'application/json'},
              body: JSON.stringify({url})
            });
          }
          finish(true);
        } catch (err) {
          console.error('Failed to add URL to category', err);
          okBtn.disabled = false;
          input.focus();
          input.select();
        }
      }

      function finishAndRefresh(value){
        finish(value);
      }
      function onCancel(){ finish(false); }
      function onKeyDown(event){
        if (event.key === 'Escape') finish(false);
        if (event.key === 'Enter') {
          event.preventDefault();
          onOk();
        }
      }
      function cleanup(){
        okBtn.removeEventListener('click', onOk);
        cancelBtn.removeEventListener('click', onCancel);
        closeBtn.removeEventListener('click', onCancel);
        document.removeEventListener('keydown', onKeyDown, true);
      }
      function finish(value){
        cleanup();
        okBtn.disabled = false;
        closeFloatingWindow(win);
        resolve(value);
      }
      okBtn.addEventListener('click', onOk);
      cancelBtn.addEventListener('click', onCancel);
      closeBtn.addEventListener('click', onCancel);
      document.addEventListener('keydown', onKeyDown, true);
      window.requestAnimationFrame(() => {
        try { input.focus({preventScroll: true}); } catch (_err) { input.focus(); }
      });
    });

    if (completed) {
      if (windowTarget === 'queue') {
        await loadQueue();
      } else if (windowTarget === 'playlist') {
        selectedCategoryId = String(categoryId);
        renderPlaylistTreeFromDomSelection();
        await loadTracks();
      }
    }
  }

  function clampFloatingWindow(win, left, top, width, height){
    const viewportWidth = window.innerWidth || document.documentElement.clientWidth || 1280;
    const viewportHeight = window.innerHeight || document.documentElement.clientHeight || 720;
    const edgePadding = 12;
    const topPadding = 8;
    const dialogMinWidth = win && win.classList.contains('studio-floating-window--dialog') ? 420 : 560;
    const dialogMinHeight = win && win.classList.contains('studio-floating-window--dialog') ? 180 : 360;
    const explicitMinWidth = Number.parseInt((win && win.dataset && win.dataset.minWidth) || '', 10);
    const explicitMinHeight = Number.parseInt((win && win.dataset && win.dataset.minHeight) || '', 10);
    const minWidth = Number.isFinite(explicitMinWidth) ? explicitMinWidth : dialogMinWidth;
    const minHeight = Number.isFinite(explicitMinHeight) ? explicitMinHeight : dialogMinHeight;
    const maxWidth = Math.max(minWidth, viewportWidth - (edgePadding * 2));
    const maxHeight = Math.max(minHeight, viewportHeight - (edgePadding * 2));
    const nextWidth = Math.min(Math.max(width, minWidth), maxWidth);
    const nextHeight = Math.min(Math.max(height, minHeight), maxHeight);
    const minLeft = edgePadding;
    const minTop = topPadding;
    const maxLeft = Math.max(minLeft, viewportWidth - nextWidth - edgePadding);
    const maxTop = Math.max(minTop, viewportHeight - nextHeight - edgePadding);
    return {
      left: Math.min(maxLeft, Math.max(minLeft, left)),
      top: Math.min(maxTop, Math.max(minTop, top)),
      width: nextWidth,
      height: nextHeight
    };
  }

  function applyFloatingWindowRect(win, left, top, width, height){
    const rect = clampFloatingWindow(win, left, top, width, height);
    win.style.left = `${Math.round(rect.left)}px`;
    win.style.top = `${Math.round(rect.top)}px`;
    win.style.width = `${Math.round(rect.width)}px`;
    win.style.height = `${Math.round(rect.height)}px`;
  }

  function openFloatingWindow(win){
    if (!win) return;
    win.style.display = 'flex';
    win.setAttribute('aria-hidden', 'false');
    const viewportWidth = window.innerWidth || document.documentElement.clientWidth || 1280;
    const viewportHeight = window.innerHeight || document.documentElement.clientHeight || 720;
    const isCenteredDialog = win.classList.contains('studio-floating-window--dialog');
    const isCenteredOpen = isCenteredDialog || win.classList.contains('studio-floating-window--centered');
    const defaultWidth = isCenteredDialog
      ? Math.min(420, viewportWidth - 48)
      : Math.min(920, viewportWidth - 48);
    const defaultHeight = win.classList.contains('studio-settings-window')
      ? Math.min(Math.max(Math.round(viewportHeight * 0.88), 720), viewportHeight - 24)
      : (isCenteredDialog ? Math.min(180, viewportHeight - 48) : Math.min(620, viewportHeight - 64));
    const currentWidth = Number.parseInt(win.style.width || '', 10);
    const currentHeight = Number.parseInt(win.style.height || '', 10);
    const width = Number.isFinite(currentWidth) ? currentWidth : defaultWidth;
    const height = Number.isFinite(currentHeight) ? currentHeight : defaultHeight;
    const left = Math.round((viewportWidth - width) / 2);
    const top = isCenteredOpen ? Math.round((viewportHeight - height) / 2) : 8;
    if (typeof window.scrollTo === 'function') {
      window.scrollTo({top: 0, left: window.scrollX || 0, behavior: 'auto'});
    }
    applyFloatingWindowRect(win, left, top, width, height);
    bringWindowToFront(win);
  }

  function closeFloatingWindow(win){
    if (!win) return;
    win.setAttribute('aria-hidden', 'true');
    win.style.display = 'none';
  }

  function bringWindowToFront(win){
    if (!win) return;
    if (win.dataset && win.dataset.schedulerTopmost === '1') {
      win.style.zIndex = '2147483647';
      return;
    }
    const nextZIndex = String((Number(shell.dataset.topZIndex || 40) + 1));
    shell.dataset.topZIndex = nextZIndex;
    win.style.zIndex = nextZIndex;
  }

  function initializeStudioUsersAddWindow(){
    const win = els.studioUsersAddWindow;
    if (!win) return;
    const titlebar = win.querySelector('.studio-floating-window__titlebar');
    const handle = win.querySelector('.panel-resize-handle');
    let dragState = null;
    let resizeState = null;

    function onPointerMove(event){
      if (dragState){
        applyFloatingWindowRect(
          win,
          dragState.startLeft + (event.clientX - dragState.startClientX),
          dragState.startTop + (event.clientY - dragState.startClientY),
          dragState.width,
          dragState.height
        );
      } else if (resizeState){
        applyFloatingWindowRect(
          win,
          resizeState.left,
          resizeState.top,
          resizeState.startWidth + (event.clientX - resizeState.startClientX),
          resizeState.startHeight + (event.clientY - resizeState.startClientY)
        );
      }
    }

    function endPointerInteraction(){
      dragState = null;
      resizeState = null;
      win.classList.remove('is-dragging');
      win.classList.remove('is-resizing');
      document.removeEventListener('pointermove', onPointerMove);
      document.removeEventListener('pointerup', endPointerInteraction);
      document.removeEventListener('pointercancel', endPointerInteraction);
    }

    if (titlebar){
      titlebar.addEventListener('pointerdown', event => {
        if (event.button !== 0) return;
        if (event.target.closest('button, a, input, select, textarea')) return;
        event.preventDefault();
        bringWindowToFront(win);
        dragState = {
          startClientX: event.clientX,
          startClientY: event.clientY,
          startLeft: Number.parseInt(win.style.left || '80', 10),
          startTop: Number.parseInt(win.style.top || '96', 10),
          width: win.offsetWidth,
          height: win.offsetHeight
        };
        win.classList.add('is-dragging');
        document.addEventListener('pointermove', onPointerMove);
        document.addEventListener('pointerup', endPointerInteraction);
        document.addEventListener('pointercancel', endPointerInteraction);
      });
    }

    if (handle){
      handle.addEventListener('pointerdown', event => {
        if (event.button !== 0) return;
        event.preventDefault();
        event.stopPropagation();
        bringWindowToFront(win);
        resizeState = {
          startClientX: event.clientX,
          startClientY: event.clientY,
          left: Number.parseInt(win.style.left || '80', 10),
          top: Number.parseInt(win.style.top || '96', 10),
          startWidth: win.offsetWidth,
          startHeight: win.offsetHeight
        };
        win.classList.add('is-resizing');
        document.addEventListener('pointermove', onPointerMove);
        document.addEventListener('pointerup', endPointerInteraction);
        document.addEventListener('pointercancel', endPointerInteraction);
      });
    }

    if (els.studioUsersAddClose) els.studioUsersAddClose.addEventListener('click', () => { hideStudioUsersForms(); setStudioUsersFeedback(''); });
    if (els.studioUsersAddCancel) {
      els.studioUsersAddCancel.addEventListener('click', () => {
        hideStudioUsersForms();
        setStudioUsersFeedback('');
      });
    }
    if (win){
      win.addEventListener('pointerdown', () => bringWindowToFront(win));
      window.addEventListener('resize', () => {
        if (win.getAttribute('aria-hidden') === 'true') return;
        applyFloatingWindowRect(
          win,
          Number.parseInt(win.style.left || '80', 10),
          Number.parseInt(win.style.top || '96', 10),
          win.offsetWidth,
          win.offsetHeight
        );
      });
    }
    document.addEventListener('keydown', event => {
      if (event.key !== 'Escape' || !win) return;
      if (win.getAttribute('aria-hidden') === 'true' || win.style.display === 'none') return;
      event.preventDefault();
      event.stopPropagation();
      if (typeof event.stopImmediatePropagation === 'function') event.stopImmediatePropagation();
      hideStudioUsersForms();
      setStudioUsersFeedback('');
    }, true);
  }

  function initializeSimpleFloatingWindow(win){
    if (!win || win.dataset.simpleFloatingInit === '1') return;
    win.dataset.simpleFloatingInit = '1';
    const titlebar = win.querySelector('.studio-floating-window__titlebar');
    let dragState = null;

    function onPointerMove(event){
      if (!dragState) return;
      applyFloatingWindowRect(
        win,
        dragState.startLeft + (event.clientX - dragState.startClientX),
        dragState.startTop + (event.clientY - dragState.startClientY),
        dragState.width,
        dragState.height
      );
      dragState.moved = true;
    }

    function endPointerInteraction(){
      dragState = null;
      win.classList.remove('is-dragging');
      document.removeEventListener('pointermove', onPointerMove);
      document.removeEventListener('pointerup', endPointerInteraction);
      document.removeEventListener('pointercancel', endPointerInteraction);
    }

    if (titlebar){
      titlebar.addEventListener('pointerdown', event => {
        if (event.button !== 0) return;
        const target = event.target;
        if (target && typeof target.closest === 'function' && (target.closest('button') || target.closest('input') || target.closest('textarea') || target.closest('select') || target.closest('label'))) return;
        const rect = win.getBoundingClientRect();
        dragState = {
          startLeft: rect.left,
          startTop: rect.top,
          startClientX: event.clientX,
          startClientY: event.clientY,
          width: rect.width,
          height: rect.height,
          moved: false
        };
        win.classList.add('is-dragging');
        bringWindowToFront(win);
        document.addEventListener('pointermove', onPointerMove);
        document.addEventListener('pointerup', endPointerInteraction);
        document.addEventListener('pointercancel', endPointerInteraction);
        event.preventDefault();
      });
    }

    win.addEventListener('pointerdown', () => bringWindowToFront(win));
    window.addEventListener('resize', () => {
      if (win.getAttribute('aria-hidden') === 'true' || win.style.display === 'none') return;
      const rect = win.getBoundingClientRect();
      applyFloatingWindowRect(win, rect.left, rect.top, rect.width, rect.height);
    });
  }

  function initializeStudioUsersDeleteWindow(){
    const win = els.studioUsersDeleteWindow;
    if (!win) return;
    const titlebar = win.querySelector('.studio-floating-window__titlebar');
    const handle = win.querySelector('.panel-resize-handle');
    let dragState = null;
    let resizeState = null;

    function onPointerMove(event){
      if (dragState){
        applyFloatingWindowRect(
          win,
          dragState.startLeft + (event.clientX - dragState.startClientX),
          dragState.startTop + (event.clientY - dragState.startClientY),
          dragState.width,
          dragState.height
        );
      } else if (resizeState){
        applyFloatingWindowRect(
          win,
          resizeState.left,
          resizeState.top,
          resizeState.startWidth + (event.clientX - resizeState.startClientX),
          resizeState.startHeight + (event.clientY - resizeState.startClientY)
        );
      }
    }

    function endPointerInteraction(){
      dragState = null;
      resizeState = null;
      win.classList.remove('is-dragging');
      win.classList.remove('is-resizing');
      document.removeEventListener('pointermove', onPointerMove);
      document.removeEventListener('pointerup', endPointerInteraction);
      document.removeEventListener('pointercancel', endPointerInteraction);
    }

    if (titlebar){
      titlebar.addEventListener('pointerdown', event => {
        if (event.button !== 0) return;
        if (event.target.closest('button, a, input, select, textarea')) return;
        event.preventDefault();
        bringWindowToFront(win);
        dragState = {
          startClientX: event.clientX,
          startClientY: event.clientY,
          startLeft: Number.parseInt(win.style.left || '80', 10),
          startTop: Number.parseInt(win.style.top || '96', 10),
          width: win.offsetWidth,
          height: win.offsetHeight
        };
        win.classList.add('is-dragging');
        document.addEventListener('pointermove', onPointerMove);
        document.addEventListener('pointerup', endPointerInteraction);
        document.addEventListener('pointercancel', endPointerInteraction);
      });
    }

    if (handle && !win.classList.contains('studio-floating-window--no-resize')){
      handle.addEventListener('pointerdown', event => {
        if (event.button !== 0) return;
        event.preventDefault();
        event.stopPropagation();
        bringWindowToFront(win);
        resizeState = {
          startClientX: event.clientX,
          startClientY: event.clientY,
          left: Number.parseInt(win.style.left || '80', 10),
          top: Number.parseInt(win.style.top || '96', 10),
          startWidth: win.offsetWidth,
          startHeight: win.offsetHeight
        };
        win.classList.add('is-resizing');
        document.addEventListener('pointermove', onPointerMove);
        document.addEventListener('pointerup', endPointerInteraction);
        document.addEventListener('pointercancel', endPointerInteraction);
      });
    }

    if (els.studioUsersDeleteClose) els.studioUsersDeleteClose.addEventListener('click', () => { hideStudioUsersForms(); setStudioUsersFeedback(''); });
    if (els.studioUsersDeleteCancel) {
      els.studioUsersDeleteCancel.addEventListener('click', () => {
        hideStudioUsersForms();
        setStudioUsersFeedback('');
      });
    }
    win.addEventListener('pointerdown', () => bringWindowToFront(win));
    window.addEventListener('resize', () => {
      if (win.getAttribute('aria-hidden') === 'true') return;
      applyFloatingWindowRect(
        win,
        Number.parseInt(win.style.left || '80', 10),
        Number.parseInt(win.style.top || '96', 10),
        win.offsetWidth,
        win.offsetHeight
      );
    });
    document.addEventListener('keydown', event => {
      if (event.key !== 'Escape' || !win) return;
      if (win.getAttribute('aria-hidden') === 'true' || win.style.display === 'none') return;
      event.preventDefault();
      event.stopPropagation();
      if (typeof event.stopImmediatePropagation === 'function') event.stopImmediatePropagation();
      hideStudioUsersForms();
      setStudioUsersFeedback('');
    }, true);
  }

  function initializeStudioUsersPasswordWindow(){
    const win = els.studioUsersPasswordWindow;
    if (!win) return;
    const titlebar = win.querySelector('.studio-floating-window__titlebar');
    const handle = win.querySelector('.panel-resize-handle');
    let dragState = null;
    let resizeState = null;

    function onPointerMove(event){
      if (dragState){
        applyFloatingWindowRect(
          win,
          dragState.startLeft + (event.clientX - dragState.startClientX),
          dragState.startTop + (event.clientY - dragState.startClientY),
          dragState.width,
          dragState.height
        );
      } else if (resizeState){
        applyFloatingWindowRect(
          win,
          resizeState.left,
          resizeState.top,
          resizeState.startWidth + (event.clientX - resizeState.startClientX),
          resizeState.startHeight + (event.clientY - resizeState.startClientY)
        );
      }
    }

    function endPointerInteraction(){
      dragState = null;
      resizeState = null;
      win.classList.remove('is-dragging');
      win.classList.remove('is-resizing');
      document.removeEventListener('pointermove', onPointerMove);
      document.removeEventListener('pointerup', endPointerInteraction);
      document.removeEventListener('pointercancel', endPointerInteraction);
    }

    if (titlebar){
      titlebar.addEventListener('pointerdown', event => {
        if (event.button !== 0) return;
        if (event.target.closest('button, a, input, select, textarea')) return;
        event.preventDefault();
        bringWindowToFront(win);
        dragState = {
          startClientX: event.clientX,
          startClientY: event.clientY,
          startLeft: Number.parseInt(win.style.left || '80', 10),
          startTop: Number.parseInt(win.style.top || '96', 10),
          width: win.offsetWidth,
          height: win.offsetHeight
        };
        win.classList.add('is-dragging');
        document.addEventListener('pointermove', onPointerMove);
        document.addEventListener('pointerup', endPointerInteraction);
        document.addEventListener('pointercancel', endPointerInteraction);
      });
    }

    if (handle){
      handle.addEventListener('pointerdown', event => {
        if (event.button !== 0) return;
        event.preventDefault();
        event.stopPropagation();
        bringWindowToFront(win);
        resizeState = {
          startClientX: event.clientX,
          startClientY: event.clientY,
          left: Number.parseInt(win.style.left || '80', 10),
          top: Number.parseInt(win.style.top || '96', 10),
          startWidth: win.offsetWidth,
          startHeight: win.offsetHeight
        };
        win.classList.add('is-resizing');
        document.addEventListener('pointermove', onPointerMove);
        document.addEventListener('pointerup', endPointerInteraction);
        document.addEventListener('pointercancel', endPointerInteraction);
      });
    }

    if (els.studioUsersPasswordClose) els.studioUsersPasswordClose.addEventListener('click', () => { hideStudioUsersForms(); setStudioUsersFeedback(''); });
    if (els.studioUsersPasswordCancel) {
      els.studioUsersPasswordCancel.addEventListener('click', () => {
        hideStudioUsersForms();
        setStudioUsersFeedback('');
      });
    }
    if (win){
      win.addEventListener('pointerdown', () => bringWindowToFront(win));
      window.addEventListener('resize', () => {
        if (win.getAttribute('aria-hidden') === 'true') return;
        applyFloatingWindowRect(
          win,
          Number.parseInt(win.style.left || '80', 10),
          Number.parseInt(win.style.top || '96', 10),
          win.offsetWidth,
          win.offsetHeight
        );
      });
    }
    document.addEventListener('keydown', event => {
      if (event.key !== 'Escape' || !win) return;
      if (win.getAttribute('aria-hidden') === 'true' || win.style.display === 'none') return;
      event.preventDefault();
      event.stopPropagation();
      if (typeof event.stopImmediatePropagation === 'function') event.stopImmediatePropagation();
      hideStudioUsersForms();
      setStudioUsersFeedback('');
    }, true);
  }


  function initializeStudioAutodjLoadWindow(){
    const win = els.studioAutodjLoadWindow;
    if (!win) return;
    const titlebar = win.querySelector('.studio-floating-window__titlebar');
    const handle = win.querySelector('.panel-resize-handle');
    let dragState = null;
    let resizeState = null;

    function onPointerMove(event){
      if (dragState){
        applyFloatingWindowRect(
          win,
          dragState.startLeft + (event.clientX - dragState.startClientX),
          dragState.startTop + (event.clientY - dragState.startClientY),
          dragState.width,
          dragState.height
        );
      } else if (resizeState){
        applyFloatingWindowRect(
          win,
          resizeState.left,
          resizeState.top,
          resizeState.startWidth + (event.clientX - resizeState.startClientX),
          resizeState.startHeight + (event.clientY - resizeState.startClientY)
        );
      }
    }

    function endPointerInteraction(){
      dragState = null;
      resizeState = null;
      win.classList.remove('is-dragging');
      win.classList.remove('is-resizing');
      document.removeEventListener('pointermove', onPointerMove);
      document.removeEventListener('pointerup', endPointerInteraction);
      document.removeEventListener('pointercancel', endPointerInteraction);
    }

    if (titlebar){
      titlebar.addEventListener('pointerdown', event => {
        if (event.button !== 0) return;
        if (event.target.closest('button, a, input, select, textarea')) return;
        event.preventDefault();
        bringWindowToFront(win);
        dragState = {
          startClientX: event.clientX,
          startClientY: event.clientY,
          startLeft: Number.parseInt(win.style.left || '80', 10),
          startTop: Number.parseInt(win.style.top || '96', 10),
          width: win.offsetWidth,
          height: win.offsetHeight
        };
        win.classList.add('is-dragging');
        document.addEventListener('pointermove', onPointerMove);
        document.addEventListener('pointerup', endPointerInteraction);
        document.addEventListener('pointercancel', endPointerInteraction);
      });
    }

    if (handle){
      handle.addEventListener('pointerdown', event => {
        if (event.button !== 0) return;
        event.preventDefault();
        event.stopPropagation();
        bringWindowToFront(win);
        resizeState = {
          startClientX: event.clientX,
          startClientY: event.clientY,
          left: Number.parseInt(win.style.left || '80', 10),
          top: Number.parseInt(win.style.top || '96', 10),
          startWidth: win.offsetWidth,
          startHeight: win.offsetHeight
        };
        win.classList.add('is-resizing');
        document.addEventListener('pointermove', onPointerMove);
        document.addEventListener('pointerup', endPointerInteraction);
        document.addEventListener('pointercancel', endPointerInteraction);
      });
    }

    win.addEventListener('pointerdown', () => bringWindowToFront(win));
    window.addEventListener('resize', () => {
      if (win.getAttribute('aria-hidden') === 'true') return;
      applyFloatingWindowRect(
        win,
        Number.parseInt(win.style.left || '80', 10),
        Number.parseInt(win.style.top || '96', 10),
        win.offsetWidth,
        win.offsetHeight
      );
    });
    document.addEventListener('keydown', event => {
      if (event.key !== 'Escape' || !win) return;
      if (win.getAttribute('aria-hidden') === 'true' || win.style.display === 'none') return;
      event.preventDefault();
      event.stopPropagation();
      if (typeof event.stopImmediatePropagation === 'function') event.stopImmediatePropagation();
      closeStudioAutodjLoadWindow();
    }, true);
  }



  function initializeStudioAutodjSaveWindow(){
    const win = els.studioAutodjSaveWindow;
    if (!win) return;
    const titlebar = win.querySelector('.studio-floating-window__titlebar');
    const handle = win.querySelector('.panel-resize-handle');
    let dragState = null;
    let resizeState = null;

    function onPointerMove(event){
      if (dragState){
        applyFloatingWindowRect(
          win,
          dragState.startLeft + (event.clientX - dragState.startClientX),
          dragState.startTop + (event.clientY - dragState.startClientY),
          dragState.width,
          dragState.height
        );
      } else if (resizeState){
        applyFloatingWindowRect(
          win,
          resizeState.left,
          resizeState.top,
          resizeState.startWidth + (event.clientX - resizeState.startClientX),
          resizeState.startHeight + (event.clientY - resizeState.startClientY)
        );
      }
    }

    function endPointerInteraction(){
      dragState = null;
      resizeState = null;
      win.classList.remove('is-dragging');
      win.classList.remove('is-resizing');
      document.removeEventListener('pointermove', onPointerMove);
      document.removeEventListener('pointerup', endPointerInteraction);
      document.removeEventListener('pointercancel', endPointerInteraction);
    }

    if (titlebar){
      titlebar.addEventListener('pointerdown', event => {
        if (event.button !== 0) return;
        if (event.target.closest('button, a, input, select, textarea')) return;
        event.preventDefault();
        bringWindowToFront(win);
        dragState = {
          startClientX: event.clientX,
          startClientY: event.clientY,
          startLeft: Number.parseInt(win.style.left || '80', 10),
          startTop: Number.parseInt(win.style.top || '96', 10),
          width: win.offsetWidth,
          height: win.offsetHeight
        };
        win.classList.add('is-dragging');
        document.addEventListener('pointermove', onPointerMove);
        document.addEventListener('pointerup', endPointerInteraction);
        document.addEventListener('pointercancel', endPointerInteraction);
      });
    }

    if (handle){
      handle.addEventListener('pointerdown', event => {
        if (event.button !== 0) return;
        event.preventDefault();
        event.stopPropagation();
        bringWindowToFront(win);
        resizeState = {
          startClientX: event.clientX,
          startClientY: event.clientY,
          left: Number.parseInt(win.style.left || '80', 10),
          top: Number.parseInt(win.style.top || '96', 10),
          startWidth: win.offsetWidth,
          startHeight: win.offsetHeight
        };
        win.classList.add('is-resizing');
        document.addEventListener('pointermove', onPointerMove);
        document.addEventListener('pointerup', endPointerInteraction);
        document.addEventListener('pointercancel', endPointerInteraction);
      });
    }

    win.addEventListener('pointerdown', () => bringWindowToFront(win));
    window.addEventListener('resize', () => {
      if (win.getAttribute('aria-hidden') === 'true') return;
      applyFloatingWindowRect(
        win,
        Number.parseInt(win.style.left || '80', 10),
        Number.parseInt(win.style.top || '96', 10),
        win.offsetWidth,
        win.offsetHeight
      );
    });
    document.addEventListener('keydown', event => {
      if (event.key !== 'Escape' || !win) return;
      if (win.getAttribute('aria-hidden') === 'true' || win.style.display === 'none') return;
      event.preventDefault();
      event.stopPropagation();
      if (typeof event.stopImmediatePropagation === 'function') event.stopImmediatePropagation();
      closeStudioAutodjSaveWindow();
    }, true);
  }


  function initializeScriptsAddScriptWindow(){
    const win = els.scriptsAddScriptWindow;
    if (!win) return;
    const titlebar = win.querySelector('.studio-floating-window__titlebar');
    const handle = win.querySelector('.panel-resize-handle');
    let dragState = null;
    let resizeState = null;

    function onPointerMove(event){
      if (dragState){
        applyFloatingWindowRect(
          win,
          dragState.startLeft + (event.clientX - dragState.startClientX),
          dragState.startTop + (event.clientY - dragState.startClientY),
          dragState.width,
          dragState.height
        );
      } else if (resizeState){
        applyFloatingWindowRect(
          win,
          resizeState.left,
          resizeState.top,
          resizeState.startWidth + (event.clientX - resizeState.startClientX),
          resizeState.startHeight + (event.clientY - resizeState.startClientY)
        );
      }
    }

    function endPointerInteraction(){
      dragState = null;
      resizeState = null;
      win.classList.remove('is-dragging');
      win.classList.remove('is-resizing');
      document.removeEventListener('pointermove', onPointerMove);
      document.removeEventListener('pointerup', endPointerInteraction);
      document.removeEventListener('pointercancel', endPointerInteraction);
    }

    if (titlebar){
      titlebar.addEventListener('pointerdown', event => {
        if (event.button !== 0) return;
        if (event.target.closest('button, a, input, select, textarea, label')) return;
        event.preventDefault();
        bringWindowToFront(win);
        dragState = {
          startClientX: event.clientX,
          startClientY: event.clientY,
          startLeft: Number.parseInt(win.style.left || '80', 10),
          startTop: Number.parseInt(win.style.top || '96', 10),
          width: win.offsetWidth,
          height: win.offsetHeight
        };
        win.classList.add('is-dragging');
        document.addEventListener('pointermove', onPointerMove);
        document.addEventListener('pointerup', endPointerInteraction);
        document.addEventListener('pointercancel', endPointerInteraction);
      });
    }

    if (handle){
      handle.addEventListener('pointerdown', event => {
        if (event.button !== 0) return;
        event.preventDefault();
        event.stopPropagation();
        bringWindowToFront(win);
        resizeState = {
          startClientX: event.clientX,
          startClientY: event.clientY,
          left: Number.parseInt(win.style.left || '80', 10),
          top: Number.parseInt(win.style.top || '96', 10),
          startWidth: win.offsetWidth,
          startHeight: win.offsetHeight
        };
        win.classList.add('is-resizing');
        document.addEventListener('pointermove', onPointerMove);
        document.addEventListener('pointerup', endPointerInteraction);
        document.addEventListener('pointercancel', endPointerInteraction);
      });
    }

    win.addEventListener('pointerdown', () => bringWindowToFront(win));
    window.addEventListener('resize', () => {
      if (win.getAttribute('aria-hidden') === 'true') return;
      applyFloatingWindowRect(
        win,
        Number.parseInt(win.style.left || '80', 10),
        Number.parseInt(win.style.top || '96', 10),
        win.offsetWidth,
        win.offsetHeight
      );
    });
  }


  function initializeScriptsConfigWindow(){
    const win = els.scriptsConfigWindow;
    if (!win) return;
    const titlebar = win.querySelector('.studio-floating-window__titlebar');
    const handle = win.querySelector('.panel-resize-handle');
    let dragState = null;
    let resizeState = null;

    function onPointerMove(event){
      if (dragState){
        applyFloatingWindowRect(
          win,
          dragState.startLeft + (event.clientX - dragState.startClientX),
          dragState.startTop + (event.clientY - dragState.startClientY),
          dragState.width,
          dragState.height
        );
      } else if (resizeState){
        applyFloatingWindowRect(
          win,
          resizeState.left,
          resizeState.top,
          resizeState.startWidth + (event.clientX - resizeState.startClientX),
          resizeState.startHeight + (event.clientY - resizeState.startClientY)
        );
      }
    }

    function endPointerInteraction(){
      dragState = null;
      resizeState = null;
      win.classList.remove('is-dragging');
      win.classList.remove('is-resizing');
      document.removeEventListener('pointermove', onPointerMove);
      document.removeEventListener('pointerup', endPointerInteraction);
      document.removeEventListener('pointercancel', endPointerInteraction);
    }

    if (titlebar){
      titlebar.addEventListener('pointerdown', event => {
        if (event.button !== 0) return;
        if (event.target.closest('button, a, input, select, textarea, label')) return;
        event.preventDefault();
        bringWindowToFront(win);
        dragState = {
          startClientX: event.clientX,
          startClientY: event.clientY,
          startLeft: Number.parseInt(win.style.left || '80', 10),
          startTop: Number.parseInt(win.style.top || '96', 10),
          width: win.offsetWidth,
          height: win.offsetHeight
        };
        win.classList.add('is-dragging');
        document.addEventListener('pointermove', onPointerMove);
        document.addEventListener('pointerup', endPointerInteraction);
        document.addEventListener('pointercancel', endPointerInteraction);
      });
    }

    if (handle){
      handle.addEventListener('pointerdown', event => {
        if (event.button !== 0) return;
        event.preventDefault();
        event.stopPropagation();
        bringWindowToFront(win);
        resizeState = {
          startClientX: event.clientX,
          startClientY: event.clientY,
          left: Number.parseInt(win.style.left || '80', 10),
          top: Number.parseInt(win.style.top || '96', 10),
          startWidth: win.offsetWidth,
          startHeight: win.offsetHeight
        };
        win.classList.add('is-resizing');
        document.addEventListener('pointermove', onPointerMove);
        document.addEventListener('pointerup', endPointerInteraction);
        document.addEventListener('pointercancel', endPointerInteraction);
      });
    }

    win.addEventListener('pointerdown', () => bringWindowToFront(win));
    window.addEventListener('resize', () => {
      if (win.getAttribute('aria-hidden') === 'true') return;
      applyFloatingWindowRect(
        win,
        Number.parseInt(win.style.left || '80', 10),
        Number.parseInt(win.style.top || '96', 10),
        win.offsetWidth,
        win.offsetHeight
      );
    });
  }



  function initializePlaylistAddUrlWindow(){
    const win = els.playlistAddUrlWindow;
    if (!win) return;
    const titlebar = win.querySelector('.studio-floating-window__titlebar');
    const handle = win.querySelector('.panel-resize-handle');
    let dragState = null;
    let resizeState = null;

    function onPointerMove(event){
      if (dragState){
        applyFloatingWindowRect(
          win,
          dragState.startLeft + (event.clientX - dragState.startClientX),
          dragState.startTop + (event.clientY - dragState.startClientY),
          dragState.width,
          dragState.height
        );
      } else if (resizeState){
        applyFloatingWindowRect(
          win,
          resizeState.left,
          resizeState.top,
          resizeState.startWidth + (event.clientX - resizeState.startClientX),
          resizeState.startHeight + (event.clientY - resizeState.startClientY)
        );
      }
    }

    function endPointerInteraction(){
      dragState = null;
      resizeState = null;
      win.classList.remove('is-dragging');
      win.classList.remove('is-resizing');
      document.removeEventListener('pointermove', onPointerMove);
      document.removeEventListener('pointerup', endPointerInteraction);
      document.removeEventListener('pointercancel', endPointerInteraction);
    }

    if (titlebar){
      titlebar.addEventListener('pointerdown', event => {
        if (event.button !== 0) return;
        if (event.target.closest('button, a, input, select, textarea, label')) return;
        event.preventDefault();
        bringWindowToFront(win);
        dragState = {
          startClientX: event.clientX,
          startClientY: event.clientY,
          startLeft: Number.parseInt(win.style.left || '80', 10),
          startTop: Number.parseInt(win.style.top || '96', 10),
          width: win.offsetWidth,
          height: win.offsetHeight
        };
        win.classList.add('is-dragging');
        document.addEventListener('pointermove', onPointerMove);
        document.addEventListener('pointerup', endPointerInteraction);
        document.addEventListener('pointercancel', endPointerInteraction);
      });
    }

    if (handle){
      handle.addEventListener('pointerdown', event => {
        if (event.button !== 0) return;
        event.preventDefault();
        event.stopPropagation();
        bringWindowToFront(win);
        resizeState = {
          startClientX: event.clientX,
          startClientY: event.clientY,
          left: Number.parseInt(win.style.left || '80', 10),
          top: Number.parseInt(win.style.top || '96', 10),
          startWidth: win.offsetWidth,
          startHeight: win.offsetHeight
        };
        win.classList.add('is-resizing');
        document.addEventListener('pointermove', onPointerMove);
        document.addEventListener('pointerup', endPointerInteraction);
        document.addEventListener('pointercancel', endPointerInteraction);
      });
    }

    win.addEventListener('pointerdown', () => bringWindowToFront(win));
    window.addEventListener('resize', () => {
      if (win.getAttribute('aria-hidden') === 'true') return;
      applyFloatingWindowRect(
        win,
        Number.parseInt(win.style.left || '80', 10),
        Number.parseInt(win.style.top || '96', 10),
        win.offsetWidth,
        win.offsetHeight
      );
    });
  }
  function initializeFloatingPlaylistBrowserWindow(){
    const win = els.playlistAddFilesWindow;
    if (!win) return;
    const titlebar = win.querySelector('.studio-floating-window__titlebar');
    const handle = win.querySelector('.panel-resize-handle');
    let dragState = null;
    let resizeState = null;

    function onPointerMove(event){
      if (dragState){
        applyFloatingWindowRect(
          win,
          dragState.startLeft + (event.clientX - dragState.startClientX),
          dragState.startTop + (event.clientY - dragState.startClientY),
          dragState.width,
          dragState.height
        );
      } else if (resizeState){
        applyFloatingWindowRect(
          win,
          resizeState.left,
          resizeState.top,
          resizeState.startWidth + (event.clientX - resizeState.startClientX),
          resizeState.startHeight + (event.clientY - resizeState.startClientY)
        );
      }
    }

    function endPointerInteraction(){
      dragState = null;
      resizeState = null;
      win.classList.remove('is-dragging');
      win.classList.remove('is-resizing');
      document.removeEventListener('pointermove', onPointerMove);
      document.removeEventListener('pointerup', endPointerInteraction);
      document.removeEventListener('pointercancel', endPointerInteraction);
    }

    if (titlebar){
      titlebar.addEventListener('pointerdown', event => {
        if (event.button !== 0) return;
        if (event.target.closest('button, a, input, select, textarea')) return;
        event.preventDefault();
        bringWindowToFront(win);
        dragState = {
          startClientX: event.clientX,
          startClientY: event.clientY,
          startLeft: Number.parseInt(win.style.left || '80', 10),
          startTop: Number.parseInt(win.style.top || '96', 10),
          width: win.offsetWidth,
          height: win.offsetHeight
        };
        win.classList.add('is-dragging');
        document.addEventListener('pointermove', onPointerMove);
        document.addEventListener('pointerup', endPointerInteraction);
        document.addEventListener('pointercancel', endPointerInteraction);
      });
    }

    if (handle){
      handle.addEventListener('pointerdown', event => {
        if (event.button !== 0) return;
        event.preventDefault();
        event.stopPropagation();
        bringWindowToFront(win);
        resizeState = {
          startClientX: event.clientX,
          startClientY: event.clientY,
          left: Number.parseInt(win.style.left || '80', 10),
          top: Number.parseInt(win.style.top || '96', 10),
          startWidth: win.offsetWidth,
          startHeight: win.offsetHeight
        };
        win.classList.add('is-resizing');
        document.addEventListener('pointermove', onPointerMove);
        document.addEventListener('pointerup', endPointerInteraction);
        document.addEventListener('pointercancel', endPointerInteraction);
      });
    }

    window.addEventListener('resize', () => {
      if (win.getAttribute('aria-hidden') === 'true') return;
      applyFloatingWindowRect(
        win,
        Number.parseInt(win.style.left || '80', 10),
        Number.parseInt(win.style.top || '96', 10),
        win.offsetWidth,
        win.offsetHeight
      );
    });
  }
  function initializePlaylistMenuActions(){
    if (!els.playlistContextMenu) return;
    els.playlistContextMenu.querySelectorAll('[data-playlist-menu-action]').forEach(btn => {
      btn.addEventListener('click', async event => {
        event.preventDefault();
        event.stopPropagation();
        const action = btn.dataset.playlistMenuAction;
        const targetCategoryId = playlistContextTargetId || selectedCategoryId;
        closePlaylistContextMenu();
        if (action === 'new') {
          await createPlaylistCategory();
        } else if (action === 'delete') {
          await deletePlaylistCategory();
        } else if (action === 'rename') {
          if (targetCategoryId) startPlaylistRename(targetCategoryId);
        } else if (action === 'add-files') {
          if (targetCategoryId) await openPlaylistBrowserWindow(targetCategoryId, PLAYLIST_BROWSER_MODES.FILES);
        } else if (action === 'add-directory') {
          if (targetCategoryId) await openPlaylistBrowserWindow(targetCategoryId, PLAYLIST_BROWSER_MODES.DIRECTORIES);
        } else if (action === 'add-url') {
          if (targetCategoryId) await openPlaylistAddUrlWindow(targetCategoryId);
        }
      });
    });
  }

  function initializeQueueToolbar(){
    document.querySelectorAll('[data-queue-action]').forEach(btn => {
      btn.addEventListener('click', event => {
        const action = btn.dataset.queueAction;
        if (action === 'add') {
          event.preventDefault();
          event.stopPropagation();
          if (tracksAddMenuOpen && tracksAddMenuTarget === 'queue') {
            closeTracksAddMenu();
            return;
          }
          const rect = btn.getBoundingClientRect();
          openTracksAddMenu(rect.left, rect.bottom + 4, 'queue');
        } else if (action === 'refresh') {
          loadQueue();
        } else if (action === 'remove') {
          removeSelectedQueueItems();
        } else if (action === 'search') {
          event.preventDefault();
          event.stopPropagation();
          openStudioSearch('queue');
        }
      });
    });
    updateQueueToolbarState();
  }

  function bringPanelToFront(panel){
    const nextZIndex = String((Number(shell.dataset.topZIndex || 40) + 1));
    shell.dataset.topZIndex = nextZIndex;
    panel.style.zIndex = nextZIndex;
  }

  function initializeResizablePanels(){
    let resizeState = null;

    function onPointerMove(event){
      if (!resizeState) return;
      const panel = resizeState.panel;
      const workspace = panel.parentElement;
      const baseLeft = panel.offsetLeft + Number(panel.dataset.translateX || 0);
      const baseTop = panel.offsetTop + Number(panel.dataset.translateY || 0);
      const workspaceWidth = workspace ? workspace.clientWidth : window.innerWidth;
      const workspaceHeight = workspace ? workspace.clientHeight : window.innerHeight;
      const maxWidth = Math.max(resizeState.minWidth, workspaceWidth - baseLeft);
      const maxHeight = Math.max(resizeState.minHeight, workspaceHeight - baseTop);
      const workspaceScale = getStudioWorkspaceScale();
      const rawWidth = Math.min(maxWidth, Math.max(resizeState.minWidth, resizeState.startWidth + ((event.clientX - resizeState.startClientX) / workspaceScale)));
      const rawHeight = Math.min(maxHeight, Math.max(resizeState.minHeight, resizeState.startHeight + ((event.clientY - resizeState.startClientY) / workspaceScale)));
      const nextWidth = Math.min(maxWidth, Math.max(resizeState.minWidth, snapToPanelGrid(rawWidth)));
      const nextHeight = Math.min(maxHeight, Math.max(resizeState.minHeight, snapToPanelGrid(rawHeight)));
      panel.style.width = `${Math.round(nextWidth)}px`;
      panel.style.height = `${Math.round(nextHeight)}px`;
      normalizePanelPosition(panel);
      updateStudioWorkspaceScale();
    }

    function endResize(){
      if (!resizeState) return;
      resizeState.panel.classList.remove('is-resizing');
      persistPanelSize(resizeState.panel);
      persistPanelPosition(resizeState.panel);
      resizeState = null;
      document.removeEventListener('pointermove', onPointerMove);
      document.removeEventListener('pointerup', endResize);
      document.removeEventListener('pointercancel', endResize);
    }

    panelElements.forEach(panel => {
      let handle = panel.querySelector('.panel-resize-handle');
      if (!handle) {
        handle = document.createElement('div');
        handle.className = 'panel-resize-handle';
        handle.setAttribute('aria-hidden', 'true');
        panel.appendChild(handle);
      }
      handle.addEventListener('pointerdown', event => {
        if (event.button !== 0) return;
        event.preventDefault();
        event.stopPropagation();
        resizeState = {
          panel,
          startClientX: event.clientX,
          startClientY: event.clientY,
          startWidth: panel.offsetWidth,
          startHeight: panel.offsetHeight,
          minWidth: Math.max(240, panel.scrollWidth ? Math.min(panel.scrollWidth, 320) : 240),
          minHeight: Math.max(160, panel.querySelector('.panel-titlebar') ? panel.querySelector('.panel-titlebar').offsetHeight + 80 : 160)
        };
        panel.classList.add('is-resizing');
        bringPanelToFront(panel);
        if (handle.setPointerCapture) {
          try { handle.setPointerCapture(event.pointerId); } catch (err) {}
        }
        document.addEventListener('pointermove', onPointerMove);
        document.addEventListener('pointerup', endResize);
        document.addEventListener('pointercancel', endResize);
      });
    });
  }

  function initializeDraggablePanels(){
    let dragState = null;

    function onPointerMove(event){
      if (!dragState) return;
      const workspaceScale = getStudioWorkspaceScale();
      const nextX = dragState.startTranslateX + ((event.clientX - dragState.startClientX) / workspaceScale);
      const nextY = dragState.startTranslateY + ((event.clientY - dragState.startClientY) / workspaceScale);
      applyPanelTransform(dragState.panel, nextX, nextY);
    }

    function endDrag(){
      if (!dragState) return;
      dragState.panel.classList.remove('is-dragging');
      persistPanelPosition(dragState.panel);
      dragState = null;
      document.removeEventListener('pointermove', onPointerMove);
      document.removeEventListener('pointerup', endDrag);
      document.removeEventListener('pointercancel', endDrag);
    }

    panelElements.forEach(panel => {
      const handle = panel.querySelector('.panel-titlebar');
      if (!handle) return;

      panel.dataset.translateX = panel.dataset.translateX || '0';
      panel.dataset.translateY = panel.dataset.translateY || '0';

      handle.addEventListener('pointerdown', event => {
        if (event.button !== 0) return;
        if (event.target.closest('button, a, input, select, textarea')) return;
        if (panel.classList.contains('is-resizing')) return;
        dragState = {
          panel,
          startClientX: event.clientX,
          startClientY: event.clientY,
          startTranslateX: Number(panel.dataset.translateX || 0),
          startTranslateY: Number(panel.dataset.translateY || 0)
        };
        panel.classList.add('is-dragging');
        bringPanelToFront(panel);
        if (handle.setPointerCapture) {
          try { handle.setPointerCapture(event.pointerId); } catch (err) {}
        }
        document.addEventListener('pointermove', onPointerMove);
        document.addEventListener('pointerup', endDrag);
        document.addEventListener('pointercancel', endDrag);
      });

      handle.addEventListener('dblclick', event => {
        if (event.target.closest('button, a')) return;
        resetPanelTransform(panel);
        persistPanelPosition(panel);
      });
    });
  }

  async function removeSelectedCategoryTracks(){
    const trackIds = Array.from(selectedTrackIds);
    if (!trackIds.length || !selectedCategoryId) return;

    const trackLabel = trackIds.length === 1 ? 'selected track' : `${trackIds.length} selected tracks`;
    const confirmed = await confirmCategoryTracksDeleteModal({
      title: 'Delete Category Tracks',
      body: `Are you sure you want to delete ${trackLabel} from this category?`,
      yesText: 'Delete',
      noText: 'Cancel'
    });
    if (!confirmed) return;

    try {
      await jsonFetch(`/api/library/category/${encodeURIComponent(String(selectedCategoryId))}/tracks/delete`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({track_ids: trackIds})
      });
      selectedTrackIds.clear();
      lastSelectedTrackId = null;
      await loadTracks();
    } catch (err) {
      console.error('Failed to delete category tracks', err);
    }
  }

  function initializeTrackToolbar(){
    document.querySelectorAll('[data-track-action]').forEach(btn => {
      btn.addEventListener('click', event => {
        const action = btn.dataset.trackAction;
        if (action === 'add') {
          event.preventDefault();
          event.stopPropagation();
          if (tracksAddMenuOpen) {
            closeTracksAddMenu();
            return;
          }
          const rect = btn.getBoundingClientRect();
          openTracksAddMenu(rect.left, rect.bottom + 4);
        } else if (action === 'refresh') {
          loadTracks();
        } else if (action === 'remove') {
          removeSelectedCategoryTracks();
        } else if (action === 'search') {
          event.preventDefault();
          event.stopPropagation();
          openStudioSearch('tracks');
        }
      });
    });
  }


  function ensureStudioSearchModal(){
    return !!(els.studioSearchWindow && els.studioSearchInput && els.studioSearchResults);
  }

  function isDashboardView(){
    const path = String(window.location && window.location.pathname ? window.location.pathname : '').replace(/\/+$/, '');
    return path === '/dashboard' || path.startsWith('/dashboard/') || shell.classList.contains('studio-dashboard-shell');
  }

  function isEditableSearchTarget(target){
    const element = target instanceof Element ? target : null;
    if (!element) return false;
    return !!element.closest('input, textarea, select, [contenteditable=""], [contenteditable="true"], [role="textbox"], [role="combobox"]');
  }

  function isVisibleStudioOverlay(element){
    if (!element || element.hidden) return false;
    if (element.closest('[aria-hidden="true"], [hidden]')) return false;
    const style = window.getComputedStyle(element);
    if (style.display === 'none' || style.visibility === 'hidden') return false;
    return element.getClientRects().length > 0;
  }

  function hasOpenStudioWindow(){
    return Array.from(document.querySelectorAll('.studio-floating-window, .modal-backdrop')).some(isVisibleStudioOverlay);
  }

  function updateStudioSearchActions(){
    if (!els.studioSearchAdd) return;
    const selectedCount = selectedStudioSearchIds.size;
    els.studioSearchAdd.textContent = `Add selected to queue (${selectedCount})`;
    els.studioSearchAdd.disabled = studioSearchAddInFlight || selectedCount === 0;
  }

  function getSelectedStudioSearchIdsInResultOrder(){
    return studioSearchResultsData
      .map(item => String(item && item.id != null ? item.id : ''))
      .filter(trackId => trackId && selectedStudioSearchIds.has(trackId));
  }

  async function addSelectedStudioSearchTracks(){
    if (studioSearchAddInFlight) return;
    const trackIds = getSelectedStudioSearchIdsInResultOrder();
    if (!trackIds.length) return;
    studioSearchAddInFlight = true;
    updateStudioSearchActions();
    try {
      await addTracksToQueue(trackIds);
      selectedStudioSearchIds.clear();
      lastSelectedStudioSearchId = null;
      syncStudioSearchSelection();
    } catch (err) {
      console.error('Failed to add selected search results to queue', err);
    } finally {
      studioSearchAddInFlight = false;
      updateStudioSearchActions();
    }
  }

  function syncStudioSearchSelection(){
    if (!els.studioSearchResults) return;
    els.studioSearchResults.querySelectorAll('[data-search-track-id]').forEach(row => {
      const rowId = String(row.dataset.searchTrackId || '');
      row.classList.toggle('is-selected', !!rowId && selectedStudioSearchIds.has(rowId));
    });
    updateStudioSearchActions();
  }

  function openStudioSearch(context = 'queue', initialQuery = ''){
    if (!ensureStudioSearchModal()) return;
    studioSearchContext = context === 'tracks' ? 'tracks' : 'queue';
    studioSearchResultsData = [];
    selectedStudioSearchIds.clear();
    lastSelectedStudioSearchId = null;
    studioSearchAddInFlight = false;
    updateStudioSearchActions();
    openFloatingWindow(els.studioSearchWindow);
    if (els.studioSearchResults) els.studioSearchResults.innerHTML = '';
    if (els.studioSearchInput) {
      els.studioSearchInput.value = String(initialQuery || '');
      try { els.studioSearchInput.focus({preventScroll: true}); } catch (_err) { els.studioSearchInput.focus(); }
      const valueLength = els.studioSearchInput.value.length;
      try { els.studioSearchInput.setSelectionRange(valueLength, valueLength); } catch (_err) {}
      if (els.studioSearchInput.value) {
        els.studioSearchInput.dispatchEvent(new Event('input', {bubbles: true}));
      }
    }
  }

  function closeStudioSearch(){
    if (!els.studioSearchWindow) return;
    studioSearchResultsData = [];
    selectedStudioSearchIds.clear();
    lastSelectedStudioSearchId = null;
    studioSearchAddInFlight = false;
    updateStudioSearchActions();
    closeFloatingWindow(els.studioSearchWindow);
  }

  function renderStudioSearchResults(emptyText = 'No results.'){
    if (!els.studioSearchResults) return;
    if (!studioSearchResultsData.length) {
      selectedStudioSearchIds.clear();
      lastSelectedStudioSearchId = null;
      updateStudioSearchActions();
      els.studioSearchResults.innerHTML = `<div class="queue-table__empty">${escapeHtml(emptyText)}</div>`;
      return;
    }

    const validIds = new Set(studioSearchResultsData.map(item => String(item && item.id != null ? item.id : '')).filter(Boolean));
    Array.from(selectedStudioSearchIds).forEach(id => {
      if (!validIds.has(String(id))) selectedStudioSearchIds.delete(String(id));
    });

    const ordered = studioSearchResultsData.slice();
    const resultIdsInOrder = ordered
      .map(item => String(item && item.id != null ? item.id : ''))
      .filter(Boolean);

    els.studioSearchResults.innerHTML = ordered.map((t, idx) => {
      const rowId = String(t && t.id != null ? t.id : '');
      const selectedClass = selectedStudioSearchIds.has(rowId) ? ' is-selected' : '';
      const artist = String(t.artist || '').trim();
      const title = String(t.title || '').trim();
      const album = String(t.album || '').trim();
      const displayTitle = title || String(t.filename || '') || String(t.path || '') || 'Untitled';
      const titleText = artist ? `${artist} - ${displayTitle}` : displayTitle;
      const durationValue = Number(t.cue_duration_seconds);
      const durationText = Number.isFinite(durationValue) && durationValue > 0 ? formatSeconds(durationValue) : '--:--';
      return `
        <button class="queue-table__row${selectedClass}" type="button" data-search-track-id="${escapeHtml(rowId)}" title="${escapeHtml(album || titleText)}">
          <div class="queue-table__eta">${idx + 1}</div>
          <div class="queue-table__title">${escapeHtml(titleText)}</div>
          <div class="queue-table__duration">${durationText}</div>
        </button>
      `;
    }).join('');

    els.studioSearchResults.querySelectorAll('[data-search-track-id]').forEach(row => {
      row.addEventListener('click', event => {
        const rowId = String(row.dataset.searchTrackId || '');
        if (!rowId) return;
        if (event.shiftKey && resultIdsInOrder.length) {
          const anchorId = lastSelectedStudioSearchId && resultIdsInOrder.includes(lastSelectedStudioSearchId)
            ? lastSelectedStudioSearchId
            : (selectedStudioSearchIds.size ? Array.from(selectedStudioSearchIds).find(id => resultIdsInOrder.includes(id)) : null);
          if (anchorId) {
            const startIndex = resultIdsInOrder.indexOf(anchorId);
            const endIndex = resultIdsInOrder.indexOf(rowId);
            if (startIndex !== -1 && endIndex !== -1) {
              const fromIndex = Math.min(startIndex, endIndex);
              const toIndex = Math.max(startIndex, endIndex);
              selectedStudioSearchIds.clear();
              for (let i = fromIndex; i <= toIndex; i += 1) selectedStudioSearchIds.add(resultIdsInOrder[i]);
            } else {
              selectedStudioSearchIds.clear();
              selectedStudioSearchIds.add(rowId);
            }
          } else {
            selectedStudioSearchIds.clear();
            selectedStudioSearchIds.add(rowId);
          }
        } else if (event.ctrlKey || event.metaKey) {
          if (selectedStudioSearchIds.has(rowId)) selectedStudioSearchIds.delete(rowId);
          else selectedStudioSearchIds.add(rowId);
        } else {
          const singleSelected = selectedStudioSearchIds.size === 1 && selectedStudioSearchIds.has(rowId);
          selectedStudioSearchIds.clear();
          if (!singleSelected) selectedStudioSearchIds.add(rowId);
        }
        lastSelectedStudioSearchId = rowId;
        syncStudioSearchSelection();
      });

      row.addEventListener('dblclick', event => {
        const rowId = String(row.dataset.searchTrackId || '');
        if (!rowId) return;
        event.preventDefault();
        event.stopPropagation();
        selectedStudioSearchIds.clear();
        selectedStudioSearchIds.add(rowId);
        lastSelectedStudioSearchId = rowId;
        syncStudioSearchSelection();
        addTracksToQueue([rowId]).catch(err => {
          console.error('Failed to add search result to queue', err);
        });
      });
    });

    updateStudioSearchActions();
  }

  async function runStudioSearch(q){
    if (!ensureStudioSearchModal() || !els.studioSearchResults) return;
    if (studioSearchController) studioSearchController.abort();
    studioSearchController = new AbortController();
    const params = new URLSearchParams();
    const stationKey = getActiveStationKey();
    if (stationKey) params.set('station_key', String(stationKey));
    params.set('q', q);
    if (studioSearchContext === 'tracks' && selectedCategoryId) params.set('category_id', String(selectedCategoryId));
    const resp = await fetch('/api/search_tracks?' + params.toString(), {signal: studioSearchController.signal});
    const data = await resp.json();
    els.studioSearchResults.innerHTML = '';
    studioSearchResultsData = Array.isArray(data) ? data.slice() : [];
    if (!studioSearchResultsData.length) {
      renderStudioSearchResults('No results.');
      return;
    }
    renderStudioSearchResults();
  }

  function initializeStudioSearch(){
    if (!ensureStudioSearchModal()) return;
    const win = els.studioSearchWindow;
    const titlebar = win ? win.querySelector('.studio-floating-window__titlebar') : null;
    const handle = win ? win.querySelector('.panel-resize-handle') : null;
    let dragState = null;
    let resizeState = null;

    function onPointerMove(event){
      if (dragState){
        applyFloatingWindowRect(
          win,
          dragState.startLeft + (event.clientX - dragState.startClientX),
          dragState.startTop + (event.clientY - dragState.startClientY),
          dragState.width,
          dragState.height
        );
      } else if (resizeState){
        applyFloatingWindowRect(
          win,
          resizeState.left,
          resizeState.top,
          resizeState.startWidth + (event.clientX - resizeState.startClientX),
          resizeState.startHeight + (event.clientY - resizeState.startClientY)
        );
      }
    }

    function endPointerInteraction(){
      dragState = null;
      resizeState = null;
      if (win){
        win.classList.remove('is-dragging');
        win.classList.remove('is-resizing');
      }
      document.removeEventListener('pointermove', onPointerMove);
      document.removeEventListener('pointerup', endPointerInteraction);
      document.removeEventListener('pointercancel', endPointerInteraction);
    }

    if (titlebar){
      titlebar.addEventListener('pointerdown', event => {
        if (event.button !== 0) return;
        if (event.target.closest('button, a, input, select, textarea')) return;
        event.preventDefault();
        bringWindowToFront(win);
        dragState = {
          startClientX: event.clientX,
          startClientY: event.clientY,
          startLeft: Number.parseInt(win.style.left || '80', 10),
          startTop: Number.parseInt(win.style.top || '96', 10),
          width: win.offsetWidth,
          height: win.offsetHeight
        };
        win.classList.add('is-dragging');
        document.addEventListener('pointermove', onPointerMove);
        document.addEventListener('pointerup', endPointerInteraction);
        document.addEventListener('pointercancel', endPointerInteraction);
      });
    }

    if (handle){
      handle.addEventListener('pointerdown', event => {
        if (event.button !== 0) return;
        event.preventDefault();
        event.stopPropagation();
        bringWindowToFront(win);
        resizeState = {
          startClientX: event.clientX,
          startClientY: event.clientY,
          left: Number.parseInt(win.style.left || '80', 10),
          top: Number.parseInt(win.style.top || '96', 10),
          startWidth: win.offsetWidth,
          startHeight: win.offsetHeight
        };
        win.classList.add('is-resizing');
        document.addEventListener('pointermove', onPointerMove);
        document.addEventListener('pointerup', endPointerInteraction);
        document.addEventListener('pointercancel', endPointerInteraction);
      });
    }

    if (els.studioSearchClose) els.studioSearchClose.addEventListener('click', closeStudioSearch);
    if (els.studioSearchFooterClose) els.studioSearchFooterClose.addEventListener('click', closeStudioSearch);
    if (els.studioSearchAdd) els.studioSearchAdd.addEventListener('click', addSelectedStudioSearchTracks);
    if (win){
      win.addEventListener('pointerdown', () => bringWindowToFront(win));
      window.addEventListener('resize', () => {
        if (win.getAttribute('aria-hidden') === 'true') return;
        applyFloatingWindowRect(
          win,
          Number.parseInt(win.style.left || '80', 10),
          Number.parseInt(win.style.top || '96', 10),
          win.offsetWidth,
          win.offsetHeight
        );
      });
    }
    if (els.studioSearchInput) {
      els.studioSearchInput.addEventListener('input', () => {
        const q = (els.studioSearchInput.value || '').trim();
        if (q.length < 3) {
          if (studioSearchTimer) clearTimeout(studioSearchTimer);
          studioSearchResultsData = [];
          selectedStudioSearchIds.clear();
          lastSelectedStudioSearchId = null;
          updateStudioSearchActions();
          if (els.studioSearchResults) els.studioSearchResults.innerHTML = '';
          return;
        }
        if (studioSearchTimer) clearTimeout(studioSearchTimer);
        studioSearchTimer = setTimeout(() => {
          runStudioSearch(q).catch(() => {});
        }, 250);
      });
    }
    document.addEventListener('keydown', event => {
      if (event.key !== 'Escape' || !win) return;
      if (win.getAttribute('aria-hidden') === 'true' || win.style.display === 'none') return;
      event.preventDefault();
      event.stopPropagation();
      closeStudioSearch();
    }, true);

    document.addEventListener('keydown', event => {
      if (event.defaultPrevented || event.isComposing || event.ctrlKey || event.altKey || event.metaKey) return;
      if (event.key.length !== 1 || !event.key.trim()) return;
      if (isDashboardView() || hasOpenStudioWindow()) return;
      const activeTarget = event.target instanceof Element ? event.target : document.activeElement;
      if (isEditableSearchTarget(activeTarget)) return;
      event.preventDefault();
      event.stopPropagation();
      openStudioSearch('queue', event.key);
    }, true);
  }


  function initializeClockFace(){
    if (!els.clockMajorTicks || !els.clockMinorTicks) return;
    const svgNamespace = 'http://www.w3.org/2000/svg';
    const createTick = (angle, outerRadius, innerRadius, target) => {
      const line = document.createElementNS(svgNamespace, 'line');
      line.setAttribute('x1', '50');
      line.setAttribute('y1', String(50 - outerRadius));
      line.setAttribute('x2', '50');
      line.setAttribute('y2', String(50 - innerRadius));
      line.setAttribute('transform', `rotate(${angle} 50 50)`);
      target.appendChild(line);
    };

    els.clockMajorTicks.innerHTML = '';
    els.clockMinorTicks.innerHTML = '';
    for (let index = 0; index < 60; index += 1) {
      const angle = index * 6;
      if (index % 5 === 0) {
        createTick(angle, 40, 34, els.clockMajorTicks);
      } else {
        createTick(angle, 38, 35, els.clockMinorTicks);
      }
    }
  }

  function updateClock(){
    const now = new Date();
    const seconds = now.getSeconds();
    const minutes = now.getMinutes() + (seconds / 60);
    const hours = (now.getHours() % 12) + (minutes / 60);
    if (els.clockHourHand) els.clockHourHand.setAttribute('transform', `rotate(${hours * 30} 50 50)`);
    if (els.clockMinuteHand) els.clockMinuteHand.setAttribute('transform', `rotate(${minutes * 6} 50 50)`);
    if (els.clockSecondHand) els.clockSecondHand.setAttribute('transform', `rotate(${seconds * 6} 50 50)`);
    if (els.clockDate) {
      const yyyy = String(now.getFullYear());
      const mm = String(now.getMonth() + 1).padStart(2, '0');
      const dd = String(now.getDate()).padStart(2, '0');
      els.clockDate.textContent = `${yyyy}.${mm}.${dd}.`;
    }
  }

  document.querySelectorAll('[data-control]').forEach(btn => {
    btn.addEventListener('click', () => sendControl(btn.dataset.control));
  });
  document.querySelectorAll('.layout-btn').forEach(btn => {
    btn.addEventListener('click', () => saveLayout(btn.dataset.layoutTarget));
  });

  if (els.playlistRootToggle && els.playlistCategories) {
    els.playlistRootToggle.addEventListener('click', () => {
      const expanded = els.playlistRootToggle.getAttribute('aria-expanded') !== 'false';
      els.playlistRootToggle.setAttribute('aria-expanded', expanded ? 'false' : 'true');
      els.playlistRootToggle.classList.toggle('is-expanded', !expanded);
      els.playlistCategories.style.display = expanded ? 'none' : '';
    });
  }
  document.querySelectorAll('[data-refresh="queue"]').forEach(btn => btn.addEventListener('click', loadQueue));

  async function initializeStudio(){
    initializeResizablePanels();
    initializeDraggablePanels();
    updateStudioWorkspaceScale();
    initializeLayoutContextMenu();
    initializePlaylistSplitter();
    initializePlaylistContextMenu();
    initializeTracksAddMenu();
    initializeScriptsAddMenu();
    initializeScriptsAddScriptWindow();
    initializeScriptsConfigWindow();
    initializeFloatingPlaylistBrowserWindow();
    initializePlaylistAddUrlWindow();
    initializePlaylistMenuActions();
    initializeTrackToolbar();
    initializeDeckOnAirButton();
    initializeDeckProgressSeek();
    initializeStudioSearch();
    initializeStudioSettings();
    initializeStudioUsersAddWindow();
    initializeStudioUsersDeleteWindow();
    initializeStudioUsersPasswordWindow();
    initializeSimpleFloatingWindow(els.schedulerRuleDeleteWindow);
    initializeSimpleFloatingWindow(els.scriptsDeleteBackdrop);
    initializeSimpleFloatingWindow(els.encodersDeleteWindow);
    initializeSimpleFloatingWindow(els.studioStopConfirmBackdrop);
    initializeSimpleFloatingWindow(els.queueDeleteWindow);
    initializeSimpleFloatingWindow(els.playlistDeleteWindow);
    initializeSimpleFloatingWindow(els.categoryTracksDeleteWindow);
    initializeSimpleFloatingWindow(els.studioStationRenameBackdrop);
    initializeSimpleFloatingWindow(els.studioStationDeleteConfirmBackdrop);
    initializeSimpleFloatingWindow(els.studioStationDeletePasswordBackdrop);
    initializeSimpleFloatingWindow(els.playlistPromptBackdrop);
    initializeStudioAutodjCategoryWindow();
    initializeStudioAutodjLoadWindow();
    initializeStudioAutodjSaveWindow();
    initializeStudioAddStationModal();
    initializeClockFace();
    await loadLayoutStateFromServer();
    initializeQueueToolbar();
    saveLayout(preferredLayoutName || 'layout-1');
    updateClock();
    loadStatus();
    loadQueue();
    loadHistory();
    loadCategories();
    loadEncoders();
    loadScheduler();
    initializeStudioUiEvents();

    window.addEventListener('resize', () => {
      requestAnimationFrame(() => {
        updateStudioWorkspaceScale();
      });
    });

    const toastCloseButton = document.getElementById('autodj-toast-close');
    if (toastCloseButton && !toastCloseButton.__studioBound){
      toastCloseButton.__studioBound = true;
      toastCloseButton.addEventListener('click', hideStudioAutodjToast);
    }

    setInterval(updateClock, 1000);
    setInterval(updateDeckProgressLocalClock, STUDIO_PROGRESS_UI_TICK_MS);
    setInterval(updateQueueEtaLocalClock, 1000);
    setInterval(loadStatus, STUDIO_STATUS_POLL_MS);
    setInterval(syncDeckOnAirButton, STUDIO_ON_AIR_SYNC_MS);
    setInterval(loadQueue, STUDIO_QUEUE_POLL_MS);
    setInterval(loadHistory, STUDIO_HISTORY_POLL_MS);
    setInterval(updateEncoderElapsedDisplays, 1000);
    setInterval(loadEncoders, STUDIO_ENCODERS_POLL_MS);
    window.addEventListener('scheduler:rules-changed', () => { loadScheduler(); });
    // v2794: countdown text in the Scripts/Scheduler window is rendered locally
    // from next_run_at. Avoid polling /api/studio/scripts every second just to
    // change the ETA string while the browser is open.
    setInterval(renderStudioScriptsList, 1000);
    setInterval(loadScheduler, 15000);
  }

  initializeStudio();
})();
