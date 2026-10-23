'use strict';
const $ = id => document.getElementById(id);
const modes = {normal:'Обычный', diagnostic:'Диагностика', quiet:'Тихий'};
let state = null;
let csrf = '';
let dirty = false;
let saving = false;
let startupBusy = false;
let loading = false;
let online = false;
let availableUpdateSha = null;
let preparingUpdate = false;
const mobile = matchMedia('(max-width: 950px)');

function toast(message, error = false) {
  const item = document.createElement('div');
  item.className = 'toast' + (error ? ' error' : '');
  if (error) item.setAttribute('role', 'alert');
  const icon = document.createElementNS('http://www.w3.org/2000/svg','svg');
  const use = document.createElementNS('http://www.w3.org/2000/svg','use');
  use.setAttribute('href', error ? '#i-close' : '#i-check'); icon.append(use);
  const text = document.createElement('span'); text.textContent = message;
  item.append(icon,text); $('toasts').append(item);
  setTimeout(() => item.remove(), error ? 10000 : 5500);
}

async function api(path, payload) {
  const options = {cache:'no-store', signal:AbortSignal.timeout(path === '/api/update/prepare' ? 120000 : 16000)};
  if (payload !== undefined) {
    options.method = 'POST';
    options.headers = {'Content-Type':'application/json','X-Sakuya-CSRF':csrf};
    options.body = JSON.stringify(payload);
  }
  const response = await fetch(path, options);
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || 'Не удалось выполнить запрос.');
  return data;
}

function setConnection(connected) {
  online = connected;
  const connection = $('connection');
  connection.classList.toggle('offline', !connected);
  connection.querySelector('span').textContent = connected ? 'Sakuya работает' : 'Нет связи';
  connection.querySelector('use').setAttribute('href',connected ? '#i-check' : '#i-close');
  $('connection-error').hidden = connected;
  $('open-data').disabled = !connected || !state?.capabilities.open_folder;
  $('show-log').disabled = !connected;
  $('tools-log').disabled = !connected;
  $('open-documents').disabled = !connected || !state?.capabilities.open_documents;
  $('port').disabled = !connected || saving;
  $('autostart-browser').disabled = !connected || saving;
  $('startup-enabled').disabled = !connected || startupBusy || !state?.startup.supported;
  updateSave();
}

function uptime(seconds) {
  const hours = Math.floor(seconds / 3600), minutes = Math.floor(seconds % 3600 / 60);
  if (hours) return `${hours} ч ${minutes} мин`;
  if (minutes) return `${minutes} мин ${seconds % 60} с`;
  return `${seconds} с`;
}

function render(current, initial = false) {
  state = current;
  document.querySelectorAll('[data-bind="version"]').forEach(node => node.textContent = current.version);
  $('python-version').textContent = 'Python ' + current.python_version;
  $('uptime').textContent = uptime(current.uptime_seconds);
  $('mode-note').textContent = ({normal:'Обычный режим',diagnostic:'Режим диагностики',quiet:'Тихий режим'}[current.mode] || current.mode);
  $('tray-status').textContent = {active:'Трей активен', starting:'Запускается', unavailable:'Через консоль'}[current.tray] || 'Проверка…';
  $('tray-note').textContent = current.tray === 'active' ? 'Значок рядом с часами' : current.tray === 'starting' ? 'Ожидание готовности' : 'Для выхода — Ctrl+C в консоли';
  $('data-path').textContent = current.data_directory; $('data-path').title = current.data_directory;
  $('folder-hint').textContent = current.capabilities.open_folder ? '' : 'Папку можно открыть автоматически при запуске Sakuya в Windows.';
  $('server-address').textContent = current.url.replace('http://127.0.0.1:', 'Порт ');
  $('server-pid').textContent = String(current.pid);
  $('update-ready').textContent = current.update?.automatic_install ? 'Доступны' : 'Вручную';
  $('about-python').textContent = current.python_version;
  $('about-mode').textContent = modes[current.mode] || current.mode;
  $('about-pid').textContent = current.pid;
  $('about-address').textContent = current.url.replace('http://','');
  $('about-address').title = current.url;
  if (initial || !dirty) renderSettings();
  if (!startupBusy) $('startup-enabled').checked = current.startup.enabled;
  $('startup-help').textContent = !current.startup.supported ? 'Эта настройка доступна при запуске Sakuya в Windows.' : current.startup.enabled && !current.startup.matches ? 'Путь программы изменился. Выключите и снова включите автозапуск, чтобы обновить его.' : 'Sakuya появится в трее после входа в Windows.';
  $('open-log-file').hidden = !current.capabilities.open_log;
  setConnection(true);
}

function renderSettings() {
  if (!state) return;
  $('port').value = state.settings.port;
  $('autostart-browser').checked = state.settings.autostart_browser;
  dirty = false; $('form-error').hidden = true; updateSave();
}

function updateSave() {
  $('save-settings').disabled = !dirty || saving || !online;
  $('reset-settings').disabled = !dirty || saving || !online;
  $('save-note').textContent = saving ? 'Сохранение…' : dirty ? 'Есть несохранённые изменения' : state ? 'Все изменения сохранены' : 'Настройки ещё не загружены';
}

async function refresh(showError = false) {
  if (loading || saving || startupBusy) return;
  loading = true; $('refresh').disabled = true; $('retry').disabled = true;
  try {
    const first = !csrf;
    let current = await api(first ? '/api/bootstrap' : '/api/status');
    if (first || current.session !== state?.session) {
      if (!first) current = await api('/api/bootstrap');
      csrf = current.csrf;
    }
    if (!saving && !startupBusy) render(current, first);
  } catch (error) {
    setConnection(false);
    if (showError) toast('Не удалось связаться с Sakuya. Проверьте запуск программы и нажмите «Повторить».',true);
  } finally {
    loading = false; $('refresh').disabled = false; $('retry').disabled = false;
  }
}

function syncMenu() {
  const expanded = mobile.matches ? document.body.classList.contains('mobile-menu-open') : !document.body.classList.contains('sidebar-collapsed');
  const label = mobile.matches ? (expanded ? 'Закрыть меню' : 'Открыть меню') : (expanded ? 'Свернуть меню' : 'Развернуть меню');
  $('menu-toggle').setAttribute('aria-expanded',String(expanded));
  $('menu-toggle').setAttribute('aria-label',label); $('menu-toggle').title = label;
  $('sidebar').inert = mobile.matches && !expanded;
}

function closeDrawer() {
  document.body.classList.remove('mobile-menu-open'); $('drawer-overlay').hidden = true;
  syncMenu();
}

function route() {
  const page = ['home','settings','tools','updates'].includes(location.hash.slice(1)) ? location.hash.slice(1) : 'home';
  const names = {home:'Главная',settings:'Настройки',tools:'Инструменты',updates:'Обновления'};
  for (const key of Object.keys(names)) {
    $('page-' + key).hidden = key !== page;
    $('nav-' + key).classList.toggle('active', key === page);
    $('nav-' + key).setAttribute('aria-current', key === page ? 'page' : 'false');
  }
  $('breadcrumb-page').textContent = names[page];
  document.title = 'Sakuya — ' + names[page];
  closeDrawer();
  if (page === 'updates') checkUpdates(false);
  window.scrollTo({top:0,behavior:'instant'});
}
async function checkUpdates(remote = false) {
  $('check-updates').disabled = true;
  $('prepare-update').disabled = true;
  availableUpdateSha = null;
  $('update-status').textContent = remote ? 'Проверка GitHub…' : 'Проверка возможностей…';
  try {
    const response = await api(remote ? '/api/update/check' : '/api/update/status');
    if (remote && response.reachable) {
      availableUpdateSha = response.commit;
      $('update-status').textContent = 'GitHub: коммит ' + response.commit.slice(0,12);
      $('prepare-update').disabled = preparingUpdate || !online;
    } else {
      $('update-status').textContent = remote ? response.reason : response.message;
    }
  } catch (error) { $('update-status').textContent = 'Проверка не удалась: ' + error.message; }
  finally { $('check-updates').disabled = false; }
}

try { document.body.classList.toggle('sidebar-collapsed',localStorage.getItem('sakuya.menu.collapsed') === 'true'); } catch {}
$('menu-toggle').addEventListener('click', () => {
  if (mobile.matches) {
    const open = document.body.classList.toggle('mobile-menu-open');
    $('drawer-overlay').hidden = !open; $('menu-toggle').setAttribute('aria-expanded',String(open));
    if (open) $('nav-home').focus();
  } else {
    const collapsed = document.body.classList.toggle('sidebar-collapsed');
    $('menu-toggle').setAttribute('aria-expanded',String(!collapsed));
    $('menu-toggle').setAttribute('aria-label',collapsed ? 'Развернуть меню' : 'Свернуть меню');
    $('menu-toggle').title = collapsed ? 'Развернуть меню' : 'Свернуть меню';
    try { localStorage.setItem('sakuya.menu.collapsed',String(collapsed)); } catch {}
  }
  syncMenu();
});
$('drawer-overlay').addEventListener('click',closeDrawer);
mobile.addEventListener('change',closeDrawer);
window.addEventListener('hashchange',route);
window.addEventListener('keydown',event => { if (event.key === 'Escape') closeDrawer(); });
$('sidebar').querySelectorAll('a').forEach(link => link.addEventListener('click',closeDrawer));
$('refresh').addEventListener('click',() => refresh(true));
$('retry').addEventListener('click',() => refresh(true));

function changed() {
  if (!state) return;
  dirty = Number($('port').value) !== state.settings.port || $('autostart-browser').checked !== state.settings.autostart_browser;
  $('form-error').hidden = true; updateSave();
}
$('port').addEventListener('input',changed);
$('autostart-browser').addEventListener('change',changed);
$('reset-settings').addEventListener('click',renderSettings);
$('settings-form').addEventListener('submit',async event => {
  event.preventDefault(); if (saving || !state || !online || !$('settings-form').reportValidity()) return;
  const payload = {port:Number($('port').value),autostart_browser:$('autostart-browser').checked};
  const portChanged = payload.port !== state.settings.port;
  saving = true; $('form-error').hidden = true; setConnection(online);
  try {
    const response = await api('/api/settings',payload);
    state.settings = response.settings; renderSettings();
    toast(portChanged ? 'Настройки сохранены. Новый порт будет выбран при следующем запуске Sakuya.' : 'Настройки сохранены.');
  } catch (error) {
    $('form-error').textContent = error.message; $('form-error').hidden = false;
  } finally { saving = false; setConnection(online); }
});
$('startup-enabled').addEventListener('change',async () => {
  const enabled = $('startup-enabled').checked;
  startupBusy = true; $('startup-enabled').disabled = true;
  try {
    const response = await api('/api/startup',{enabled}); state.startup = response.startup;
    toast(enabled ? 'Автозапуск Windows включён.' : 'Автозапуск Windows выключен.');
  } catch (error) { $('startup-enabled').checked = !enabled; toast(error.message,true); }
  finally { startupBusy = false; render(state); }
});
async function openTarget(target,button) {
  button.disabled = true;
  try { await api('/api/open',{target}); toast(target === 'data' ? 'Папка данных открыта.' : target === 'documents' ? 'Папка документов открыта.' : 'Журнал открыт.'); }
  catch (error) { toast(error.message,true); }
  finally { button.disabled = !online || !(target === 'data' ? state?.capabilities.open_folder : target === 'documents' ? state?.capabilities.open_documents : state?.capabilities.open_log); }
}
$('open-documents').addEventListener('click',() => openTarget('documents',$('open-documents')));
$('tools-log').addEventListener('click',() => $('show-log').click());
$('check-updates').addEventListener('click',() => checkUpdates(true));
$('prepare-update').addEventListener('click', async () => {
  if (!availableUpdateSha || preparingUpdate || !online) return;
  if (!confirm('Подготовить проверенный релиз? Для применения потребуется завершить Sakuya через трей и открыть Sakuya.bat ещё раз.')) return;
  preparingUpdate = true;
  $('prepare-update').disabled = true;
  $('check-updates').disabled = true;
  $('update-instructions').textContent = 'Загрузка и проверка файлов…';
  try {
    const result = await api('/api/update/prepare', {commit: availableUpdateSha});
    if (result.prepared) {
      $('update-instructions').textContent = 'Релиз подготовлен. Закройте Sakuya через значок трея и запустите Sakuya.bat повторно. При сбое пробного запуска повторное открытие BAT восстановит предыдущую версию.';
      toast('Проверенный релиз подготовлен к следующему запуску.');
    }
  } catch (error) { $('update-instructions').textContent = error.message; toast(error.message, true); }
  finally { preparingUpdate = false; $('check-updates').disabled = false; }
});
$('open-data').addEventListener('click',() => openTarget('data',$('open-data')));
$('open-log-file').addEventListener('click',() => openTarget('log',$('open-log-file')));
$('show-log').addEventListener('click',async () => {
  $('log-content').textContent = 'Загрузка…'; $('log-path').textContent = '';
  $('log-dialog').showModal();
  try {
    const result = await api('/api/logs');
    $('log-content').textContent = result.lines.length ? result.lines.join('\n') : 'В журнале пока нет записей.';
    $('log-path').textContent = result.path;
  } catch (error) { $('log-content').textContent = error.message; }
});
$('close-log').addEventListener('click',() => $('log-dialog').close());
$('log-dialog').addEventListener('click',event => { if (event.target === $('log-dialog')) { const r=event.target.getBoundingClientRect(); if (event.clientX<r.left || event.clientX>r.right || event.clientY<r.top || event.clientY>r.bottom) event.target.close(); } });
document.addEventListener('visibilitychange',() => { if (!document.hidden) refresh(); });
route(); refresh();
setInterval(() => { if (!document.hidden) refresh(); },5000);
