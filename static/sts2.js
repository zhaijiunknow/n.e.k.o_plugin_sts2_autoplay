Spire.shell('sts2');
const $=id=>document.getElementById(id);
let busy=false,liveLoaded=false,liveDirty=false;
function feedback(text,failed=false){$('feedback').textContent=text;$('feedback').classList.toggle('is-error',failed);}
function paintBrain(data) {
  const brain=data.decision_engine;
  let engine=brain?.engine || 'unknown',phase=brain?.phase || 'unknown';
  let detail=brain?.detail || '插件尚未返回当前决策来源，请更新插件后重试。';
  let label=({policy:'策略网络',solver:'求解器',heuristic:'启发式后备'})[engine] || (phase==='inactive'?'未接管':'等待确认');
  if(data.server?.state!=='connected'){
    engine='unknown';phase='offline';label='未连接';detail='连接游戏后才能确认当前主脑。';
  }else if(data.autoplay?.standby || data.autoplay?.state!=='running'){
    engine='unknown';phase='inactive';label='未接管';detail=data.autoplay?.standby?'当前待机，不执行自动操作。':'自动游玩未运行；陪玩建议不代表主脑接管。';
  }
  $('brain-status').dataset.engine=engine;
  $('live-brain').textContent=label;
  $('live-brain-phase').textContent=({ready:'本步决策',planning:'规划中',checking:'确认中',waiting:'等待行动',forced:'直接选项',inactive:'未接管',offline:'离线'})[phase] || '等待状态';
  const characters={IRONCLAD:'铁甲战士',SILENT:'静默猎手',DEFECT:'故障机器人',REGENT:'储君',NECROBINDER:'亡灵契约师',FOOL:'愚者'};
  $('live-brain-detail').textContent=(engine!=='unknown'&&brain?.character?`${characters[brain.character] || brain.character} · `:'')+detail+(engine==='heuristic'?' · 本步使用规则规划，并非神经网络或战斗搜索。':'');
}
function brainUnavailable(message) {
  paintTalkSwitches();
  $('brain-status').dataset.engine='unknown';
  $('live-brain').textContent='状态未知';
  $('live-brain-phase').textContent='刷新失败';
  $('live-brain-detail').textContent='无法确认当前主脑：'+message;
}
function paintTalkSwitches(data={}) {
  for(const [key,id,entry] of [['host_digest','digest-state','sts2_set_host_digest'],['run_reflection','reflection-state','sts2_set_run_reflection']]) {
    const enabled=data[key]?.enabled,known=typeof enabled==='boolean';
    $(id).textContent=known?(enabled?'已开启':'已关闭'):'状态未知';
    $(id).className='badge '+(known&&enabled?'ok':'');
    document.querySelectorAll(`[data-entry="${entry}"]`).forEach(button=>{
      const selected=known&&JSON.parse(button.dataset.args).enabled===enabled;
      button.setAttribute('aria-pressed',String(selected));
      button.classList.toggle('is-selected',selected);
    });
  }
}
function paint(data) {
  if(data.server){paintBrain(data);paintTalkSwitches(data);}
  if(data.server){
    const connected=data.server.state==='connected';
    $('connection').textContent=connected?'游戏已连接':data.server.state==='error'?'连接异常':'未连接游戏';
    $('connection').className='badge '+(connected?'ok':'bad');
  }
  if(data.autoplay){
    const state=data.autoplay.standby?'standby':data.autoplay.state;
    $('autoplay-title').textContent=({running:'正在攀登中',paused:'稍作休息',standby:'正在待机',idle:'准备好，再出发',stopped:'本次自动游玩已停止'})[state] || state || '状态未知';
    $('autoplay-note').textContent=data.autoplay.pause_reason || data.autoplay.stop_reason || (state==='running'?'自动游玩已开启。当前决策来源和回退原因见「当前主脑」。':'打开面板不会自动开始游玩，点击「开始自动玩」后才会接管。');
  }
  if(data.run){
    $('live-floor').textContent=data.run.floor ?? '—';
    const names={COMBAT:'战斗',MAP:'地图',REWARD:'奖励',CARD_SELECTION_REWARD:'选牌',REST:'休息',SHOP:'商店',EVENT:'事件',MAIN_MENU:'主菜单',CHARACTER_SELECT:'选人',GAME_OVER:'局末',CHEST:'宝箱',UNKNOWN:'未知',CAPSTONE_SELECTION:'抉择'};
    $('live-screen').textContent=names[data.run.screen] || data.run.screen || '—';
  }
  if(data.policy){
    $('live-decisions').textContent=data.policy.decisions ?? '—';$('live-fallbacks').textContent=data.policy.fallbacks ?? '—';
    $('live-policy').textContent=!data.policy.enabled?'策略网络未启用':!data.policy.runtime_ready?'运行环境未就绪，请前往策略网络页修复':data.policy.last_note || `环境已就绪 · ${data.policy.characters?.length || 0} 个角色模型可用`;
  }
  for(const [key,id] of [['companion_mode','companion-state'],['danmaku','danmaku-state']])if(data[key]){
    $(id).textContent=data[key].enabled?'已开启':'已关闭';$(id).className='badge '+(data[key].enabled?'ok':'');
  }
  if(data.livestream){
    const live=data.livestream;
    $('livestream-state').textContent=live.enabled?(live.waiting?`互动中 · 剩余 ${live.remaining_seconds} 秒`:'已开启'):'已关闭';
    if(!liveDirty){
      $('livestream-enabled').value=String(live.enabled);
      $('livestream-profile').value=live.profile;
      $('livestream-scale').value=live.wait_scale;
      liveLoaded=true;
    }
  }
  $('live-time').textContent='更新于 '+new Date().toLocaleTimeString('zh-CN',{hour12:false});
}
function lock(value){
  busy=value;
  document.querySelectorAll('[data-entry]').forEach(b=>{b.disabled=value;});
  for(const id of ['livestream-enabled','livestream-profile','livestream-scale','livestream-save'])$(id).disabled=value||!liveLoaded;
  $('livestream-form').setAttribute('aria-busy',String(value));
}
async function runEntry(entry,args={},quiet=false){
  if(busy)return;
  lock(true);if(!quiet){feedback('正在处理，请稍候…');$('result').textContent='执行中…';}
  try {
    const data=await Spire.entry(entry,args);
    if(!quiet)$('result').textContent=JSON.stringify(data,null,2);
    if(entry==='sts2_get_status')paint(data);
    else {
      if(entry==='sts2_set_livestream'){liveDirty=false;paint(data);}
      try {paint(await Spire.entry('sts2_get_status'));}
      catch(e){brainUnavailable(e.message);feedback('操作已返回，但刷新状态失败：'+e.message,true);return;}
    }
    const summary=data.message || data.summary;
    if(!quiet)feedback(typeof summary==='string'&&!summary.startsWith('transport=')?summary:entry==='sts2_get_status'?'状态已刷新；页面可见时每 5 秒自动更新。':'操作已完成，状态已刷新。');
  } catch(e){if(entry==='sts2_get_status')brainUnavailable(e.message);if(!quiet){feedback(e.message,true);$('result').textContent=e.message;}}
  finally{lock(false);}
}
document.querySelectorAll('[data-entry]').forEach(button=>button.addEventListener('click',()=>{
  if(button.dataset.entry==='sts2_execute_planned_operation'&&!confirm('执行当前规划动作？这会实际操作游戏。'))return;
  runEntry(button.dataset.entry,JSON.parse(button.dataset.args||'{}'));
}));
for(const id of ['livestream-enabled','livestream-profile','livestream-scale'])$(id).addEventListener('input',()=>{liveDirty=true;});
$('livestream-form').addEventListener('submit',event=>{
  event.preventDefault();
  const scale=Number($('livestream-scale').value);
  if(!liveLoaded||!Number.isFinite(scale)||scale<0.25||scale>3){feedback('请输入 0.25～3 的等待倍率。',true);return;}
  runEntry('sts2_set_livestream',{enabled:$('livestream-enabled').value==='true',profile:$('livestream-profile').value,wait_scale:scale});
});
runEntry('sts2_get_status');

// Read-only refresh; never starts autoplay, downloads models or overwrites dirty live settings.
async function pollStatus(){
  if(!document.hidden&&!busy)await runEntry('sts2_get_status',{},true);
  setTimeout(pollStatus,5000);
}
setTimeout(pollStatus,5000);
document.addEventListener('visibilitychange',()=>{
  if(!document.hidden&&!busy)runEntry('sts2_get_status',{},true);
});
