// Shared by the login page and the beneficiary portal: whole-interface language switching (like government portals).
const LANGS={auto:'Auto-detect',en:'English',hx:'Hinglish',hi:'हिन्दी · Hindi',bn:'বাংলা · Bengali',ta:'தமிழ் · Tamil',te:'తెలుగు · Telugu',mr:'मराठी · Marathi',gu:'ગુજરાતી · Gujarati',kn:'ಕನ್ನಡ · Kannada',ml:'മലയാളം · Malayalam',pa:'ਪੰਜਾਬੀ · Punjabi',or:'ଓଡ଼ିଆ · Odia',as:'অসমীয়া · Assamese',ur:'اردو · Urdu',ne:'नेपाली · Nepali',kok:'कोंकणी · Konkani',mai:'मैथिली · Maithili',doi:'डोगरी · Dogri',brx:'बड़ो · Bodo',sa:'संस्कृतम् · Sanskrit',sat:'ᱥᱟᱱᱛᱟᱲᱤ · Santali',ks:'کٲشُر · Kashmiri',sd:'سنڌي · Sindhi',mni:'মৈতৈলোন্ · Manipuri'};
const Q=s=>document.querySelector(s);
let EN={},S={},CUR='en';
const t=(k,p)=>{let s=S[k]??EN[k]??k;if(p)for(const x in p)s=s.split('{'+x+'}').join(p[x]);return s};
async function loadStrings(code){
  if(!Object.keys(EN).length)EN=(await (await fetch('/api/i18n/en')).json()).s;
  if(['en','auto','hx'].includes(code)){S=EN;return{dir:'ltr',mt:false}}
  const r=await (await fetch('/api/i18n/'+code)).json();S={...EN,...r.s};return r}
function applyI18n(){
  document.querySelectorAll('[data-i18n]').forEach(e=>e.textContent=t(e.dataset.i18n));
  document.querySelectorAll('[data-i18n-ph]').forEach(e=>e.placeholder=t(e.dataset.i18nPh))}
function i18nBusy(on){let d=Q('#i18nbusy');
  if(!d){d=document.createElement('div');d.id='i18nbusy';d.className='overlay';d.style.zIndex=80;d.hidden=true;
   d.innerHTML='<div class="card" style="text-align:center"><span class="spin"></span> 🌐 Preparing your language…<br><small>The first time can take a minute.</small></div>';document.body.append(d)}
  d.hidden=!on}
async function setUILang(code){
  i18nBusy(true);let info={dir:'ltr',mt:false};try{info=await loadStrings(code)}catch(e){console.warn('i18n',e)}i18nBusy(false);
  CUR=code;localStorage.setItem('sahayak_lang',code);document.documentElement.lang=code;document.documentElement.dir=info.dir||'ltr';
  const n=Q('#mtnote');if(n)n.hidden=!info.mt;
  applyI18n();if(window.onLang)window.onLang()}
const langOptions=()=>Object.entries(LANGS).map(([k,v])=>`<option value="${k}">${v}</option>`).join('');
