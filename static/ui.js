/* Shared transport: no dependency on a frontend framework or a remote CDN. */
window.Spire = (() => {
  const characters = {
    IRONCLAD: {name:'铁甲战士',short:'铁甲',color:'#9a4934',desc:'以力量与烈焰，踏上新的攀登。'},
    SILENT: {name:'静默猎手',short:'静默',color:'#547c62',desc:'在刀锋与毒雾之间，寻找致胜的路径。'},
    DEFECT: {name:'故障机器人',short:'故障',color:'#4e7f98',desc:'唤醒充能球，让每一次运算成为可能。'},
    REGENT: {name:'储君',short:'储君',color:'#af8547',desc:'以群星为冠，以王权开辟前路。'},
    NECROBINDER: {name:'亡灵契约师',short:'亡灵',color:'#826a9c',desc:'与亡灵同行，向高塔缔下新的契约。'},
    FOOL: {name:'愚者',short:'愚者',color:'#9b6376',desc:'踏入未知，开始属于你的下一段旅程。'},
  };
  const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  function character(value) {
    const key = String(value || '').toUpperCase();
    if (characters[key]) return key;
    return Object.keys(characters).find(k => characters[k].name === value || characters[k].short === value) || '';
  }
  const art = key => characters[key] ? `assets/${key.toLowerCase()}.webp` : '';
  function message(data) {
    const error = data?.error || data?.detail || data;
    if (error?.code === 'NOT_READY' || error?.code === 'PLUGIN_NOT_READY') return '插件尚未运行。请先在 N.E.K.O 插件管理中启动「杀戮尖塔 2 陪玩」，再重试。';
    return error?.message || (typeof error === 'string' ? error : JSON.stringify(error));
  }
  async function request(url, options = {}) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 12000);
    try {
      const response = await fetch(url, {...options, signal:controller.signal, cache:'no-store'});
      const data = await response.json();
      if (!response.ok) throw new Error(message(data) || `HTTP ${response.status}`);
      return data;
    } catch (error) {
      if (error.name === 'AbortError') throw new Error('请求超时。操作可能仍在后台执行，请先刷新状态，不要重复提交。');
      throw error;
    } finally { clearTimeout(timer); }
  }
  async function entry(entryId, args = {}) {
    const created = await request('/runs', {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({plugin_id:'sts2_autoplay',entry_id:entryId,args})});
    if (!created.run_id) throw new Error(message(created) || '未收到任务编号');
    const path = '/runs/' + encodeURIComponent(created.run_id);
    const deadline = Date.now() + 45000;
    while (Date.now() < deadline) {
      const run = await request(path);
      if (['failed','cancelled','canceled'].includes(run.status)) throw new Error(message(run.error || run));
      if (run.stage === 'done' || run.status === 'succeeded') {
        const exp = await request(path + '/export');
        const json = exp.items?.[0]?.json;
        if (!json || typeof json !== 'object') throw new Error('任务完成，但没有返回有效结果。请刷新状态确认。');
        const data = json.data ?? json;
        if (typeof data !== 'object' || Array.isArray(data)) throw new Error('插件返回的结果格式不正确，请刷新状态确认。');
        if (json.success === false || data.success === false || data.status === 'error') throw new Error(message(data));
        return data;
      }
      await new Promise(resolve => setTimeout(resolve, 500));
    }
    throw new Error('等待结果超时。操作可能仍在后台执行，请先刷新状态，不要重复提交。');
  }
  const uiRevision='20261127-parameters2';
  function shell(page) {
    const links = [['index','总览'],['sts2','游玩控制'],['policy','策略网络'],['history','游玩历史'],['settings','参数配置']];
    const header = document.getElementById('masthead');
    if (header) header.innerHTML = '<a class="brand" href="index.html" aria-label="NekoSpire 总览"><span class="brand-mark" aria-hidden="true">♜</span><span><b>NEKOSPIRE</b><small>YOUR SPIRE COMPANION</small></span></a><nav aria-label="主导航">' + links.map(([id,label])=>`<a href="${id}.html?v=${uiRevision}" ${page===id?'aria-current="page"':''}>${label}</a>`).join('') + '</nav>';
  }
  return {characters,character,art,esc,entry,request,shell};
})();
