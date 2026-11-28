Spire.shell('settings');
const $=id=>document.getElementById(id);
let busy=false,loaded=false,dirty=false;
function feedback(text,failed=false){$('feedback').textContent=text;$('feedback').classList.toggle('is-error',failed);}
function lock(value){
  busy=value;
  $('map-refresh').disabled=value;
  $('map-save').disabled=value||!loaded;
  $('map-policy').disabled=value||!loaded;
  $('map-wait').disabled=value||!loaded||$('map-policy').value!=='follow';
  $('map-form').setAttribute('aria-busy',String(value));
}
function paint(settings){
  if(!settings||!['follow','wait','auto'].includes(settings.policy)||typeof settings.vote_wait_seconds!=='number'||!Number.isFinite(settings.vote_wait_seconds)||settings.vote_wait_seconds<0||settings.vote_wait_seconds>86400){
    throw new Error('未收到有效的地图设置，请确认插件版本后重新读取。');
  }
  $('map-policy').value=settings.policy;
  $('map-wait').value=settings.vote_wait_seconds;
  loaded=true;dirty=false;
  $('map-note').textContent='已读取保存的设置。等待秒数仅对「跟随队友」有效；保存后立即生效并重新计时。';
}
async function runEntry(entry,args={}){
  if(busy)return;
  lock(true);feedback(entry==='sts2_get_status'?'正在读取设置…':'正在保存设置…');
  try{
    const data=await Spire.entry(entry,args);
    $('result').textContent=JSON.stringify(data,null,2);
    // The setter returns settings directly; the status entry nests them.
    paint(entry==='sts2_get_status'?data.map_policy:data);
    feedback(entry==='sts2_get_status'?'设置已读取；尚未修改任何参数。':data.summary||data.message||'设置已保存并应用，联机地图等待已重新计时。');
  }catch(e){feedback(e.message,true);$('result').textContent=e.message;}
  finally{lock(false);}
}
function markDirty(){dirty=true;$('map-note').textContent='有尚未保存的修改；点击「保存并应用」后生效。';}
$('map-policy').addEventListener('change',()=>{markDirty();lock(busy);});
$('map-wait').addEventListener('input',markDirty);
$('map-refresh').addEventListener('click',()=>{
  if(dirty&&!confirm('重新读取会覆盖尚未保存的修改，继续吗？'))return;
  runEntry('sts2_get_status');
});
$('map-form').addEventListener('submit',event=>{
  event.preventDefault();
  const wait=Number($('map-wait').value);
  if(busy||!loaded)return;
  if(!$('map-wait').value.trim()||!Number.isFinite(wait)||wait<0||wait>86400){feedback('请输入 0～86400 之间的等待秒数。',true);return;}
  runEntry('sts2_set_map_policy',{policy:$('map-policy').value,vote_wait_seconds:wait});
});
runEntry('sts2_get_status');

// Independent from the map form: refreshing one section never discards the other.
(() => {
  const form=$('preferences-form'), fields=$('preferences-fields');
  let pending=false, snapshot=null, specs=null;
  const controls=new Map(), changed=new Set();
  form.noValidate=true; // Validate only dirty fields; old invalid fields must not block unrelated saves.
  function notify(text,failed=false){
    $('preferences-feedback').textContent=text;
    $('preferences-feedback').classList.toggle('is-error',failed);
  }
  function lockPreferences(value){
    pending=value;form.setAttribute('aria-busy',String(value));
    $('preferences-refresh').disabled=value;
    $('preferences-retry').disabled=value;
    $('preferences-save').disabled=value||!snapshot||!changed.size;
    controls.forEach(control=>{control.disabled=value||!snapshot;});
  }
  function valueOf(key){
    const input=controls.get(key),spec=specs[key];
    if(spec.type==='boolean')return input.checked;
    if(spec.type==='number')return input.value.trim()===''?NaN:Number(input.value);
    return input.value;
  }
  function valid(value,spec){
    if(spec.type==='boolean')return typeof value==='boolean';
    if(spec.type==='number')return typeof value==='number'&&Number.isFinite(value)&&value>=spec.minimum&&value<=spec.maximum;
    return spec.type==='string'&&typeof value==='string'&&Object.hasOwn(spec.choices||{},value);
  }
  function track(key){
    if(valueOf(key)===snapshot[key])changed.delete(key);else changed.add(key);
    $('preferences-note').textContent=changed.size?`有 ${changed.size} 项尚未保存。只会提交这些修改，不覆盖其他参数。`:'当前没有未保存的修改。启动偏好将在下次启动读取。';
    controls.get(key).setAttribute('aria-invalid',String(!valid(valueOf(key),specs[key])));
    lockPreferences(pending);
  }
  function render(data){
    if(!data?.settings||!data.fields||!data.groups||!Object.keys(data.fields).length)throw new Error('未收到参数配置，请更新插件后重新读取。');
    for(const [key,spec] of Object.entries(data.fields)){
      if(!Object.hasOwn(data.settings,key)||(!valid(data.settings[key],spec)&&typeof data.field_errors?.[key]!=='string')||!data.groups[spec.group])throw new Error('参数格式不正确：'+key+'。请检查已保存的配置。');
    }
    const fragment=document.createDocumentFragment(),nextControls=new Map();
    for(const [group,title] of Object.entries(data.groups)){
      const section=document.createElement('section');section.className='setting';section.id='settings-'+group;
      const heading=document.createElement('h3');heading.textContent=title;section.append(heading);
      for(const [key,spec] of Object.entries(data.fields)){
        if(spec.group!==group)continue;
        const row=document.createElement('div');row.className='preference-field';
        const label=document.createElement('label');label.htmlFor='pref-'+key;label.textContent=spec.label+(spec.restart?' · 下次启动':'');
        const problem=data.field_errors?.[key];
        const input=document.createElement(spec.type==='string'?'select':'input');input.id='pref-'+key;
        if(spec.type==='string'){
          for(const [value,text] of Object.entries(spec.choices)){
            const option=document.createElement('option');option.value=value;option.textContent=text;input.append(option);
          }
          if(problem){const option=document.createElement('option');option.value=String(data.settings[key]);option.textContent='原值（无效）：'+String(data.settings[key]);input.prepend(option);}
          input.value=data.settings[key];
        }else if(spec.type==='boolean'){
          input.type='checkbox';input.checked=data.settings[key]===true;
        }else{
          input.type='number';input.min=spec.minimum;input.max=spec.maximum;input.step='any';input.required=true;input.value=data.settings[key];
        }
        const hint=document.createElement('p');hint.className='note';hint.id=input.id+'-hint';
        hint.textContent=spec.hint+(spec.type==='number'?` 范围：${spec.minimum}～${spec.maximum}。`:'');
        if(problem){hint.textContent+=' 原值：'+JSON.stringify(data.settings[key])+'。'+problem+'；请修改此项。';hint.classList.add('is-error');input.setAttribute('aria-invalid','true');}
        input.setAttribute('aria-describedby',hint.id);
        input.addEventListener(spec.type==='number'?'input':'change',()=>track(key));
        row.append(label,input,hint);section.append(row);nextControls.set(key,input);
      }
      fragment.append(section);
    }
    fields.replaceChildren(fragment);controls.clear();nextControls.forEach((value,key)=>controls.set(key,value));
    snapshot={...data.settings};specs=data.fields;changed.clear();
    $('preferences-note').textContent='已读取当前参数。修改后点击保存；启动偏好将在下次启动读取。';
  }
  async function request(save=false){
    if(pending)return;
    let patch;
    if(save){
      if(!snapshot||!changed.size)return;
      patch={};
      for(const key of changed){
        const value=valueOf(key);
        if(!valid(value,specs[key])){notify(specs[key].label+'：请输入允许范围内的值。',true);controls.get(key).focus();return;}
        patch[key]=value;
      }
    }
    lockPreferences(true);notify(save?'正在保存修改…':'正在读取参数…');
    $('preferences-load-state').textContent=save?'正在保存':'正在读取';
    $('preferences-load-state').className='badge';
    $('preferences-retry').hidden=true;
    try{
      const data=await Spire.entry(save?'sts2_update_settings':'sts2_get_settings',save?{patch}:{});
      $('result').textContent=JSON.stringify(data,null,2);
      render(data);
      const invalid=Object.keys(data.field_errors||{}).length;
      $('preferences-load-state').textContent=`已载入 ${controls.size} 项通用参数`+(invalid?` · ${invalid} 项待修正`:'');
      $('preferences-load-state').className='badge '+(invalid?'bad':'ok');
      $('preferences-overview-note').textContent=`下方按 ${Object.keys(data.groups).length} 组展示通用参数，另有联机地图的策略和等待秒数。两个区域分别保存，只提交修改项。`;
      notify(save?data.summary||'修改已保存。':'参数已读取；未执行任何设置更改。');
    }catch(error){
      notify(error.message+' 若保存结果不确定，请先读取参数确认。',true);
      $('preferences-load-state').textContent=save?'保存未确认':'读取失败';
      $('preferences-load-state').className='badge bad';
      $('preferences-overview-note').textContent=(snapshot?'已保留当前表单与未保存修改。':'通用参数暂时无法加载，不是只有联机地图可配置。')+' '+error.message;
      $('preferences-retry').hidden=false;
      if(!snapshot)fields.querySelectorAll('.note').forEach(note=>{note.textContent='参数未加载，请使用上方「重新读取通用参数」重试。';});
    }
    finally{lockPreferences(false);}
  }
  form.addEventListener('submit',event=>{event.preventDefault();request(true);});
  function refreshPreferences(){
    if(changed.size&&!confirm('重新读取会覆盖尚未保存的参数修改，继续吗？'))return;
    request();
  }
  $('preferences-refresh').addEventListener('click',refreshPreferences);
  $('preferences-retry').addEventListener('click',refreshPreferences);
  window.addEventListener('beforeunload',event=>{
    if(dirty||changed.size||pending){event.preventDefault();event.returnValue='';}
  });
  request();
})();
