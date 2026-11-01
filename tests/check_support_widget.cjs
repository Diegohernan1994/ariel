const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync(require('node:path').join(__dirname,'../public/support-widget.js'),'utf8');
const elements=new Map();let tab='chat';const events={};let calls=[];
function element(id){if(!elements.has(id))elements.set(id,{hidden:true,value:'',textContent:'',children:[],events:{},setAttribute(k,v){this[k]=v},appendChild(p){this.children.push(p)},addEventListener(k,fn){this.events[k]=fn},focus(){this.focused=true}});return elements.get(id)}
const ctx={URL,ErrorEvent:class{},location:{href:'https://ariel.example.com/',origin:'https://ariel.example.com'},document:{getElementById:element,createElement:()=>({}),querySelector:()=>({id:'panel-tab-'+tab})},window:{addEventListener:(k,fn)=>events[k]=fn,fetch:async(url,opts)=>{calls.push({url,opts});return {ok:true,json:async()=>({reply:'Podés elegir varias fotos.',id:'fixture',message:'Guardado localmente, sin envío.'})};}}};
vm.createContext(ctx);vm.runInContext(source,ctx);
(async()=>{
 assert.equal(element('support-widget').hidden,true);
 tab='tools';events['ariel:section']();assert.equal(element('support-widget').hidden,false);
 element('support-toggle').events.click();assert.equal(element('support-box').hidden,false);
 element('support-input').value='¿Cómo armo un reel?';await element('support-form').events.submit({preventDefault(){}});
 assert.equal(calls[0].url,'/api/support/chat');assert.equal(JSON.parse(calls[0].opts.body).section,'tools');
 assert.equal(element('support-feed').children.at(-1).textContent,'Podés elegir varias fotos.');
 element('support-input').value='No termina el reel';await element('support-report').events.click();
 assert.equal(calls[1].url,'/api/support/report');assert.match(element('support-status').textContent,/sin envío/);
 tab='chat';events['ariel:section']();assert.equal(element('support-widget').hidden,true);assert.equal(element('support-box').hidden,true);
 console.log('OK: mascota fuera de chat, apertura y cierre, ayuda contextual y reporte local.');
})().catch(error=>{console.error(error);process.exitCode=1});
