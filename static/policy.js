Spire.shell('policy');
const $ = id => document.getElementById(id);
const mb = value => (Math.max(0,Number(value)||0)/1048576).toFixed(1) + ' MB';
let snapshot = null, selected = '', pending = false, initialized = false, lastStamp = 0;
let pollTimer = null, pollGeneration = 0;
const cards = new Map();
function error(message) {
  $('page-error').hidden = !message;
  $('page-err').textContent = message ? String(message) : '';
}
function progress(id, job, running) {
  const el = $(id), pct = job.total_bytes ? Math.max(0,Math.min(100,100*job.done_bytes/job.total_bytes)) : 0;
  el.hidden = !running; el.firstElementChild.style.width = pct + '%';
  el.setAttribute('aria-valuenow', String(Math.round(pct)));
}
function apply(data) {
  if (!data?.runtime || !Array.isArray(data.models)) throw new Error('插件没有返回有效的模型状态，请重新连接。');
  snapshot = data;
  if (Number.isFinite(Number(data.updated_at))) lastStamp = Math.max(lastStamp, Number(data.updated_at));
  $('root').textContent = data.root || '插件未提供路径';
  const rt = data.runtime, running = rt.state === 'running', failed = rt.state === 'failed' && !rt.ready;
  const badge = $('rt-badge');
  badge.className = 'badge ' + (rt.ready?'ok':running?'run':failed?'bad':'');
  badge.textContent = rt.ready?'已就绪':running?'准备中':failed?'修复失败':rt.repair_needed?'待补齐':'未安装';
  $('rt-line').textContent = running ? `${mb(rt.done_bytes)} / ${mb(rt.total_bytes || rt.download_bytes)} · ${rt.current || ''} · ${rt.message || '连接中'}` : rt.ready ? 'Python 与 PyTorch 已就绪，内容目录校验通过。' : rt.repair_needed ? `只需补齐 / 修复内容目录约 ${mb(rt.repair_bytes)}，无需重下 Python、PyTorch 或模型。` : `准备 Python 与 PyTorch，预计下载 ${mb(rt.download_bytes)}。已有模型无需删除。`;
  $('rt-err').hidden = !failed; $('rt-err').textContent = rt.error || '';
  $('runtime-repair').textContent = running?'环境准备中…':failed?'重试修复':rt.ready?'重新检查环境':'准备 / 修复环境';
  $('runtime-repair').disabled = running || pending;
  progress('rt-bar',rt,running);
  renderCards(data.models);
  renderDetail();
}
function renderCards(models) {
  // Keep nodes (and keyboard focus) alive while the progress snapshot is polled.
  if (!cards.size) $('cards').replaceChildren();
  const keys = new Set(models.map(m=>m.character));
  for (const [key,el] of cards) if (!keys.has(key)) { el.remove();cards.delete(key); }
  if (!keys.has(selected)) selected = models[0]?.character || '';
  models.forEach((m,index)=>{
    let card = cards.get(m.character);
    const meta = Spire.characters[m.character];
    if (!card) {
      card = document.createElement('button');card.type='button';card.className='character';
      card.setAttribute('aria-controls','model-detail');
      if (meta) {
        const img=document.createElement('img');img.src=Spire.art(m.character);img.alt='';img.draggable=false;
        img.addEventListener('error',()=>{img.hidden=true;});card.append(img);
      }
      const number=document.createElement('span');number.className='character-number';number.textContent=String(index+1).padStart(2,'0');
      const copy=document.createElement('span');copy.className='character-copy';
      for(const cls of ['character-en','character-name','character-status']) {const el=document.createElement('span');el.className=cls;copy.append(el);}
      const meter=document.createElement('span');meter.className='character-meter';
      card.append(number,copy,meter);
      card.addEventListener('click',()=>select(m.character,true));
      card.addEventListener('keydown',event=>{
        const list=[...cards.keys()],i=list.indexOf(m.character);
        const next=event.key==='ArrowRight'?(i+1)%list.length:event.key==='ArrowLeft'?(i-1+list.length)%list.length:event.key==='Home'?0:event.key==='End'?list.length-1:null;
        if(next!==null){event.preventDefault();select(list[next],true);cards.get(list[next]).focus();}
      });
      cards.set(m.character,card);$('cards').append(card);
    }
    const state=m.ready?'已下载':m.state==='running'?'下载中':m.state==='failed'?'下载失败':'待下载';
    card.style.setProperty('--tone',meta?.color || '#63736d');
    card.querySelector('.character-en').textContent=m.character;
    card.querySelector('.character-name').textContent=meta?.short || m.label || m.character;
    card.querySelector('.character-status').textContent=state;
    card.setAttribute('aria-label',`${m.label || m.character} · ${state}`);
    card.setAttribute('aria-expanded',String(selected===m.character));
    card.querySelector('.character-meter').style.width=m.ready?'100%':m.state==='running'&&m.total_bytes?Math.min(100,100*m.done_bytes/m.total_bytes)+'%':'0';
  });
  $('model-count').textContent=`${models.filter(m=>m.ready).length} / ${models.length} 模型已下载`;
}
function select(key,scroll=false) {
  selected=key;
  for(const [id,card] of cards)card.setAttribute('aria-expanded',String(id===key));
  renderDetail();
  if(scroll&&matchMedia('(max-width:640px)').matches) {
    // Scroll only the horizontal strip, never move the entire host/plugin page.
    const el=cards.get(key),box=$('cards');
    box.scrollTo({left:el.offsetLeft-box.offsetLeft-(box.clientWidth-el.clientWidth)/2,behavior:matchMedia('(prefers-reduced-motion:reduce)').matches?'auto':'smooth'});
  }
}
function renderDetail() {
  const m=snapshot?.models.find(m=>m.character===selected);
  $('model-detail').hidden=!m;if(!m)return;
  const running=m.state==='running',failed=m.state==='failed'&&!m.ready;
  $('model-en').textContent=`${m.character} / POLICY MODEL`;
  $('model-name').textContent=m.label || Spire.characters[selected]?.name || selected;
  $('model-desc').textContent=Spire.characters[selected]?.desc || '为这位角色准备独立的策略网络。';
  $('model-file').textContent=`${m.file || '模型文件'} · ${mb(m.size)}`;
  $('model-status').textContent=m.ready?(snapshot.runtime.ready?'模型与环境已就绪 · 自动游玩时可使用策略网络':'模型已下载 · 等待运行环境就绪'):running?`${mb(m.done_bytes)} / ${mb(m.total_bytes || m.size)} · ${m.message || '连接中'}`:failed?'下载未完成，已有断点会保留。':'下载后由插件自动识别，无需手动配置路径。';
  const download=$('model-download');download.disabled=pending||running||m.ready;
  download.textContent=m.ready?'已下载':running?'正在下载…':failed?'重试下载':`下载模型 · ${mb(m.size)}`;
  $('model-remove').hidden=!m.ready;$('model-remove').disabled=pending;
  $('model-error').hidden=!failed;$('model-error').textContent=m.error || '';
  progress('model-bar',m,running);
}
async function act(entry,args={}) {
  if(pending)return;
  pending=true;pollGeneration++;clearTimeout(pollTimer);error('');
  if(snapshot)apply(snapshot);else $('runtime-repair').disabled=true;
  $('page-retry').disabled=true;
  try { apply(await Spire.entry(entry,args));initialized=true; }
  catch(e){error(e.message);}
  finally {pending=false;$('page-retry').disabled=false;if(snapshot)apply(snapshot);else $('runtime-repair').disabled=false;if(initialized)schedulePoll();}
}
function schedulePoll() {
  clearTimeout(pollTimer);
  pollTimer=setTimeout(poll,snapshot?.runtime.state==='running'||snapshot?.models.some(m=>m.state==='running')?1000:4000);
}
async function poll() {
  if(pending || !initialized)return;
  const generation=pollGeneration;
  try {
    const data=await Spire.request('policy_status.json?t='+Date.now());
    if(generation!==pollGeneration)return;
    // Never replace a fresh entry response with an older on-disk snapshot.
    if(Number(data.updated_at)>lastStamp){apply(data);error('');}
  } catch(e){if(generation===pollGeneration)error('状态刷新失败，以下保留上次结果：'+e.message);}
  if(generation===pollGeneration)schedulePoll();
}
$('runtime-repair').addEventListener('click',()=>act('sts2_policy_prepare_runtime'));
$('page-retry').addEventListener('click',()=>act('sts2_policy_status'));
$('model-download').addEventListener('click',()=>act('sts2_policy_download_model',{character:selected}));
$('model-remove').addEventListener('click',()=>{
  const m=snapshot.models.find(m=>m.character===selected);
  if(confirm(`删除「${m.label || selected}」的模型？该角色将回退求解器与启发式。`))act('sts2_policy_remove_model',{character:selected});
});
// Opening/retrying the page is read-only. Downloads require an explicit action.
act('sts2_policy_status');
