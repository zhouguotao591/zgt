"""Write-only robot credential encrypted with Windows per-user DPAPI."""
import base64,ctypes,json,os
from pathlib import Path
from ctypes import wintypes

class Blob(ctypes.Structure):_fields_=[('size',wintypes.DWORD),('data',ctypes.POINTER(ctypes.c_ubyte))]

def protect(raw,decrypt=False):
 if os.name!='nt':raise ValueError('机器人密钥文件需要Windows DPAPI；当前平台不支持')
 data=ctypes.create_string_buffer(raw);source=Blob(len(raw),ctypes.cast(data,ctypes.POINTER(ctypes.c_ubyte)));target=Blob();crypt=ctypes.WinDLL('crypt32',use_last_error=True);kernel=ctypes.WinDLL('kernel32',use_last_error=True)
 crypt.CryptProtectData.argtypes=[ctypes.POINTER(Blob),wintypes.LPCWSTR,ctypes.POINTER(Blob),ctypes.c_void_p,ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(Blob)];crypt.CryptProtectData.restype=wintypes.BOOL
 crypt.CryptUnprotectData.argtypes=[ctypes.POINTER(Blob),ctypes.c_void_p,ctypes.POINTER(Blob),ctypes.c_void_p,ctypes.c_void_p,wintypes.DWORD,ctypes.POINTER(Blob)];crypt.CryptUnprotectData.restype=wintypes.BOOL
 kernel.LocalFree.argtypes=[ctypes.c_void_p];kernel.LocalFree.restype=ctypes.c_void_p
 ok=crypt.CryptUnprotectData(ctypes.byref(source),None,None,None,None,1,ctypes.byref(target)) if decrypt else crypt.CryptProtectData(ctypes.byref(source),'PC28 robot signal token',None,None,None,1,ctypes.byref(target))
 if not ok:raise ValueError('无法读取或加密机器人密钥，请使用运行服务的Windows账户重新保存')
 try:return ctypes.string_at(target.data,target.size)
 finally:kernel.LocalFree(target.data)

class SecretStore:
 def __init__(self,root):self.path=Path(root)/'data/private/robot-token.dpapi'
 def exists(self):return self.path.is_file()
 def write(self,token):
  if not isinstance(token,str) or not 16<=len(token)<=256 or any(c.isspace() for c in token) or not token.isascii():raise ValueError('密钥格式无效')
  encrypted=protect(token.encode());self.path.parent.mkdir(parents=True,exist_ok=True);temporary=self.path.with_suffix('.tmp');temporary.write_bytes(encrypted);temporary.replace(self.path)
 def read(self):
  if not self.exists():raise ValueError('请先配置机器人密钥')
  return protect(self.path.read_bytes(),True).decode()
