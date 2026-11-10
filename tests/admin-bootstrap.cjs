const fs=require('fs'),vm=require('vm'),assert=require('assert');
const source=fs.readFileSync('web/admin-bootstrap.js','utf8');
async function scenario(fail){
 const loaded=[],boxes=[],window={pc28Access:{},pc28AccessManagement:{},pc28HistoryReady:true,pc28RobotReady:true,pc28RobotAccounts:{},pc28AiAdminReady:true,pc28TrendReady:true,pc28SimulationRows(){},pc28AdminUsersRefresh(){},pc28Strategies:{refresh(){}}};let refreshes=0;
 const document={body:{dataset:{admin:'1'}},readyState:'complete',scripts:['app.js','profit-curve.js','admin.js','strategies.js','member-admin.js'].map(file=>({src:'https://admin.example/admin/assets/'+file+'?v=abc123'})),getElementById(){return boxes.find(b=>!b.removed)||null;},createElement(){return {setAttribute(){},remove(){this.removed=true;}};},querySelector(selector){return selector==='main'?{prepend(box){boxes.push(box);}}:{id:'admin-member-orders'};},head:{append(script){loaded.push(script.src);queueMicrotask(()=>{if(fail)script.onerror();else{window.pc28MemberAdmin={refreshOrders(){refreshes++;}};script.onload();}});}}};
 vm.runInNewContext(source,{document,window,api(){},table(){},URL,location:{href:'https://admin.example/admin'},console:{error(){}},Date,setTimeout,clearTimeout});
 for(let i=0;i<8;i++)await new Promise(resolve=>setImmediate(resolve));
 if(fail){assert.equal(loaded.length,2);assert.match(boxes[0].textContent,/member-admin.js/);assert.equal(refreshes,0);}
 else{assert.equal(loaded.length,1);assert.match(loaded[0],/v=abc123/);assert.match(loaded[0],/retry=/);assert.equal(refreshes,1);assert.equal(boxes[0].removed,true);}
}
(async()=>{await scenario(false);await scenario(true);console.log('PASS missing module recovery, active order refresh, version retention, bounded retry and visible failure');})().catch(e=>{console.error(e);process.exitCode=1;});
