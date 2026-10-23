'use strict';
const $ = id => document.getElementById(id);
const modes = {normal:'Обычный', diagnostic:'Диагностика', quiet:'Тихий'};
let state = null;
let chatHistory = [];
let chatBusy = false;
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
  const options = {cache:'no-store', signal:AbortSignal.timeout(path === '/api/update/prepare' ? 120000 : path === '/api/chat' ? 330000 : 16000)};
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
  $('chat-send').disabled = !connected || chatBusy;
  $('cloudru-save').disabled = !connected;
  $('cloudru-test').disabled = !connected;
  $('prepare-update').disabled = !connected || preparingUpdate || !availableUpdateSha;
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
  const page = ['home','settings','chat','memory','monitor','tools','updates'].includes(location.hash.slice(1)) ? location.hash.slice(1) : 'home';
  const names = {home:'Главная',settings:'Настройки',chat:'Чат',memory:'Память',monitor:'Мониторинг',tools:'Инструменты',updates:'Обновления'};
  for (const key of Object.keys(names)) {
    $('page-' + key).hidden = key !== page;
    $('nav-' + key).classList.toggle('active', key === page);
    $('nav-' + key).setAttribute('aria-current', key === page ? 'page' : 'false');
  }
  $('breadcrumb-page').textContent = names[page];
  document.title = 'Sakuya — ' + names[page];
  closeDrawer();
  if (page === 'updates') checkUpdates(false);
  if (page === 'memory') loadMemories();
  if (page === 'tools') loadDocuments();
  if (page === 'monitor') loadMonitor();
  if (page === 'chat') loadChatList();
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
    if (open) $('sidebar').querySelector('.nav-item.active')?.focus();
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
$('restart-update').addEventListener('click', async () => {
  if (!confirm('Закрыть Sakuya и автоматически запустить проверенную новую версию?')) return;
  $('restart-update').disabled = true;
  try {
    await api('/api/update/restart', {confirm: true});
    $('update-instructions').textContent = 'Sakuya перезапускается. Если браузер потеряет связь, откройте страницу через значок трея.';
  } catch (error) {
    $('restart-update').disabled = false;
    toast(error.message, true);
  }
});
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
      $('restart-update').disabled = false;
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
async function loadCloudru() {
  try {
    const status = await api('/api/cloudru/status');
    $('cloudru-status').textContent = status.connected ? 'API-ключ сохранён локально. Для проверки соединения нажмите кнопку ниже.' : 'API-ключ ещё не добавлен.';
    $('cloudru-model').dataset.saved = status.model || '';
    $('cloudru-test').disabled = !status.connected;
    $('cloudru-delete').disabled = !status.connected;
  } catch (error) { $('cloudru-status').textContent = error.message; }
}
$('cloudru-save').addEventListener('click',async () => {
  const key = $('cloudru-key').value.trim();
  if (!key) return toast('Введите API-ключ Cloud.ru.',true);
  $('cloudru-save').disabled = true;
  try {
    await api('/api/cloudru/key',{key});
    $('cloudru-key').value = '';
    toast('Ключ Cloud.ru защищён и сохранён.');
    await loadCloudru();
  } catch (error) { toast(error.message,true); }
  finally { $('cloudru-save').disabled = false; }
});
$('cloudru-test').addEventListener('click',async () => {
  $('cloudru-test').disabled = true;
  $('cloudru-status').textContent = 'Подключение к Cloud.ru…';
  try {
    const data = await api('/api/cloudru/models');
    const select = $('cloudru-model');
    select.replaceChildren();
    for (const model of data.models) {
      const opt = document.createElement('option');
      opt.value = model; opt.textContent = model; select.append(opt);
    }
    if (data.models.includes(select.dataset.saved)) select.value = select.dataset.saved;
    $('cloudru-model-save').disabled = data.models.length === 0;
    $('cloudru-status').textContent = 'Связь установлена. Доступных моделей: ' + data.models.length;
  } catch (error) { $('cloudru-status').textContent = 'Ошибка соединения: ' + error.message; }
  finally { $('cloudru-test').disabled = false; }
});
$('cloudru-model-save').addEventListener('click',async () => {
  try {
    const result = await api('/api/cloudru/model',{model:$('cloudru-model').value});
    $('cloudru-model').dataset.saved = result.model;
    toast('Модель Cloud.ru сохранена.');
  } catch (error) { toast(error.message,true); }
});
$('cloudru-delete').addEventListener('click',async () => {
  if (!confirm('Удалить сохранённый API-ключ Cloud.ru?')) return;
  try {
    await api('/api/cloudru/delete',{confirm:true});
    $('cloudru-model').replaceChildren();
    $('cloudru-model-save').disabled = true;
    await loadCloudru();
    toast('Ключ Cloud.ru удалён.');
  } catch (error) { toast(error.message,true); }
});
loadCloudru();
let currentChatId = null;
let chatAbort = null;
let activeChatModel = '';
async function loadChatList() {
  try {
    const term = $('chat-search').value.trim();
    const result = await api('/api/workspace/chats');
    const chats = result.chats.filter(item => !term || item.title.toLowerCase().includes(term.toLowerCase()));
    const list = $('chat-list'); list.replaceChildren();
    for (const chat of chats) {
      const button=document.createElement('button');
      button.type='button'; button.className='chat-history-entry'+(chat.id===currentChatId?' selected':'');
      button.textContent=chat.title; button.title=chat.title;
      button.onclick=()=>openChat(chat.id);
      list.append(button);
    }
  } catch(error) { $('chat-status').textContent='История: '+error.message; }
}
async function openChat(id) {
  if(chatBusy) return toast('Сначала завершите генерацию.',true);
  try {
    const result=await api('/api/workspace/chat?id='+encodeURIComponent(id));
    currentChatId=id; chatHistory=result.messages;
    $('chat-messages').replaceChildren();
    for(const item of chatHistory) addChatMessage(item.role,item.content);
    $('chat-status').textContent='Локальная история загружена.';
    await loadChatList();
  } catch(error) { toast(error.message,true); }
}
async function newChat() {
  if(chatBusy) return toast('Сначала завершите генерацию.',true);
  try {
    const created=await api('/api/workspace/create',{title:'Новый диалог'});
    await openChat(created.id);
  } catch(error) { toast(error.message,true); }
}
function addChatMessage(role,content) {
  const item=document.createElement('div');item.className='chat-message '+role;
  const heading=document.createElement('strong');heading.textContent=role==='user'?'Вы':'Sakuya';
  const message=document.createElement('p');message.textContent=content;
  item.append(heading,message);$('chat-messages').append(item);
  $('chat-messages').scrollTop=$('chat-messages').scrollHeight;
  return message;
}
$('chat-new').onclick=newChat;
$('chat-clear').onclick=newChat;
$('chat-search').addEventListener('input',loadChatList);
$('chat-rename').onclick=async()=>{
  if(!currentChatId||chatBusy)return;
  const title=prompt('Новое название диалога:');
  if(!title)return;
  try{await api('/api/workspace/rename',{id:currentChatId,title});await loadChatList();}
  catch(error){toast(error.message,true);}
};
$('chat-delete').onclick=async()=>{
  if(!currentChatId||chatBusy||!confirm('Удалить диалог и всю его историю?'))return;
  try{await api('/api/workspace/delete',{id:currentChatId});currentChatId=null;chatHistory=[];$('chat-messages').replaceChildren();await loadChatList();}
  catch(error){toast(error.message,true);}
};
$('chat-file').addEventListener('change',()=>{
  const file=$('chat-file').files[0];
  $('chat-file-info').textContent=file?file.name+' · '+Math.ceil(file.size/1024)+' КиБ':'Прикрепите текстовый файл до 2 МиБ';
});
async function textAttachment(file) {
  if(file.size>2*1024*1024)throw new Error('Файл превышает 2 МиБ');
  const bytes=new Uint8Array(await file.arrayBuffer());
  let binary='';for(let i=0;i<bytes.length;i+=8192){binary+=String.fromCharCode(...bytes.subarray(i,i+8192));}
  const result=await api('/api/workspace/attachment',{name:file.name,base64:btoa(binary)});
  return result.text;
}
$('chat-reload-models').onclick=async()=>{
  $('chat-reload-models').disabled=true;
  try {
    const [available,saved]=await Promise.all([api('/api/cloudru/models'),api('/api/cloudru/status')]);
    const select=$('chat-select-model');select.replaceChildren();
    for(const name of available.models){const option=document.createElement('option');option.value=name;option.textContent=name;select.append(option);}
    if(available.models.includes(saved.model))select.value=saved.model;
    activeChatModel=select.value;
    $('chat-status').textContent='Моделей доступно: '+available.models.length;
  }catch(error){toast(error.message,true);}
  finally{$('chat-reload-models').disabled=false;}
};
$('chat-select-model').onchange=async()=>{
  try{
    await api('/api/cloudru/model',{model:$('chat-select-model').value});
    activeChatModel=$('chat-select-model').value;
    toast('Модель сохранена.');
  }catch(error){toast(error.message,true);}
};
$('chat-stop').onclick=()=>{if(chatAbort)chatAbort.abort();};
$('chat-form').addEventListener('submit',async event=>{
  event.preventDefault();
  if(chatBusy||!online)return;
  const plain=$('chat-input').value.trim();
  const file=$('chat-file').files[0];
  if(!plain&&!file)return;
  chatBusy=true;$('chat-send').disabled=true;$('chat-stop').disabled=false;
  $('chat-status').textContent='Подготовка запроса…';
  let partial=null, answer='';
  try {
    if(!currentChatId){const created=await api('/api/workspace/create',{title:'Новый диалог'});currentChatId=created.id;}
    let value=plain;
    let image=null;
    if(file){
      const mime=file.type.toLowerCase();
      if(['image/png','image/jpeg','image/webp'].includes(mime)){
        if(file.size>2*1024*1024)throw new Error('Изображение превышает 2 МиБ');
        const bytes=new Uint8Array(await file.arrayBuffer());
        let binary='';
        for(let i=0;i<bytes.length;i+=8192)binary+=String.fromCharCode(...bytes.subarray(i,i+8192));
        image={mime,base64:btoa(binary)};
        value+=(value?'\n\n':'')+'[Изображение: '+file.name+']';
      }else{
        value+=(value?'\n\n':'')+await textAttachment(file);
      }
    }
    const user={role:'user',content:value};
    const history=[...chatHistory,user];
    chatAbort=new AbortController();
    // Use a streaming response so words appear as the provider generates them.
    const response=await fetch('/api/chat/stream',{
      method:'POST',cache:'no-store',signal:chatAbort.signal,
      headers:{'Content-Type':'application/json','X-Sakuya-CSRF':csrf},
      body:JSON.stringify({messages:history,chat_id:currentChatId,model:activeChatModel||undefined,use_memory:$('chat-use-memory').checked,image:image||undefined})
    });
    if(!response.ok){
      const failure=await response.json();throw new Error(failure.error||'Ошибка запроса');
    }
    addChatMessage('user',value);
    partial=addChatMessage('assistant','');
    const reader=response.body.getReader(), decoder=new TextDecoder();
    let buffer='',done=false;
    while(!done){
      const packet=await reader.read();done=packet.done;
      buffer+=decoder.decode(packet.value||new Uint8Array(),{stream:!done});
      let newline;
      while((newline=buffer.indexOf('\n'))>=0){
        const line=buffer.slice(0,newline).trim();buffer=buffer.slice(newline+1);
        if(!line)continue;
        const item=JSON.parse(line);
        if(item.error)throw new Error(item.error);
        if(item.delta){answer+=item.delta;partial.textContent=answer;$('chat-messages').scrollTop=$('chat-messages').scrollHeight;}
        if(item.done){chatHistory=[...history,{role:'assistant',content:answer}];$('chat-input').value='';$('chat-file').value='';$('chat-file-info').textContent='';$('chat-status').textContent='Ответ сохранён локально.';}
      }
    }
    await loadChatList();
  }catch(error){
    $('chat-status').textContent=error.name==='AbortError'?'Генерация остановлена. Неполный ответ не сохранён.':'Ошибка генерации: '+error.message;
    if(partial)partial.textContent=answer||'[Ответ не получен]';
    if(error.name!=='AbortError')toast(error.message,true);
    // Reload the authoritative server history to avoid duplicated partial turns.
    if(currentChatId){try{const result=await api('/api/workspace/chat?id='+currentChatId);chatHistory=result.messages;}catch{}}
  }finally{chatBusy=false;chatAbort=null;$('chat-send').disabled=!online;$('chat-stop').disabled=true;}
});
async function loadMemories(){
  try{
    const response=await api('/api/workspace/memories');const list=$('memory-list');list.replaceChildren();
    for(const item of response.memories){
      const card=document.createElement('div');card.className='memory-entry';
      const p=document.createElement('p');p.textContent=item.content;
      const button=document.createElement('button');button.className='button secondary';button.textContent='Удалить';
      button.onclick=async()=>{if(!confirm('Удалить запись?'))return;await api('/api/workspace/memory/delete',{id:item.id});await loadMemories();};
      card.append(p,button);list.append(card);
    }
  }catch(error){toast(error.message,true);}
}
$('memory-form').onsubmit=async event=>{
  event.preventDefault();try{await api('/api/workspace/memory/add',{content:$('memory-text').value});$('memory-text').value='';await loadMemories();}
  catch(error){toast(error.message,true);}
};
async function loadMonitor(){
  try{
    const result=await api('/api/workspace/status');
    const sys=result.system||{};
    const ram=sys.process_ram_bytes?' · RAM процесса: '+(sys.process_ram_bytes/1048576).toFixed(1)+' МиБ':'';
    const total=sys.ram_total_bytes?' · ОЗУ: '+(sys.ram_total_bytes/1073741824).toFixed(1)+' ГиБ':'';
    $('monitor-content').textContent='Диалогов: '+result.chats+' · Сообщений: '+result.messages+' · Записей памяти: '+result.memories+' · База: '+Math.ceil(result.database_bytes/1024)+' КиБ'+ram+total+' · CPU ядер: '+(sys.cpu_count||'?');
  }catch(error){$('monitor-content').textContent=error.message;}
}
$('monitor-refresh').onclick=loadMonitor;
$('monitor-backup').onclick=async()=>{
  if(!confirm('Создать локальную резервную копию истории и памяти?'))return;
  try{
    const result=await api('/api/workspace/backup',{confirm:true});
    $('monitor-backup-result').textContent='Копия '+result.name+' создана в папке данных Sakuya ('+Math.ceil(result.size/1024)+' КиБ).';
    toast('Резервная копия создана.');
  }catch(error){toast(error.message,true);}
};
$('sakuya-avatar').onclick=()=>{location.hash='#chat';$('chat-input').focus();};
async function loadDocuments(){
  try{
    const response=await api('/api/documents');
    const list=$('document-list');list.replaceChildren();
    if(!response.documents.length){
      const empty=document.createElement('p');empty.className='helper';empty.textContent='Документов пока нет.';list.append(empty);
    }
    for(const item of response.documents){
      const row=document.createElement('div');row.className='document-entry';
      const name=document.createElement('span');name.textContent=item.id+' · '+Math.ceil(item.bytes/1024)+' КиБ';
      const preview=document.createElement('button');preview.type='button';preview.className='button secondary';preview.textContent='Текст';
      preview.onclick=async()=>{
        try{$('document-preview').textContent=(await api('/api/documents/preview',{id:item.id})).text;}
        catch(error){toast(error.message,true);}
      };
      const remove=document.createElement('button');remove.type='button';remove.className='button secondary';remove.textContent='Удалить';
      remove.onclick=async()=>{if(!confirm('Удалить выбранный файл?'))return;try{await api('/api/documents/delete',{id:item.id});await loadDocuments();}catch(error){toast(error.message,true);}};
      row.append(name,preview,remove);list.append(row);
    }
  }catch(error){$('document-preview').textContent=error.message;}
}
$('document-upload-button').onclick=async()=>{
  const file=$('document-upload').files[0];
  if(!file)return toast('Выберите документ.',true);
  $('document-upload-button').disabled=true;
  try{
    if(file.size>2*1024*1024)throw new Error('Файл превышает 2 МиБ.');
    const bytes=new Uint8Array(await file.arrayBuffer());
    let binary='';for(let i=0;i<bytes.length;i+=8192)binary+=String.fromCharCode(...bytes.subarray(i,i+8192));
    const saved=await api('/api/documents/upload',{name:file.name,base64:btoa(binary)});
    $('document-upload').value='';
    toast('Документ сохранён: '+saved.name);await loadDocuments();
  }catch(error){toast(error.message,true);}
  finally{$('document-upload-button').disabled=false;}
};
document.addEventListener('visibilitychange',() => { if (!document.hidden) refresh(); });
route(); refresh();
setInterval(() => { if (!document.hidden) refresh(); },5000);
