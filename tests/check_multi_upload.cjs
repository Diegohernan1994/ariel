const fs = require('node:fs'), vm=require('node:vm'), assert=require('node:assert/strict');
const html=fs.readFileSync(require('node:path').join(__dirname,'../public/index.html'),'utf8');
assert.match(html,/<input[^>]+id="file-upload"[^>]+multiple/);
const body=html.slice(html.indexOf('    async function handleFilesUpload'),html.indexOf('    async function buildReelFromSelected'));
const selected=['existing.jpg'], calls=[], button={innerHTML:'Subir',disabled:false};
const ctx={reelDraftFiles:[],saveReelDraft:()=>{},selectedFiles:selected,document:{getElementById:()=>button},fileToBase64:async f=>'data:'+f.name,updateSelectedBar:()=>{},loadState:async()=>{},alert:m=>{throw Error(m)},fetch:async(url,opts)=>{
 const files=JSON.parse(opts.body).files; assert.equal(files.length,1); calls.push(files[0].name);
 return {ok:true,json:async()=>({ok:true,saved:[files[0].name]})};
}};
vm.createContext(ctx); vm.runInContext(body,ctx);
(async()=>{
 await ctx.handleFilesUpload({target:{files:[{name:'a.jpg',size:1},{name:'clip.mp4',size:2},{name:'b.jpg',size:3}],value:'files'}});
 assert.deepEqual(calls,['a.jpg','clip.mp4','b.jpg']); assert.deepEqual(selected,['existing.jpg','a.jpg','clip.mp4','b.jpg']); assert.equal(button.disabled,false);
 console.log('OK: selección múltiple, subida individual sin límite acumulado del lote, orden y selección previa conservados.');
})().catch(e=>{console.error(e);process.exitCode=1});
