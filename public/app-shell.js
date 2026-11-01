'use strict';
(() => {
  const nav=document.getElementById('app-navigation'),menu=document.getElementById('mobile-menu'),backdrop=document.getElementById('navigation-backdrop'),content=document.getElementById('panel-content');
  const compact=window.matchMedia('(max-width:900px)');let opened=false;
  function sync(){
    const mobile=compact.matches,active=mobile&&opened;
    document.body.classList.toggle('navigation-open',active);
    backdrop.hidden=!active;menu.setAttribute('aria-expanded',String(active));
    nav.inert=mobile&&!active;content.inert=active;
    if(mobile&&!active)nav.setAttribute('aria-hidden','true');else nav.removeAttribute('aria-hidden');
    document.getElementById('navigation-close').hidden=!mobile;
  }
  function close(restore=false){opened=false;sync();if(restore&&compact.matches)menu.focus();}
  menu.addEventListener('click',()=>{opened=!opened;sync();if(opened)nav.querySelector('[aria-selected="true"]')?.focus();});
  document.getElementById('navigation-close').addEventListener('click',()=>close(true));
  backdrop.addEventListener('click',()=>close(true));
  compact.addEventListener('change',()=>{opened=false;sync();resize();});
  window.addEventListener('ariel:section',()=>{
    const tab=nav.querySelector('[role="tab"][aria-selected="true"]');
    document.getElementById('mobile-section-name').textContent=tab?.querySelector('span')?.textContent||'Ariel';
    const wasOpen=opened;close(false);
    if(wasOpen&&compact.matches){const panel=document.getElementById(tab?.getAttribute('aria-controls'));if(panel){panel.setAttribute('tabindex','-1');panel.focus({preventScroll:true});}}
  });
  document.addEventListener('keydown',event=>{
    if(!compact.matches||!opened)return;
    if(event.key==='Escape'){event.preventDefault();close(true);return;}
    if(event.key!=='Tab')return;
    const controls=[...nav.querySelectorAll('button:not([disabled]),a[href],input:not([disabled])')].filter(el=>!el.hidden&&el.getClientRects().length);
    const first=controls[0],last=controls.at(-1);
    if(event.shiftKey&&(document.activeElement===first||!nav.contains(document.activeElement))){event.preventDefault();last?.focus();}
    else if(!event.shiftKey&&(document.activeElement===last||!nav.contains(document.activeElement))){event.preventDefault();first?.focus();}
  });
  function resize(){
    if(window.visualViewport&&window.visualViewport.scale!==1)return;
    const height=window.visualViewport?.height||window.innerHeight;
    document.body.classList.toggle('compact-height',height<500);
    document.documentElement.style.setProperty('--app-height',`${Math.round(height)}px`);
  }
  window.visualViewport?.addEventListener('resize',resize);window.addEventListener('resize',resize);
  sync();resize();
})();
