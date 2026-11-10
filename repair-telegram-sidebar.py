"""Stage a layout-only fix for the installed Telegram Automation Console."""
import hashlib
import json
import os
from pathlib import Path
import struct

source = Path(os.environ["LOCALAPPDATA"]) / "Programs/Telegram Automation Console UI/resources/app.asar"
stage = Path(__file__).resolve().parent / "data/telegram-sidebar-fix"
stage.mkdir(parents=True, exist_ok=True)
original = source.read_bytes()
_, header_size, _, json_size = struct.unpack_from("<4I", original)
header = json.loads(original[16:16 + json_size])
payload = original[8 + header_size:]
drivers = header["files"]["electron"]["files"]["drivers"]["files"]

def read_entry(name):
    entry = drivers[name]
    offset = int(entry["offset"])
    return payload[offset:offset + entry["size"]]

old = read_entry("web-search.cjs").decode()
start = old.index("async function searchBox(page){")
end = old.index("async function resultIdentity", start)
fixed = old[:start] + '''async function searchBox(page){
 const search=page.locator(SEARCH);
 async function usableSearch(){
  const visible=await visibleIndices(search);
  if(visible.length>1)throw Object.assign(uiError('WEB_SEARCH_AMBIGUOUS'),{searchStage:'input',searchRows:visible.length});
  return visible.length===1?search.nth(visible[0]):null;
 }
 const recoveryButtons=[
  '#column-right .sidebar-close-button:visible, .sidebar-right .sidebar-close-button:visible',
  '#column-left .sidebar-back-button:visible, #column-left .input-search-back:visible, #column-center .topbar .sidebar-close-button:visible',
 ];
 for(let step=0;step<4;step++){
  const input=await usableSearch();
  if(input)return input;
  let moved=false;
  for(const selector of recoveryButtons){
   const buttons=page.locator(selector);
   const usable=await visibleIndices(buttons);
   if(!usable.length)continue;
   try{await buttons.nth(usable[0]).click({timeout:800});moved=true;break;}catch{}
  }
  if(!moved)break;
  await page.waitForTimeout(250);
 }
 const input=await usableSearch();
 if(input)return input;
 // Reset Telegram's panel stack before typing anything. An existing chat
 // hash can leave the left column offscreen even in a desktop viewport.
 const home='https://web.telegram.org/k/';
 if(page.url().startsWith(home)){
  try{
   const destination=new URL(home);
   const current=new URL(page.url());
   if(current.pathname===destination.pathname&&!current.search){
    if(current.hash)await page.goto(home,{waitUntil:'commit',timeout:5000});
    await page.reload({waitUntil:'domcontentloaded',timeout:8000});
   }else await page.goto(home,{waitUntil:'domcontentloaded',timeout:8000});
  }catch{}
  for(let attempt=0;attempt<20&&!page.isClosed();attempt++){
   const recovered=await usableSearch();
   if(recovered)return recovered;
   await page.waitForTimeout(250);
  }
 }
 throw uiError('WEB_SEARCH_MISSING');
}
''' + old[end:]
replacement = fixed.encode()
entry = drivers["web-search.cjs"]
entry.update(offset=str(len(payload)), size=len(replacement))
if "integrity" in entry:
    integrity = entry["integrity"]
    block = integrity["blockSize"]
    digest = lambda data: hashlib.sha256(data).hexdigest()
    integrity.update(hash=digest(replacement), blocks=[
        digest(replacement[i:i + block]) for i in range(0, len(replacement), block)
    ])
encoded = json.dumps(header, ensure_ascii=False, separators=(",", ":")).encode()
padding = b"\0" * (-len(encoded) % 4)
size = len(encoded) + len(padding)
result = struct.pack("<4I", 4, size + 8, size + 4, len(encoded)) + encoded + padding + payload + replacement
(stage / "app.asar").write_bytes(result)
(stage / "web-search.cjs").write_bytes(replacement)
(stage / "web-ui-errors.cjs").write_bytes(read_entry("web-ui-errors.cjs"))
(stage / "original-sha256.txt").write_text(hashlib.sha256(original).hexdigest(), encoding="ascii")
print(stage)
