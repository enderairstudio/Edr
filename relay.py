"""Thin Python API/UI adapter for the native Rust EDR relay engine."""
import json, os, re, secrets, string, shutil, tempfile, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib import error as urlerror
from urllib import request as urlrequest
from urllib.parse import urlparse

RELAY_PREFIX="Edrnko_"; DEFAULT_RELAY_URL=os.environ.get("EDR_RELAY_URL","http://127.0.0.1:8765"); CHUNK_SIZE=1024*1024; RELAY_ID_LENGTH=10
MAX_ROOM_BYTES=int(os.environ.get("EDR_RELAY_MAX_BYTES",10*1024**3)); ROOM_IDLE_SECONDS=int(os.environ.get("EDR_RELAY_IDLE_SECONDS",3600))
ROOM_ID_RE=re.compile(r"^[a-z0-9]{6,64}$"); _EMBEDDED_RELAY_SERVER=None
def generate_relay_id(length=RELAY_ID_LENGTH): return "".join(secrets.choice(string.ascii_lowercase+string.digits) for _ in range(length))
def valid_room_id(room): return isinstance(room,str) and bool(ROOM_ID_RE.fullmatch(room))
def relay_code(room): return f"{RELAY_PREFIX}{room}"
def parse_relay_remote(remote):
    room=(remote or "")[len(RELAY_PREFIX):] if (remote or "").startswith(RELAY_PREFIX) else ""
    return (room,remote) if valid_room_id(room) else (None,None)
def relay_base_url(): return DEFAULT_RELAY_URL.rstrip("/")

class RelayClient:
    def __init__(self,base_url=None): self.base_url=(base_url or relay_base_url()).rstrip("/")
    def _url(self,room,suffix=""):
        if not valid_room_id(room): raise ValueError("Invalid relay room id.")
        return f"{self.base_url}/v1/rooms/{room}"+(f"/{suffix}" if suffix else "")
    def _request(self,method,url,data=None,headers=None):
        try:
            with urlrequest.urlopen(urlrequest.Request(url,data=data,method=method,headers=headers or {}),timeout=120) as response:return response.read()
        except urlerror.HTTPError as err: raise RuntimeError(f"Relay error {err.code}: {err.read().decode(errors='replace')}") from err
        except urlerror.URLError as err: raise RuntimeError(f"Cannot reach relay at {self.base_url}. Start one with: edr relay start") from err
    def upload_file(self,room,path,on_progress=None):
        total=Path(path).stat().st_size; sent=0
        with Path(path).open("rb") as source:
            while sent<total or (not total and sent==0):
                chunk=source.read(CHUNK_SIZE) if total else b""; self._request("PUT",self._url(room),chunk,{"Content-Type":"application/octet-stream","X-EDR-Offset":str(sent),"X-EDR-Total":str(total)}); sent+=len(chunk)
                if on_progress:on_progress(100 if not total else int(sent*100/total))
                if not total:break
    def upload(self,room,data,on_progress=None):
        with tempfile.NamedTemporaryFile(delete=False) as f:f.write(data);path=Path(f.name)
        try:self.upload_file(room,path,on_progress)
        finally:path.unlink(missing_ok=True)
    def download_to_file(self,room,path,on_progress=None):
        with urlrequest.urlopen(self._url(room),timeout=120) as response,Path(path).open("wb") as target:
            total=int(response.headers.get("Content-Length","0") or 0);received=0
            while block:=response.read(CHUNK_SIZE):target.write(block);received+=len(block);on_progress and on_progress(int(received*100/total) if total else 99)
        if on_progress:on_progress(100)
    def download(self,room,on_progress=None):
        with tempfile.NamedTemporaryFile(delete=False) as f:path=Path(f.name)
        try:self.download_to_file(room,path,on_progress);return path.read_bytes()
        finally:path.unlink(missing_ok=True)
    def room_status(self,room):return json.loads(self._request("GET",self._url(room,"status")).decode())
    def wait_until_ready(self,room,timeout=600,poll_seconds=1):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            if self.room_status(room).get("ready"):return
            time.sleep(poll_seconds)
        raise TimeoutError(f"Relay room '{room}' was not ready before timeout.")
    def register_waiting(self,room):self._request("POST",self._url(room,"wait"),b"")
    def request_pull(self,room):self._request("POST",self._url(room,"request"),b"")
    def wait_for_pull_request(self,room,timeout=3600,poll_seconds=1,on_poll=None,on_poll_hit=None):
        deadline=time.monotonic()+timeout;shown=False
        while time.monotonic()<deadline:
            if self.room_status(room).get("requested"):return
            if on_poll and on_poll() and on_poll_hit and not shown:on_poll_hit();shown=True
            time.sleep(poll_seconds)
        raise TimeoutError(f"Timed out waiting for a pull on {relay_code(room)}.")
    def wait_until_consumed(self,room,timeout=600,poll_seconds=.5):
        deadline=time.monotonic()+timeout;saw=False
        while time.monotonic()<deadline:
            ready=self.room_status(room).get("ready");saw|=ready
            if saw and not ready:return
            time.sleep(poll_seconds)
        raise TimeoutError(f"Timed out waiting for receiver to finish downloading {relay_code(room)}.")
    def delete_room(self,room):self._request("DELETE",self._url(room))

class _RelayStore:
    """Bounded, disk-backed rooms; no relay payload is held in memory."""
    def __init__(self):self.lock=threading.Lock();self.rooms={};self.dir=Path(tempfile.mkdtemp(prefix="edr-relay-"))
    def _drop(self,room):
        old=self.rooms.pop(room,None)
        if old and old.get("path"):Path(old["path"]).unlink(missing_ok=True)
    def _clean(self):
        now=time.monotonic()
        for room,data in list(self.rooms.items()):
            if now-data["updated"]>ROOM_IDLE_SECONDS:self._drop(room)
    def wait(self,room):
        with self.lock:self._clean();self._drop(room);self.rooms[room]={"path":None,"total":0,"received":0,"ready":False,"requested":False,"waiting":True,"updated":time.monotonic()}
    def request(self,room):
        with self.lock:
            self._clean();data=self.rooms.setdefault(room,{"path":None,"total":0,"received":0,"ready":False,"requested":False,"waiting":False,"updated":time.monotonic()});data["requested"]=data["waiting"]=True;data["updated"]=time.monotonic()
    def put(self,room,offset,total,chunk):
        if total<0 or total>MAX_ROOM_BYTES or offset<0 or offset>total or len(chunk)>total-offset:raise ValueError("invalid chunk size")
        with self.lock:
            self._clean();data=self.rooms.setdefault(room,{"path":None,"total":0,"received":0,"ready":False,"requested":False,"waiting":False,"updated":time.monotonic()})
            if offset==0:
                requested,waiting=data["requested"],data["waiting"];self._drop(room);data={"path":str(self.dir/f"{room}-{secrets.token_hex(8)}.part"),"total":total,"received":0,"ready":False,"requested":requested,"waiting":waiting,"updated":time.monotonic()};self.rooms[room]=data
            if data["total"]!=total or data["ready"] or data["received"]!=offset:raise ValueError("chunks must be contiguous")
            with Path(data["path"]).open("ab") as output:output.write(chunk)
            data["received"]+=len(chunk);data["ready"]=data["received"]==total;data["updated"]=time.monotonic()
    # Kept for the testable relay-store API used by earlier EDR releases.
    def put_chunk(self,room,offset,total,chunk):self.put(room,offset,total,chunk)
    def status(self,room):
        with self.lock:
            self._clean();data=self.rooms.get(room)
            return {"ready":bool(data and data["ready"]),"requested":bool(data and data["requested"]),"waiting":bool(data and data["waiting"]),"bytes":data["received"] if data else 0}
    def take(self,room):
        with self.lock:
            self._clean();data=self.rooms.get(room)
            return (Path(data["path"]),data["total"]) if data and data["ready"] else None
    def delete(self,room):
        with self.lock:self._drop(room)
_STORE=_RelayStore()

class RelayHandler(BaseHTTPRequestHandler):
    protocol_version="HTTP/1.1"
    def log_message(self,*args):return
    def parts(self):
        values=urlparse(self.path).path.strip("/").split("/")
        return (values[2],values[3] if len(values)==4 else None) if len(values) in (3,4) and values[:2]==["v1","rooms"] and valid_room_id(values[2]) else (None,None)
    def send(self,code,body=b"",kind="text/plain"):
        self.send_response(code);self.send_header("Content-Type",kind);self.send_header("Content-Length",str(len(body)));self.end_headers()
        if body:self.wfile.write(body)
    def do_GET(self):
        if urlparse(self.path).path=="/v1/health":return self.send(200,b'{"ok":true}',"application/json")
        room,action=self.parts()
        if not room:return self.send(404,b"not found")
        if action=="status":return self.send(200,json.dumps(_STORE.status(room)).encode(),"application/json")
        if action:return self.send(404,b"not found")
        data=_STORE.take(room)
        if not data:return self.send(404,b"not ready")
        path,size=data
        try:
            self.send_response(200);self.send_header("Content-Type","application/octet-stream");self.send_header("Content-Length",str(size));self.end_headers()
            with path.open("rb") as source:shutil.copyfileobj(source,self.wfile,CHUNK_SIZE)
        finally:_STORE.delete(room)
    def do_POST(self):
        room,action=self.parts()
        if action=="wait":_STORE.wait(room);return self.send(204)
        if action=="request":_STORE.request(room);return self.send(204)
        self.send(404,b"not found")
    def do_PUT(self):
        room,action=self.parts()
        try:
            length=int(self.headers.get("Content-Length","-1"));offset=int(self.headers.get("X-EDR-Offset","-1"));total=int(self.headers.get("X-EDR-Total","-1"))
            if not room or action or length<0 or length>CHUNK_SIZE:raise ValueError("invalid upload")
            chunk=self.rfile.read(length)
            if len(chunk)!=length:raise ValueError("incomplete chunk")
            _STORE.put(room,offset,total,chunk)
        except ValueError as err:return self.send(400,str(err).encode())
        self.send(204)
    def do_DELETE(self):
        room,action=self.parts()
        if not room or action:return self.send(404,b"not found")
        _STORE.delete(room);self.send(204)
def start_relay_server(host="0.0.0.0",port=8765):
    server=ThreadingHTTPServer((host,port),RelayHandler);threading.Thread(target=server.serve_forever,daemon=True,name="edr-relay").start()
    return server,f"http://{host if host!='0.0.0.0' else '127.0.0.1'}:{server.server_address[1]}"
def _is_local(base):return urlparse((base or relay_base_url()).rstrip("/")).hostname in {"127.0.0.1","localhost","::1"}
def _health(base):
    try:RelayClient(base)._request("GET",f"{base.rstrip('/')}/v1/health");return True
    except RuntimeError:return False
def ensure_relay_available(base_url=None):
    global _EMBEDDED_RELAY_SERVER
    base=(base_url or relay_base_url()).rstrip("/")
    if _health(base):return base
    if not _is_local(base):raise RuntimeError(f"Cannot reach relay at {base}. Start one with: edr relay start")
    if _EMBEDDED_RELAY_SERVER is None:_EMBEDDED_RELAY_SERVER,base=start_relay_server(port=urlparse(base).port or 8765)
    for _ in range(50):
        if _health(base):return base
        time.sleep(.2)
    raise RuntimeError(f"Cannot reach relay at {base}.")
def register_waiting_room(room,base_url=None):RelayClient(base_url).register_waiting(room)
def request_pull(room,base_url=None):RelayClient(base_url).request_pull(room)
def wait_for_pull_request(room,timeout=3600,base_url=None,on_poll=None,on_poll_hit=None):RelayClient(base_url).wait_for_pull_request(room,timeout,on_poll=on_poll,on_poll_hit=on_poll_hit)
def upload_payload(room,data,on_progress=None,base_url=None):RelayClient(base_url).upload(room,data,on_progress)
def upload_file(room,path,on_progress=None,base_url=None):RelayClient(base_url).upload_file(room,path,on_progress)
def download_payload(room,on_progress=None,base_url=None):client=RelayClient(base_url);client.wait_until_ready(room);return client.download(room,on_progress)
def download_to_file(room,path,on_progress=None,base_url=None):client=RelayClient(base_url);client.wait_until_ready(room);return client.download_to_file(room,path,on_progress)
def wait_until_consumed(room,timeout=600,base_url=None):RelayClient(base_url).wait_until_consumed(room,timeout)
def delete_room(room,base_url=None):RelayClient(base_url).delete_room(room)
