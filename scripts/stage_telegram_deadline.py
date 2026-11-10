"""Build a deadline-aware Telegram Automation Console archive from the installed version."""
import hashlib
import json
from pathlib import Path
import struct
import sys


def stage(source, output):
    original = source.read_bytes()
    _, header_size, _, json_size = struct.unpack_from("<4I", original)
    header = json.loads(original[16:16 + json_size])
    payload = bytearray(original[8 + header_size:])
    files = header["files"]["electron"]["files"]

    def patch(path, changes):
        tree = files
        for component in path.split("/")[:-1]:
            tree = tree[component]["files"]
        entry = tree[path.split("/")[-1]]
        start = int(entry["offset"])
        content = payload[start:start + entry["size"]].decode()
        for old, new in changes:
            if content.count(old) != 1:
                raise ValueError(f"Unexpected installed version at {path}: {old}")
            content = content.replace(old, new)
        encoded = content.encode()
        entry.update(offset=str(len(payload)), size=len(encoded))
        if "integrity" in entry:
            integrity = entry["integrity"]
            block = integrity["blockSize"]
            integrity.update(hash=hashlib.sha256(encoded).hexdigest(), blocks=[
                hashlib.sha256(encoded[i:i + block]).hexdigest() for i in range(0, len(encoded), block)
            ])
        payload.extend(encoded)

    patch("storage.cjs", [("migrateMultiAccount(db);persist();",
        """migrateMultiAccount(db);
   if(!db.exec('PRAGMA table_info(tasks)')[0].values.some(row=>row[1]==='expires_at')){
     db.run('ALTER TABLE tasks ADD COLUMN expires_at INTEGER');
     db.run("UPDATE tasks SET status='FAILED',error='DRAW_DEADLINE_MISSING: 旧任务缺少本期截止时间，未发送',updated_at=? WHERE event_id LIKE 'pc28-batch-%' AND status IN ('QUEUED','BLOCKED_AUTH','BLOCKED_UI')",[Date.now()]);
   }
   persist();""")])
    patch("engine.cjs", [
        ("   const accountCount=", """    let expiresAt=null;
    if(data.expires_at!==undefined){
      if(!Number.isSafeInteger(data.expires_at)||!Number.isSafeInteger(data.draw_at)||
         !Number.isSafeInteger(data.target_draw)||data.target_draw<=0||
         data.expires_at>data.draw_at||!Number.isSafeInteger(data.expires_at*1000))
        throw Error('任务截止时间或目标期号无效');
      expiresAt=data.expires_at*1000;
      if(expiresAt<=Date.now())
        throw Object.assign(Error('DRAW_DEADLINE_EXPIRED: 超过本期有效期，未发送'),{statusCode:410});
    }
    const accountCount="""),
        ("content,status,created_at,updated_at,timeout_s) VALUES(?,?,?,?,?,?,?,?,?,?,?)',[id,accountId,sourceId,eventId,r.bot_id,r.id,content,'QUEUED',now,now,r.timeout_s]",
         "content,status,created_at,updated_at,timeout_s,expires_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',[id,accountId,sourceId,eventId,r.bot_id,r.id,content,'QUEUED',now,now,r.timeout_s,expiresAt]"),
        ("if(!t||!['BLOCKED_AUTH','BLOCKED_UI'].includes(t.status))throw Error('仅可恢复未提交的阻塞任务');",
         "if(!t||!['BLOCKED_AUTH','BLOCKED_UI'].includes(t.status))throw Error('仅可恢复未提交的阻塞任务');if(t.expires_at!=null&&t.expires_at<=Date.now())throw Error('超过本期有效期，不可恢复发送');"),
        ("     const ttl=", """      const drawExpired="status IN ('QUEUED','BLOCKED_AUTH','BLOCKED_UI') AND expires_at<=?";
      const checkedAt=Date.now();
      if(store.get("SELECT 1 FROM tasks WHERE "+drawExpired+" LIMIT 1",[checkedAt])){
        store.run("UPDATE tasks SET status='FAILED',error='DRAW_DEADLINE_EXPIRED: 超过本期有效期，未发送',updated_at=? WHERE "+drawExpired,[checkedAt,checkedAt]);
        this.notify('tasks');
      }
      const ttl="""),
        ("const result=await this.tg.send(task.username,task.content,task.account_id);",
         "const result=await this.tg.send(task.username,task.content,task.account_id,task.expires_at);"),
        ("}catch(e){const blocked=e.beforeSubmit",
         """}catch(e){if(e.code==='DRAW_DEADLINE_EXPIRED'){
        store.run("UPDATE tasks SET status='FAILED',error=?,updated_at=? WHERE id=?",['DRAW_DEADLINE_EXPIRED: 超过本期有效期，未发送',Date.now(),task.id]);
        this.notify('tasks');return;
      }const blocked=e.beforeSubmit"""),
        ("status,reply,error,created_at,updated_at FROM tasks WHERE id=? AND source_id=?",
         "status,reply,error,created_at,updated_at,expires_at FROM tasks WHERE id=? AND source_id=?"),
    ])
    patch("accounts.cjs", [
        ("async send(username,content,id)", "async send(username,content,id,expiresAt)"),
        ("return this.forAccount(id).send(username,content);", "return this.forAccount(id).send(username,content,expiresAt);"),
    ])
    patch("drivers/manager.cjs", [
        ("async send(username,content)", "async send(username,content,expiresAt)"),
        ("   try{return this.active==='web'?await this.web.send(username,content,{onBaseline:",
         """    if(expiresAt!=null&&expiresAt<=Date.now())throw Object.assign(Error('超过本期有效期，未发送'),{code:'DRAW_DEADLINE_EXPIRED'});
    try{return this.active==='web'?await this.web.send(username,content,{expiresAt,onBaseline:"""),
        ("await this.desktop.send(username,content);}catch(e){", "await this.desktop.send(username,content,expiresAt);}catch(e){if(e.code==='DRAW_DEADLINE_EXPIRED')throw e;"),
    ])
    patch("drivers/web.cjs", [
        ("async send(username,content,{onBaseline}={}){", """async send(username,content,{onBaseline,expiresAt}={}){
   const assertDeadline=()=>{
     if(expiresAt!=null&&expiresAt<=Date.now())
       throw Object.assign(Error('超过本期有效期，未发送'),{code:'DRAW_DEADLINE_EXPIRED'});
   };
   assertDeadline();"""),
        ("   await input.fill(content);", "   assertDeadline();\n   await input.fill(content);"),
        ("   try{if(submit)await submit.click({timeout:5000});else await input.press('Enter');}catch{throw uiError('SUBMISSION_UNKNOWN');}",
         """   if(expiresAt!=null&&expiresAt<=Date.now()){
     await input.evaluate((element,expected)=>{
       if(document.activeElement===element&&element.innerText===expected){
         element.textContent='';
         element.dispatchEvent(new InputEvent('input',{bubbles:true,inputType:'deleteContentBackward'}));
       }
     },content).catch(()=>{});
     assertDeadline();
   }
   const timeout=expiresAt==null?5000:Math.min(5000,expiresAt-Date.now());
   if(timeout<=0)assertDeadline();
   try{if(submit)await submit.click({timeout});else await input.press('Enter',{timeout});}catch{throw uiError('SUBMISSION_UNKNOWN');}"""),
    ])
    patch("drivers/desktop.cjs", [
        ("if(code!==0||!result.ok)done(Error(result.error||`Windows UI Automation error ${code}`));",
         "if(code!==0||!result.ok)done(Object.assign(Error(result.error||`Windows UI Automation error ${code}`),{code:result.code}));"),
        ("async send(username,content){const r=await this.request('send',{username,content},30000);",
         "async send(username,content,expiresAt){const r=await this.request('send',{username,content,expiresAt},30000);"),
    ])
    patch("windows/telegram-uia.ps1", [
        ("   [System.Windows.Forms.SendKeys]::SendWait('{ENTER}')",
         """   if ($null -ne $req.expiresAt -and [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() -ge [long]$req.expiresAt) {
     respond @{ok=$false;code='DRAW_DEADLINE_EXPIRED';error='Draw deadline passed before submit'}
     return
   }
   [System.Windows.Forms.SendKeys]::SendWait('{ENTER}')"""),
    ])
    encoded = json.dumps(header, ensure_ascii=False, separators=(",", ":")).encode()
    padding = b"\0" * (-len(encoded) % 4)
    size = len(encoded) + len(padding)
    output.write_bytes(struct.pack("<4I", 4, size + 8, size + 4, len(encoded)) + encoded + padding + payload)
    print("Source SHA256:", hashlib.sha256(original).hexdigest())


if __name__ == "__main__":
    stage(Path(sys.argv[1]), Path(sys.argv[2]))
