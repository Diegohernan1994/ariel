'use strict';
(() => {
  const host=document.getElementById('support-widget'), toggle=document.getElementById('support-toggle'), box=document.getElementById('support-box');
  const feed=document.getElementById('support-feed'), input=document.getElementById('support-input'), status=document.getElementById('support-status');
  const form=document.getElementById('support-form'), send=document.getElementById('support-send'), report=document.getElementById('support-report');
  let section='chat', history=[], busy=false;
  const baseFetch=window.fetch.bind(window), reported=new Map();
  function message(role,text) { const p=document.createElement('p'); p.className='support-message '+role; p.textContent=text; feed.appendChild(p);feed.scrollTop=feed.scrollHeight; }
  function open(value) { box.hidden=!value;toggle.setAttribute('aria-expanded',String(value));if(value)input.focus(); }
  function sync() { section=document.querySelector('[role="tab"][aria-selected="true"]')?.id.replace('panel-tab-','') || 'chat';host.hidden=section==='chat';if(host.hidden)open(false);document.getElementById('support-context').textContent={tools:'Herramientas',analytics:'Analíticas',status:'Estado de tienda',chat:'Chat'}[section]; }
  async function record(payload) {
    const response=await baseFetch('/api/support/report',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...payload,section})});
    if(!response.ok)throw Error('No se pudo guardar el reporte. No se envió ningún aviso.');
    return response.json();
  }
  function notice(text) { status.hidden=false;status.textContent=text;const toast=document.getElementById('support-toast');if(host.hidden){document.getElementById('support-toast-text').textContent=text;toast.hidden=false;}else{toast.hidden=true;} }
  async function systemFailure(path,data) {
    if(data?.incident) { notice('Es un problema del sistema. '+data.incident.message);return; }
    if(reported.has(path) && Date.now()-reported.get(path)<300000)return;
    reported.set(path,Date.now());
    notice('La solicitud falló en el sistema. Estoy intentando guardar un reporte local.');
    try {const incident=await record({code:'HTTP_SERVER_ERROR',route:path});notice('Es un problema del sistema. '+incident.message);}
    catch(_) {notice('La solicitud falló en el sistema, pero no pude guardar el reporte. No se envió ningún aviso.');}
  }
  window.fetch=async(input,init)=>{
    const url=new URL(typeof input==='string'?input:input.url,location.href);
    let response;
    try {response=await baseFetch(input,init);}catch(error){if(url.origin===location.origin && url.pathname.startsWith('/api/'))notice('No pude conectar con el servidor. No puedo confirmar que sea un fallo del sistema ni que se haya guardado un reporte. Comprobá la conexión y volvé a intentar.');throw error;}
    if(url.origin===location.origin && url.pathname.startsWith('/api/') && !url.pathname.startsWith('/api/support/') && (response.status>=500 || ['/api/chat','/api/voice-order'].includes(url.pathname))) {
      let data=null;try{data=await response.clone().json();}catch(_){}
      if(response.status>=500 || data?.systemError)void systemFailure(url.pathname,data);
    }
    return response;
  };
  document.getElementById('support-toast-close').addEventListener('click',()=>{document.getElementById('support-toast').hidden=true;});
  toggle.addEventListener('click',()=>open(box.hidden));
  document.getElementById('support-close').addEventListener('click',()=>{open(false);toggle.focus();});
  box.addEventListener('keydown',event=>{if(event.key==='Escape'){open(false);toggle.focus();}});
  window.addEventListener('ariel:section',sync);
  form.addEventListener('submit',async event=>{
    event.preventDefault(); const text=input.value.trim(); if(!text||busy)return;
    busy=true;send.disabled=true;message('user',text);input.value='';
    try {
      const response=await baseFetch('/api/support/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message:text,section,history})});
      const data=await response.json();
      if(!response.ok){if(response.status>=500)notice('Es un problema del sistema. '+(data.incident?.message || 'No pude confirmar que el reporte quedara guardado.'));throw Error(data.error||'No pude responder la consulta.');}
      message('assistant',data.reply);history.push({role:'user',text},{role:'assistant',text:data.reply});history=history.slice(-8);
    } catch(error) {message('assistant','No pude completar la ayuda. Si se perdió la conexión, no puedo confirmar que sea un fallo del sistema. Podés intentar de nuevo o registrar el problema.');}
    finally {busy=false;send.disabled=false;}
  });
  report.addEventListener('click',async()=>{
    const description=input.value.trim();
    if(!description){notice('Describí el problema en el campo de abajo y tocá «Registrar problema». No incluyas contraseñas ni claves.');input.focus();return;}
    report.disabled=true;
    try {const incident=await record({code:'USER_REPORT',description});notice(incident.message);input.value='';}
    catch(error){notice(error.message);}finally{report.disabled=false;}
  });
  document.getElementById('support-reports').addEventListener('click',async()=>{
    try {const response=await baseFetch('/api/support/reports');if(!response.ok)throw Error();const data=await response.json();message('assistant',data.reports.length?data.reports.map(r=>`${r.id} · ${r.created.slice(0,16).replace('T',' ')} · ${r.code}`).join('\n'):'Todavía no hay reportes guardados.');}
    catch(_){notice('No pude consultar los reportes.');}
  });
  let lastBrowserError=0;
  async function browserError() {
    if(Date.now()-lastBrowserError<300000)return;lastBrowserError=Date.now();
    try{const incident=await record({code:'BROWSER_SCRIPT_ERROR'});notice('Se detectó un error de la aplicación. '+incident.message);}catch(_){notice('Se detectó un error de la aplicación, pero no pude guardar el reporte.');}
  }
  window.addEventListener('error',event=>{if(event instanceof ErrorEvent)void browserError();});
  window.addEventListener('unhandledrejection',()=>void browserError());
  message('assistant','Soy Ariel. Si tenés dudas, contame qué querés hacer y te guío. Los reportes se guardan acá; no se envían por correo.');sync();
})();
