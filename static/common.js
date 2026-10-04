const $=s=>document.querySelector(s);
const C={critical:'#dc2626',high:'#ea580c',watch:'#ca8a04',moderate:'#2563eb',low:'#16a34a'};
const esc=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const fmt=t=>t?new Date(t).toLocaleString('en-IN',{day:'2-digit',month:'short',hour:'2-digit',minute:'2-digit',second:'2-digit'}):'–';
const badge=l=>l?`<span class="badge" style="background:${C[l]}">${l}</span>`:'–';
async function api(u,body){
  const o=body===undefined?{}:{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)};
  const r=await fetch(u,o);if(r.status==401){location.reload();return}return r.json()}
async function logout(role){await api('/api/logout',{role});location.href='/'+role}
async function aiPill(){
  let s;try{s=await fetch('/api/ai/status').then(r=>r.json())}catch(e){s={ok:false,err:'Server unreachable'}}
  const p=$('#ai'),b=$('#aibanner');if(!p)return;const deg=s.ok&&s.analysis_ok===false;
  p.className='pill '+(deg?'warn':s.ok?'ok':'bad');
  p.innerHTML=deg?`<i></i>AI reachable · analysis failing`:s.ok?`<i></i>AI online · Qwen2.5-3B · ${s.ms}ms`:`<i></i>AI OFFLINE · fallback mode`;
  b.hidden=s.ok&&!deg;
  b.innerHTML=deg?`⚠ <b>AI server is reachable but the last analysis failed</b> (${esc(s.analysis_err)}). Check-ins use the keyword fallback until it recovers.`
   :`⚠ <b>AI engine unreachable</b> (${esc(s.err)} at ${esc(s.base||'?')}). Check-ins are still recorded, but analysed by the basic keyword fallback and flagged as <b>degraded</b>.`;
  if(window.userMode&&window.t){p.innerHTML='<i></i>'+(s.ok&&!deg?t('pill_on'):t('pill_off'));b.hidden=s.ok&&!deg;b.textContent='⚠ '+t('banner_off')}}
aiPill();setInterval(aiPill,8000);
