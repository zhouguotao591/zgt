"""Stage execution callbacks on the deadline-aware Telegram receiver ASAR."""
import argparse
import hashlib
import json
import struct
from pathlib import Path
from urllib.parse import urlsplit


def stage(source, output, origin):
    url = urlsplit(origin)
    if url.scheme != 'https' or not url.hostname or url.path or url.query or url.fragment or url.username:
        raise ValueError('Expected an HTTPS site origin without a path')
    original = source.read_bytes()
    _, header_size, _, json_size = struct.unpack_from('<4I', original)
    header = json.loads(original[16:16 + json_size])
    payload = bytearray(original[8 + header_size:])
    files = header['files']['electron']['files']

    def patch(name, replacements):
        entry = files[name]
        start = int(entry['offset'])
        content = payload[start:start + entry['size']].decode()
        for before, after in replacements:
            if content.count(before) != 1:
                raise ValueError(f'Unexpected receiver version in {name}: {before}')
            content = content.replace(before, after)
        data = content.encode()
        entry.update(offset=str(len(payload)), size=len(data))
        if 'integrity' in entry:
            integrity = entry['integrity']
            block = integrity['blockSize']
            integrity.update(hash=hashlib.sha256(data).hexdigest(), blocks=[
                hashlib.sha256(data[i:i+block]).hexdigest() for i in range(0,len(data),block)])
        payload.extend(data)

    patch('storage.cjs',[('migrateMultiAccount(db);','''migrateMultiAccount(db);
   const pc28Columns=db.exec('PRAGMA table_info(tasks)')[0].values.map(r=>r[1]);
   for(const [column,type] of [['pc28_callback','TEXT'],['pc28_reported','TEXT'],['pc28_submitted_at','INTEGER']]){
     if(!pc28Columns.includes(column))db.run('ALTER TABLE tasks ADD COLUMN '+column+' '+type);
   }
''')])
    expected = json.dumps(origin+'/api/receiver/order-status')
    strict_enqueue = r'''
   if(data.execution_callback){
     const cb=data.execution_callback;
     if(!sourceToken||cb.version!==1||cb.url!==PC28_CALLBACK_URL||
       !Number.isSafeInteger(cb.platform_id)||cb.platform_id<1||!expiresAt||
       !Array.isArray(data.orders)||!data.orders.length||data.orders.length>100)
       throw Error('无效逐笔执行确认配置');
     if(!r.reply_pattern)throw Object.assign(Error('逐笔确认需要配置接收机器人的成功回复正则'),{statusCode:422});
     const numbers=new Set(),values=[],args=[];
     for(const order of data.orders){
       if(!order||!/^PC28-[1-9][0-9]{0,17}$/.test(order.order_no)||numbers.has(order.order_no)||
          !/^[大小单双][0-9]+(?:\.[0-9]{1,2})?$/.test(order.direction_amount))
          throw Error('无效或重复的订单号或订单金额');
       numbers.add(order.order_no);
       const signature=require('node:crypto').createHmac('sha256',sourceToken)
         .update([cb.platform_id,eventId,order.order_no].join('\n')).digest('hex');
       const metadata=JSON.stringify({url:cb.url,platform_id:cb.platform_id,event_id:eventId,order_no:order.order_no,signature});
       const itemEvent=eventId+':'+order.order_no;
       if(store.get('SELECT id FROM tasks WHERE source_id=? AND account_id=? AND event_id=?',[sourceId,accountId,itemEvent]))continue;
       const item=formatTemplate(r.template,{...(data.payload||{}),text:order.direction_amount});
       values.push('(?,?,?,?,?,?,?,?,?,?,?,?,?)');
       args.push(taskId(),accountId,sourceId,itemEvent,r.bot_id,r.id,item,'QUEUED',now,now,r.timeout_s,expiresAt,metadata);
     }
     if(values.length)store.run('INSERT INTO tasks(id,account_id,source_id,event_id,bot_id,rule_id,content,status,created_at,updated_at,timeout_s,expires_at,pc28_callback) VALUES '+values.join(','),args);
     this.notify('tasks');return {status:'QUEUED',account_id:accountId,order_count:numbers.size,duplicate:!values.length};
   }
'''
    report = r'''
 async reportPc28(){
   if(this.pc28Reporting||Date.now()<(this.pc28NextReport||0))return;
   const row=store.get("SELECT * FROM tasks WHERE pc28_callback IS NOT NULL AND status IN ('SUCCESS','FAILED','CANCELLED','UNKNOWN','REPLIED') AND COALESCE(pc28_reported,'')<>status ORDER BY updated_at,id LIMIT 1");
   if(!row)return;
   this.pc28Reporting=true;
   try{
     const cb=JSON.parse(row.pc28_callback);
     if(cb.url!==PC28_CALLBACK_URL)throw Error('callback destination mismatch');
     const status=({SUCCESS:'executed',FAILED:'failed',CANCELLED:'cancelled',UNKNOWN:'unknown',REPLIED:'unknown'})[row.status];
     const body={platform_id:cb.platform_id,event_id:cb.event_id,order_no:cb.order_no,status,
       detail:String(row.error||(row.status==='REPLIED'?'回复未匹配成功规则':'')).slice(0,500)};
     if(status==='executed')body.executed_at=Math.floor(row.pc28_submitted_at/1000);
     const response=await fetch(cb.url,{method:'POST',redirect:'error',signal:AbortSignal.timeout(8000),
       headers:{Authorization:'PC28-Order '+cb.signature,'Content-Type':'application/json'},body:JSON.stringify(body)});
     if(response.ok||response.status===409)
       store.run('UPDATE tasks SET pc28_reported=? WHERE id=? AND status=?',[row.status,row.id,row.status]);
     else this.pc28NextReport=Date.now()+15000;
   }catch{this.pc28NextReport=Date.now()+15000;}
   finally{this.pc28Reporting=false;}
 }
'''
    patch('engine.cjs',[
        (' enqueue(data,sourceId=0){',' enqueue(data,sourceId=0,sourceToken=null){'),
        ("json(200,{ok:true,telegram:this.tg.status(),paused:!!store.setting('paused')})",
         "json(200,{ok:true,telegram:this.tg.status(),paused:!!store.setting('paused'),order_execution_callbacks:1})"),
        ('const id=taskId();const now=Date.now();','const id=taskId();const now=Date.now();'+strict_enqueue),
        ('const result=this.enqueue(body,source.id);',"const result=this.enqueue(body,source.id,(req.headers.authorization||'').replace(/^Bearer\\s+/i,''));"),
        ('this.process().catch(e=>store.log','this.reportPc28().catch(()=>{});this.process().catch(e=>store.log'),
        (' start(){this.running=true;',report+' start(){this.running=true;'),
        ("status='SENT',sent_id=?,updated_at=? WHERE id=?\",[localId,Date.now(),task.id]",
         "status='SENT',sent_id=?,updated_at=?,pc28_submitted_at=? WHERE id=?\",[localId,Date.now(),Date.now(),task.id]"),
        ('module.exports=Engine;',f'const PC28_CALLBACK_URL={expected};\nmodule.exports=Engine;'),
    ])
    encoded = json.dumps(header,separators=(',',':'),ensure_ascii=False).encode()
    padding = (-len(encoded)) % 4
    header_body = struct.pack('<I',len(encoded))+encoded+b'\0'*padding
    pickle = struct.pack('<I',len(header_body))+header_body
    output.write_bytes(struct.pack('<II',4,len(pickle))+pickle+payload)


if __name__ == '__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('source',type=Path)
    parser.add_argument('output',type=Path)
    parser.add_argument('--origin',required=True)
    args=parser.parse_args()
    if args.source.resolve()==args.output.resolve():
        parser.error('Use a separate output file')
    stage(args.source,args.output,args.origin)
