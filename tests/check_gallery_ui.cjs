const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync(require('node:path').join(__dirname,'../public/gallery.js'),'utf8');
class Element{
 constructor(){this.children=[];this.events={};this.value='all';this.hidden=false;}
 appendChild(child){this.children.push(child)} replaceChildren(){this.children=[]}setAttribute(k,v){this[k]=v}addEventListener(k,fn){this.events[k]=fn}
}
const elements=new Map(),el=id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id)};
const files=[{name:'a.jpg',originalName:'Foto A',type:'image',url:'/uploads/a.jpg',size:20,favorite:false},{name:'b.mp4',originalName:'Video B',type:'video',url:'/uploads/b.mp4',size:40,favorite:true}];
const data={files,trash:[],activeCount:2,favoriteCount:1,trashCount:0,usedBytes:60,maxFiles:100,maxBytes:2048*1024**2};
let events={},section='',saved=0;
const ctx={document:{getElementById:el,createElement:()=>new Element()},window:{addEventListener:(n,f)=>events[n]=f},setInterval:()=>{},selectedFiles:[],reelDraftFiles:[],saveReelDraft:()=>saved++,updateSelectedBar:()=>{},showPanelTab:name=>section=name,loadState:async()=>{},fetch:async()=>({ok:true,json:async()=>data})};
vm.createContext(ctx);vm.runInContext(source,ctx);
(async()=>{
 await el('gallery-refresh').events.click();assert.equal(el('gallery-grid').children.length,2);
 const cards=el('gallery-grid').children;cards[1].children.at(-1).children[0].events.click();
 el('gallery-grid').children[0].children.at(-1).children[0].events.click();
 el('gallery-use').events.click();assert.deepEqual(ctx.selectedFiles,['b.mp4','a.jpg']);assert.deepEqual(ctx.reelDraftFiles,['b.mp4','a.jpg']);assert.equal(section,'tools');assert.equal(saved,1);
 assert.equal(el('gallery-grid').children[1].children.at(-1).children[2].disabled,true);
 el('gallery-filter').value='image';el('gallery-filter').events.change();assert.equal(el('gallery-grid').children.length,1);
 console.log('OK: galería separada, selección en orden, reutilización en borrador, filtro y protección de favoritos.');
})().catch(e=>{console.error(e);process.exitCode=1});
