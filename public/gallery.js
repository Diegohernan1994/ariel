'use strict';
(() => {
  const grid=document.getElementById('gallery-grid'), message=document.getElementById('gallery-message');
  const filter=document.getElementById('gallery-filter'), chosen=[];
  let snapshot=null, loading=false;
  const labels={all:'Todos',image:'Fotos',video:'Videos',favorite:'Favoritos',trash:'Papelera'};
  function note(text){message.textContent=text;}
  function button(text,action,disabled=false){const b=document.createElement('button');b.type='button';b.textContent=text;b.disabled=disabled;b.className='border border-[#ddd] px-2 py-1 text-xs bg-white disabled:opacity-40';b.addEventListener('click',action);return b;}
  function render(){
    if(!snapshot)return;
    const live=new Set(snapshot.files.map(f=>f.name));for(let i=chosen.length-1;i>=0;i--)if(!live.has(chosen[i]))chosen.splice(i,1);
    document.getElementById('gallery-summary').textContent=`${snapshot.activeCount} / ${snapshot.maxFiles} archivos · ${snapshot.favoriteCount} favoritos · ${snapshot.trashCount} en papelera · ${(snapshot.usedBytes/1024**2).toFixed(1)} / ${(snapshot.maxBytes/1024**2).toFixed(0)} MB`;
    document.getElementById('gallery-use').textContent=`Usar seleccionados (${chosen.length})`;
    document.getElementById('gallery-use').disabled=!chosen.length;
    grid.replaceChildren();
    const mode=filter.value;
    const list=(mode==='trash'?snapshot.trash:snapshot.files).filter(f=>mode==='image'||mode==='video'?f.type===mode:mode==='favorite'?f.favorite:true);
    if(!list.length){const empty=document.createElement('p');empty.textContent=`No hay archivos en «${labels[mode]}».`;grid.appendChild(empty);return;}
    for(const file of list){
      const card=document.createElement('article');card.className='border border-[#ddd] p-3 bg-white flex flex-col gap-2';
      if(mode!=='trash'){
        const preview=document.createElement(file.type==='image'?'img':'video');preview.src=file.url;preview.className='w-full h-32 object-cover bg-[#fafafa]';
        if(file.type==='image'){preview.loading='lazy';preview.alt=file.originalName;}else{preview.muted=true;preview.preload='none';preview.controls=true;}
        card.appendChild(preview);
      }
      const name=document.createElement('p');name.className='text-xs break-all';name.textContent=file.originalName;card.appendChild(name);
      const info=document.createElement('p');info.className='text-xs text-[#666]';info.textContent=`${file.type==='image'?'Foto':'Video'} · ${(file.size/1024**2).toFixed(1)} MB${file.inUse?' · En uso':''}`;card.appendChild(info);
      const actions=document.createElement('div');actions.className='flex flex-wrap gap-2';
      if(mode==='trash'){
        const expiry=document.createElement('p');expiry.className='text-xs';expiry.textContent='Recuperable hasta '+new Intl.DateTimeFormat('es-AR',{timeZone:'America/Argentina/Cordoba',dateStyle:'short',timeStyle:'short'}).format(new Date(file.expiresAt));card.appendChild(expiry);
        actions.appendChild(button('Restaurar',()=>change(file,'restore')));
      }else{
        const index=chosen.indexOf(file.name);
        const choose=button(index<0?'Seleccionar':`Seleccionado ${index+1}`,()=>{const pos=chosen.indexOf(file.name);if(pos<0)chosen.push(file.name);else chosen.splice(pos,1);render();});choose.setAttribute('aria-pressed',String(index>=0));actions.appendChild(choose);
        const favorite=button(file.favorite?'★ Favorito':'☆ Favorito',()=>change(file,'favorite',!file.favorite),file.inUse);favorite.setAttribute('aria-pressed',String(file.favorite));actions.appendChild(favorite);
        const trash=button('Papelera',()=>change(file,'trash'),file.favorite||file.inUse);trash.title=file.favorite?'Quitá favorito para moverlo a papelera':file.inUse?'Esperá a que termine el reel':'Recuperable durante 7 días';actions.appendChild(trash);
      }
      card.appendChild(actions);grid.appendChild(card);
    }
  }
  async function refresh(){
    if(loading)return;loading=true;
    try{const response=await fetch('/api/media');const data=await response.json();if(!response.ok)throw Error(data.error||'No pude cargar la galería.');snapshot=data;render();}
    catch(error){note(error.message);}finally{loading=false;}
  }
  async function change(file,action,value){
    try{const response=await fetch('/api/media/action',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({name:file.name,action,value})});const data=await response.json();if(!response.ok)throw Error(data.error||'No pude actualizar el archivo.');note(action==='trash'?'Archivo en papelera: podés recuperarlo durante 7 días.':action==='restore'?'Archivo restaurado. Si la galería estaba llena, el más antiguo disponible pasó a papelera.':'Favorito actualizado.');await refresh();await loadState(false);}
    catch(error){note(error.message);}
  }
  document.getElementById('gallery-use').addEventListener('click',()=>{
    for(const name of chosen){if(!selectedFiles.includes(name))selectedFiles.push(name);if(!reelDraftFiles.includes(name))reelDraftFiles.push(name);}
    saveReelDraft();updateSelectedBar();chosen.length=0;render();showPanelTab('tools');void loadState(false);
  });
  document.getElementById('gallery-refresh').addEventListener('click',refresh);
  filter.addEventListener('change',render);
  window.addEventListener('ariel:section',()=>{if(!document.getElementById('panel-gallery').hidden)void refresh();});
  setInterval(()=>{if(!document.getElementById('panel-gallery').hidden)void refresh();},15000);
})();
