const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const html=fs.readFileSync(require('node:path').join(__dirname,'../public/index.html'),'utf8');
const start=html.indexOf('    let selectedFiles = [];'), fun=html.indexOf('    function finishReelDraft',start), end=html.indexOf('\n    }',fun)+6;
const build=html.slice(html.indexOf('    async function buildReelFromSelected'),html.indexOf('    async function clearChatHistory'));
let stored=null,success=true;
const grid={innerHTML:'old images'},button={innerHTML:'Armar',disabled:false};
const ctx={sessionStorage:{getItem:()=>stored,setItem:(_,v)=>stored=v},document:{getElementById:id=>id==='uploads-grid'?grid:id==='reel-transition'?{value:'none'}:button},updateSelectedBar:()=>{},loadState:async()=>{},alert:()=>{},fetch:async()=>({ok:success,json:async()=>({ok:success,error:'fixture'})})};
vm.createContext(ctx);vm.runInContext(html.slice(start,end)+build,ctx);
(async()=>{
 vm.runInContext("selectedFiles=['one.jpg'];reelDraftFiles=['one.jpg'];",ctx);
 success=false;await ctx.buildReelFromSelected();assert.equal(vm.runInContext('selectedFiles.length',ctx),1);assert.equal(grid.innerHTML,'old images');
 success=true;await ctx.buildReelFromSelected();assert.equal(vm.runInContext('selectedFiles.length',ctx),0);assert.equal(grid.innerHTML,'');assert.equal(stored,'[]');
 vm.runInContext("selectedFiles=['old.jpg','new.jpg'];reelDraftFiles=['old.jpg','new.jpg'];finishReelDraft(['old.jpg']);",ctx);
 assert.equal(vm.runInContext('selectedFiles[0]',ctx),'new.jpg');assert.equal(stored,'["new.jpg"]');
 console.log('OK: éxito vacía el borrador; fallo conserva archivos; las cargas nuevas no se pierden; el estado vacío persiste al recargar la pestaña.');
})().catch(e=>{console.error(e);process.exitCode=1});
