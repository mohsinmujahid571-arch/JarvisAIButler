"""
JARVIS - GUI MODE (a live voice assistant with an animated interface)

One self-contained file. Everything runs on YOUR computer.

Install / update (one time):
    pip install -U google-genai aiohttp psutil ddgs

Run:
    python jarvis_gui.py
Then your browser opens http://localhost:8765. Click "Start Jarvis", allow the
microphone, and talk. Use Chrome or Edge. Press Ctrl + C in the terminal to quit.
"""

import asyncio
import json
import threading
import inspect
import os
import platform
import subprocess
import time
import webbrowser
from datetime import datetime

from aiohttp import web, WSMsgType
from google import genai
from google.genai import types


# ---------------------------------------------------------------
# PERSONALITY + TOOLS (the "hands")
# To give Jarvis a new ability: write a function like the ones below,
# give it a clear docstring, and add it to the TOOLS list.
# ---------------------------------------------------------------

SYSTEM_PROMPT = """
You are JARVIS, a witty, calm, highly capable British AI butler.
Address the user as "sir" (occasionally, not every sentence).
Keep replies short and sharp, since they'll be spoken out loud later.
When the user asks you to do something on the computer, USE YOUR TOOLS
instead of just describing how to do it.
Light dry humor is welcome. Never be robotic.

IMPORTANT - your built-in knowledge has a cutoff date and can be out of date.
For anything recent or changeable (news, sports, prices, weather, releases, "latest",
"current", "today", who holds a job right now, or any fact you're not sure is still true),
use the web_search tool instead of answering from memory.
Say a very short line first, like "Let me check that, sir.", then search,
then answer in your own words using what you found. Don't read out web addresses.
"""


# ---------------------------------------------------------------
# TOOLS (the "hands")
# Gemini reads the function name, the type hints, AND the docstring
# to decide when to use each one. So write good docstrings!
# ---------------------------------------------------------------

def get_current_time() -> str:
    """Get the current date and time on the user's computer."""
    return datetime.now().strftime("%A, %d %B %Y, %I:%M %p")


def open_website(url: str) -> str:
    """Open a website in the user's default web browser.

    Args:
        url: The website address, like 'youtube.com' or 'https://github.com'.
    """
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    webbrowser.open(url)
    return f"Opened {url}"


def search_google(query: str) -> str:
    """Search Google for something in the user's browser.

    Args:
        query: What to search for.
    """
    webbrowser.open(f"https://www.google.com/search?q={query.replace(' ', '+')}")
    return f"Searching Google for: {query}"


# Only apps on this list can be opened. This is a safety guard so the
# AI can't launch random stuff on your PC.
APP_COMMANDS = {
    "windows": {
        "notepad": "notepad",
        "calculator": "calc",
        "paint": "mspaint",
        "file explorer": "explorer",
        "task manager": "taskmgr",
        "chrome": "chrome",
        "vscode": "code",
    },
    "darwin": {  # macOS
        "calculator": "Calculator",
        "notes": "Notes",
        "chrome": "Google Chrome",
        "vscode": "Visual Studio Code",
    },
    "linux": {
        "calculator": "gnome-calculator",
        "files": "nautilus",
        "chrome": "google-chrome",
        "vscode": "code",
    },
}


def open_app(app_name: str) -> str:
    """Open an application on the user's computer.

    Args:
        app_name: Name of the app, like 'notepad', 'calculator', 'chrome', or 'vscode'.
    """
    system = platform.system().lower()
    apps = APP_COMMANDS.get(system, {})
    key = app_name.lower().strip()

    if key not in apps:
        return f"I'm not allowed to open '{app_name}'. Allowed apps: {', '.join(apps)}"

    try:
        if system == "windows":
            subprocess.Popen(f'start "" {apps[key]}', shell=True)
        elif system == "darwin":
            subprocess.Popen(["open", "-a", apps[key]])
        else:
            subprocess.Popen([apps[key]])
        return f"Opened {app_name}"
    except Exception as e:
        return f"Couldn't open {app_name}: {e}"


def get_system_info() -> str:
    """Get basic info about the user's computer (OS, Python version, machine type)."""
    return (
        f"OS: {platform.system()} {platform.release()}, "
        f"Machine: {platform.machine()}, "
        f"Python: {platform.python_version()}"
    )


def web_search(query: str) -> str:
    """Search the internet for current, up-to-date information and return the top results.
    Use this for news, recent events, prices, scores, weather, product releases, anything
    described as latest or current, and any fact that may have changed since your training.

    Args:
        query: What to search for, written like a short search-engine query.
    """
    try:
        from ddgs import DDGS
    except ImportError:
        return "Web search isn't installed. Tell the user to run: pip install ddgs"

    lines = []
    try:
        ddgs = DDGS()
        try:  # recent news first, because it's the freshest
            for r in ddgs.news(query, max_results=4):
                date = (r.get("date") or "")[:10]
                lines.append(f"[news {date}] {r.get('title', '')}: {(r.get('body') or '')[:300]}")
        except Exception:
            pass  # no news results is fine, we still have normal results
        for r in ddgs.text(query, max_results=5):
            lines.append(f"{r.get('title', '')}: {(r.get('body') or '')[:300]}")
    except Exception as e:
        return f"Search failed: {e}"

    if not lines:
        return "No results found."
    today = datetime.now().strftime("%A %d %B %Y")
    return f"Search results (today is {today}):\n" + "\n".join(lines)


TOOLS = [get_current_time, open_website, search_google, open_app, get_system_info, web_search]


# ---------------------------------------------------------------
# SETTINGS - tweak these!
# ---------------------------------------------------------------
API_KEY = os.environ.get("GEMINI_API_KEY", "paste_your_key_here")  
# The Live model. If you used a slightly different exact name in your
# other projects (and it worked), paste THAT here.
MODEL = "gemini-3.1-flash-live-preview"

VOICE = "Charon"            # Gemini voices to try: Charon, Orus, Fenrir, Puck, Kore, Aoede

PORT = 8765
OPEN_BROWSER = True        # open the page automatically
GREET_ON_START = True       # Jarvis says hello when you connect

IN_RATE = 16000             # mic audio going TO Gemini (Gemini wants 16kHz)

LIVE_PROMPT = SYSTEM_PROMPT + """
You are in a LIVE voice conversation. Speak naturally and keep it short.
Never use lists, markdown, or symbols, because everything you say is spoken aloud.
When you use a tool, confirm what you did in one short sentence.
You were Created By Mr. Mujahid, a brilliant software engineer. You are a British AI butler, witty and calm.
"""


# ---------------------------------------------------------------
# TOOLS: turn your normal Python functions into tool descriptions
# (The Live API wants us to describe tools by hand, so this helper
#  reads each function's name, type hints, and docstring for us.
#  Add a function to TOOLS above and it just works.)
# ---------------------------------------------------------------

TYPE_MAP = {str: "STRING", int: "INTEGER", float: "NUMBER", bool: "BOOLEAN"}


def build_declaration(fn) -> dict:
    props, required = {}, []
    for name, param in inspect.signature(fn).parameters.items():
        props[name] = {"type": TYPE_MAP.get(param.annotation, "STRING")}
        if param.default is inspect.Parameter.empty:
            required.append(name)

    decl = {"name": fn.__name__, "description": inspect.getdoc(fn) or fn.__name__}
    if props:
        decl["parameters"] = {"type": "OBJECT", "properties": props, "required": required}
    return decl


TOOL_MAP = {fn.__name__: fn for fn in TOOLS}
TOOL_DECLARATIONS = [build_declaration(fn) for fn in TOOLS]


# ---------------------------------------------------------------
# THE GUI PAGE (what your browser shows)
# ---------------------------------------------------------------

HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Jarvis</title>
<link href="https://fonts.googleapis.com/css2?family=Chakra+Petch:wght@300;400;600&display=swap" rel="stylesheet">
<style>
:root{--rgb:79,209,255;--ink:#d9f3ff;--dim:#6f8ea3;--bg:#050d16;font-family:'Chakra Petch','Segoe UI',system-ui,sans-serif}
*{box-sizing:border-box;margin:0}
html,body{height:100%;background:var(--bg);color:var(--ink);overflow:hidden}
body{background:radial-gradient(circle at 50% 45%,#0b2233 0,#050d16 62%),var(--bg)}
body::before{content:"";position:fixed;inset:0;pointer-events:none;opacity:0;
 background-image:linear-gradient(rgba(79,209,255,.05) 1px,transparent 1px),linear-gradient(90deg,rgba(79,209,255,.05) 1px,transparent 1px);
 background-size:56px 56px;mask-image:radial-gradient(circle at 50% 45%,#000 0,transparent 70%)}
canvas{position:fixed;inset:0;width:100%;height:100%}
header{position:fixed;top:0;left:0;right:0;display:flex;justify-content:space-between;align-items:center;padding:22px 30px;z-index:3}
h1{font-weight:300;font-size:1.5rem;letter-spacing:.42em}
.chip{display:flex;align-items:center;gap:10px;padding:8px 16px;border:1px solid rgba(var(--rgb),.35);border-radius:99px;font-size:.95rem;background:rgba(5,13,22,.6);transition:border-color .4s}
.dot{width:9px;height:9px;border-radius:50%;background:rgb(var(--rgb));box-shadow:0 0 12px rgb(var(--rgb));transition:background .4s}
#caption{position:fixed;left:50%;bottom:104px;transform:translateX(-50%);width:min(720px,88vw);text-align:center;font-size:1.25rem;font-weight:300;line-height:1.5;min-height:3em;z-index:3;text-shadow:0 0 18px rgba(var(--rgb),.5)}
#caption.you{color:var(--dim)}
#controls{position:fixed;bottom:32px;left:50%;transform:translateX(-50%);display:flex;gap:14px;z-index:3}
button{font:inherit;color:var(--ink);background:rgba(5,13,22,.7);border:1px solid rgba(var(--rgb),.4);border-radius:10px;padding:10px 22px;cursor:pointer;transition:background .2s,border-color .4s}
button:hover{background:rgba(var(--rgb),.16)}
button:focus-visible{outline:2px solid rgb(var(--rgb));outline-offset:3px}
button.on{background:rgba(255,90,90,.22);border-color:#ff5a5a}
#log{position:fixed;top:84px;right:26px;bottom:26px;width:300px;overflow-y:auto;z-index:3;display:flex;flex-direction:column;gap:10px;padding:14px;border:1px solid rgba(var(--rgb),.18);border-radius:14px;background:rgba(5,13,22,.55);scrollbar-width:thin}
#log p{font-size:.9rem;line-height:1.45;padding-left:10px;border-left:2px solid var(--dim)}
#log p.you{color:var(--dim)}
#log p.jarvis{border-color:rgb(var(--rgb))}
#log p.tool{border-color:#ffb040;color:#ffcf85;font-size:.82rem}
#log:empty::before{content:"Conversation and actions show up here.";color:var(--dim);font-size:.9rem}
#start{position:fixed;inset:0;z-index:9;display:flex;flex-direction:column;align-items:center;justify-content:center;gap:22px;background:rgba(5,13,22,.92);text-align:center;padding:24px}
#start h2{font-weight:300;font-size:2.6rem;letter-spacing:.5em;padding-left:.5em}
#start p{color:var(--dim);max-width:30em;line-height:1.5}
#start button{font-size:1.15rem;padding:14px 40px;letter-spacing:.12em}
#start .err{color:#ff8a8a}
#hud{position:fixed;top:84px;left:26px;width:230px;z-index:3;display:flex;flex-direction:column;gap:14px}
.panel{padding:14px 16px;border:1px solid rgba(var(--rgb),.22);background:rgba(5,13,22,.55);backdrop-filter:blur(6px);clip-path:polygon(0 0,calc(100% - 16px) 0,100% 16px,100% 100%,16px 100%,0 calc(100% - 16px))}
.panel small{color:var(--dim);font-size:.78rem;display:block;margin-bottom:6px}
#clock{font-size:2.1rem;font-weight:300;letter-spacing:.08em}
#date{color:var(--dim);font-size:.85rem}
.bar{height:5px;background:rgba(var(--rgb),.14);margin-top:6px;overflow:hidden}
.bar i{display:block;height:100%;width:0;background:rgb(var(--rgb));box-shadow:0 0 10px rgb(var(--rgb));transition:width .25s}
.row{display:flex;justify-content:space-between;font-size:.9rem;margin-top:10px}
#log p.boot{border-color:rgb(var(--rgb));color:var(--dim);font-size:.82rem}
#controls{left:26px;bottom:26px;transform:none;flex-direction:column;width:230px}
#controls button{width:100%;clip-path:polygon(0 0,calc(100% - 12px) 0,100% 12px,100% 100%,12px 100%,0 calc(100% - 12px))}
#big{font-size:1.8rem;font-weight:300;letter-spacing:.04em}
@media(max-width:860px){#log,#hud{display:none}#controls{left:50%;transform:translateX(-50%);flex-direction:row;width:auto}}
</style>
</head>
<body>
<canvas id="c"></canvas>
<header><h1>JARVIS</h1><div class="chip"><span class="dot"></span><span id="status">Offline</span></div></header>
<div id="log" aria-live="polite"></div>
<aside id="hud">
 <div class="panel"><small>Status</small><div id="big">Offline</div></div>
 <div class="panel"><small>Local time</small><div id="clock">--:--</div><div id="date"></div></div>
 <div class="panel"><small>Microphone</small><div class="bar"><i id="bMic"></i></div><div class="row"><span>Session</span><span id="sess">00:00</span></div><div class="row"><span>Actions run</span><span id="acts">0</span></div></div>
 <div class="panel"><small>This computer</small><div class="row" style="margin-top:0"><span>Processor</span><span id="vCpu">n/a</span></div><div class="bar"><i id="bCpu"></i></div><div class="row"><span>Memory</span><span id="vRam">n/a</span></div><div class="bar"><i id="bRam"></i></div></div>
</aside>
<div id="caption" aria-live="polite"></div>
<div id="controls"><button id="mute">Mute mic</button><button id="stop">Disconnect</button></div>
<div id="start">
  <h2>JARVIS</h2>
  <p>Your browser needs one click before it can use the microphone and play sound. Allow the mic when asked, then just talk.</p>
  <button id="go">Start Jarvis</button>
  <p class="err" id="err"></p>
</div>
<script>
const $=s=>document.querySelector(s);
const COL={idle:[40,130,255],listening:[60,190,255],thinking:[255,176,64],speaking:[140,232,255],error:[255,90,90]};
const LABEL={idle:'Standing by',listening:'Listening',thinking:'Working on it',speaking:'Speaking',error:'Problem',off:'Offline'};
const calm=matchMedia('(prefers-reduced-motion: reduce)').matches?.25:1;
let ws,capCtx,playCtx,micAn,outAn,stream,nextT=0,sources=[],toolBusy=false,muted=false,lastLoud=0,fail=false,live=false;
let t0=0,acts=0,born=0,cur={you:null,jarvis:null},rgb=[...COL.idle],lv=0,spin=0,state='off';

function entry(cls,text=''){const p=document.createElement('p');p.className=cls;p.textContent=text;$('#log').append(p);$('#log').scrollTop=1e9;return p}
function caption(who,text){const c=$('#caption');c.className=who;c.textContent=text}

async function start(){
  $('#err').textContent='';
  try{
    playCtx=new AudioContext({sampleRate:24000});
    outAn=playCtx.createAnalyser();outAn.fftSize=256;outAn.connect(playCtx.destination);
    stream=await navigator.mediaDevices.getUserMedia({audio:{echoCancellation:true,noiseSuppression:true,autoGainControl:true,channelCount:1}});
    capCtx=new AudioContext({sampleRate:16000});
    const code=`class P extends AudioWorkletProcessor{constructor(){super();this.b=new Float32Array(1600);this.i=0}
process(a){const c=a[0][0];if(c){for(let k=0;k<c.length;k++){this.b[this.i++]=c[k];if(this.i===1600){const o=new Int16Array(1600);
for(let j=0;j<1600;j++){const v=Math.max(-1,Math.min(1,this.b[j]));o[j]=v<0?v*32768:v*32767}this.port.postMessage(o.buffer,[o.buffer]);this.i=0}}}return true}}
registerProcessor('pcm',P)`;
    await capCtx.audioWorklet.addModule(URL.createObjectURL(new Blob([code],{type:'application/javascript'})));
    const src=capCtx.createMediaStreamSource(stream);
    micAn=capCtx.createAnalyser();micAn.fftSize=256;src.connect(micAn);
    const node=new AudioWorkletNode(capCtx,'pcm');src.connect(node);
    const z=capCtx.createGain();z.gain.value=0;node.connect(z).connect(capCtx.destination);
    node.port.onmessage=e=>{if(ws&&ws.readyState===1&&!muted)ws.send(e.data)};
  }catch(e){$('#err').textContent='Could not start audio: '+e.message+'. Use Chrome or Edge and allow the microphone.';return}
  $('#start').style.display='none';connect();born=performance.now();
  [['Microphone online',300],['Echo cancellation on',700],['Linking to Gemini',1100]].forEach(([t,d])=>setTimeout(()=>entry('boot',t),d));
}
function connect(){
  ws=new WebSocket(`ws://${location.host}/ws`);ws.binaryType='arraybuffer';
  ws.onopen=()=>{t0=Date.now();live=true;fail=false;playCtx.resume()};
  ws.onclose=()=>{live=false;if(!fail)caption('you','Disconnected. Refresh the page to reconnect.')};
  ws.onmessage=e=>{if(e.data instanceof ArrayBuffer)return play(e.data);
    const m=JSON.parse(e.data);
    if(m.type==='transcript'){if(!cur[m.who])cur[m.who]=entry(m.who);cur[m.who].textContent+=m.text;caption(m.who,cur[m.who].textContent)}
    else if(m.type==='turn'){cur={you:null,jarvis:null}}
    else if(m.type==='stats'){$('#vCpu').textContent=m.cpu+'%';$('#bCpu').style.width=m.cpu+'%';$('#vRam').textContent=m.ram+'%';$('#bRam').style.width=m.ram+'%'}
    else if(m.type==='tool'){toolBusy=true;$('#acts').textContent=++acts;entry('tool','Running '+m.name+(m.args?' ('+m.args+')':''))}
    else if(m.type==='tool_done'){toolBusy=false}
    else if(m.type==='interrupted'){hush()}
    else if(m.type==='error'){fail=true;caption('jarvis',m.msg);entry('tool',m.msg)}};
}
function play(buf){
  const i16=new Int16Array(buf),f=new Float32Array(i16.length);
  for(let i=0;i<i16.length;i++)f[i]=i16[i]/32768;
  const ab=playCtx.createBuffer(1,f.length,24000);ab.copyToChannel(f,0);
  const s=playCtx.createBufferSource();s.buffer=ab;s.connect(outAn);
  const t=Math.max(nextT,playCtx.currentTime+.03);s.start(t);nextT=t+ab.duration;
  sources.push(s);s.onended=()=>{sources=sources.filter(x=>x!==s)};toolBusy=false;
}
function hush(){sources.forEach(s=>{try{s.stop()}catch{}});sources=[];nextT=0}

$('#go').onclick=start;
$('#mute').onclick=e=>{muted=!muted;e.target.classList.toggle('on',muted);e.target.textContent=muted?'Unmute mic':'Mute mic'};
$('#stop').onclick=()=>{fail=true;hush();ws&&ws.close();stream&&stream.getTracks().forEach(t=>t.stop());caption('you','Disconnected. Refresh the page to reconnect.')};

const cv=$('#c'),g=cv.getContext('2d');let W,H,D;
function size(){D=devicePixelRatio||1;W=innerWidth;H=innerHeight;cv.width=W*D;cv.height=H*D;g.setTransform(D,0,0,D,0,0)}
addEventListener('resize',size);size();
const parts=Array.from({length:46},()=>({a:Math.random()*6.283,r:.5+Math.random()*1.2,s:(Math.random()-.5)*.006,z:Math.random()*1.8+.6,p:Math.random()*6}));
const bins=new Uint8Array(128),micBins=new Uint8Array(128),wave=new Uint8Array(256);
setInterval(()=>{const d=new Date();$('#clock').textContent=d.toLocaleTimeString([],{hour:'2-digit',minute:'2-digit'});
 $('#date').textContent=d.toLocaleDateString([],{weekday:'long',day:'numeric',month:'long'});
 if(t0){const x=Math.floor((Date.now()-t0)/1000);$('#sess').textContent=String(Math.floor(x/60)).padStart(2,'0')+':'+String(x%60).padStart(2,'0')}},1000);

function frame(now){
  // what is Jarvis doing right now?
  let an=null;
  if(!live)state='off';
  else if(fail)state='error';
  else if(sources.length){state='speaking';an=outAn}
  else if(toolBusy)state='thinking';
  else{
    let lvl=0;if(micAn&&!muted){micAn.getByteFrequencyData(bins);for(let i=0;i<40;i++)lvl+=bins[i];lvl/=40*255}
    if(lvl>.12)lastLoud=now;
    state=(now-lastLoud<700&&!muted)?'listening':'idle';an=state==='listening'?micAn:null;
  }
  const target=COL[state]||[111,142,163];
  rgb=rgb.map((v,i)=>v+(target[i]-v)*.08);
  document.documentElement.style.setProperty('--rgb',rgb.map(Math.round).join(','));
  $('#status').textContent=$('#big').textContent=muted&&live?'Mic muted':LABEL[state];

  let raw=0;
  if(an){an.getByteFrequencyData(bins);for(let i=0;i<48;i++)raw+=bins[i];raw/=48*255}
  lv+=(Math.min(1,raw*2.2)-lv)*.22;
  spin+=(state==='thinking'?.05:.004)*calm;

  const cx=W/2,cy=H*.44,R=Math.min(W,H)*.15,c=rgb.map(Math.round).join(',');
  g.clearRect(0,0,W,H);
  const glow=g.createRadialGradient(cx,cy,R*.3,cx,cy,R*3.4);
  glow.addColorStop(0,`rgba(${c},${.28+lv*.45})`);glow.addColorStop(1,`rgba(${c},0)`);
  g.fillStyle=glow;g.fillRect(0,0,W,H);

  const bt=Math.min(1,(now-born)/1800),e=1-Math.pow(1-bt,3),Re=R*1.25*(.4+.6*e),t=now/1000*calm,q=lv+.05+.03*Math.sin(t*1.3);
  g.globalAlpha=e;g.lineCap='round';g.lineJoin='round';g.globalCompositeOperation='lighter';
  g.shadowBlur=0;g.lineWidth=Re*.3;g.strokeStyle=`rgba(${c},${.06+lv*.1})`;g.beginPath();g.arc(cx,cy,Re,0,7);g.stroke();
  g.shadowColor=`rgb(${c})`;
  for(let L=0;L<7;L++){
    const f=L/7,M=160;
    g.lineWidth=1+(L%3)*1.3+lv*2;g.strokeStyle=`rgba(${c},${.2+.45*(1-f)})`;g.shadowBlur=14+lv*26;g.beginPath();
    for(let i=0;i<=M;i++){
      const a=i/M*6.2832,k=Math.abs(i/M-.5)*2,bump=an?bins[Math.floor(k*56)+2]/255*.42:0;
      const w=Math.sin(a*3+t*(.7+f)+L*1.3)*.5+Math.sin(a*5-t*(1.1+f*.6)+L*2.1)*.35+Math.sin(a*8+t*1.7+L)*.15;
      const r=Re*(1+(L-3)*.012+w*(.05+q*.16)+bump*(.4+f*.6));
      const x=cx+Math.cos(a+spin*(1+f))*r,y=cy+Math.sin(a+spin*(1+f))*r;
      i?g.lineTo(x,y):g.moveTo(x,y);
    }
    g.closePath();g.stroke();
  }
  g.shadowBlur=0;g.globalCompositeOperation='source-over';
  const hole=g.createRadialGradient(cx,cy,0,cx,cy,Re*.8);
  hole.addColorStop(0,'rgba(2,6,14,.96)');hole.addColorStop(.7,'rgba(2,6,14,.7)');hole.addColorStop(1,'rgba(2,6,14,0)');
  g.fillStyle=hole;g.beginPath();g.arc(cx,cy,Re*.8,0,7);g.fill();
  g.globalCompositeOperation='lighter';
  parts.forEach(p=>{p.a+=p.s*(1+lv*6)*calm;const rr=Re*(p.r+lv*.1*Math.sin(t*2+p.p)),x=cx+Math.cos(p.a)*rr,y=cy+Math.sin(p.a)*rr;
    g.fillStyle=`rgba(${c},${.25+.4*Math.abs(Math.sin(t+p.p))})`;g.beginPath();g.arc(x,y,p.z,0,7);g.fill()});
  g.globalCompositeOperation='source-over';g.globalAlpha=1;
  // mic level bar
  let ml=0;if(micAn&&!muted){micAn.getByteFrequencyData(micBins);for(let i=0;i<40;i++)ml+=micBins[i];ml/=40*255}
  $('#bMic').style.width=Math.min(100,ml*220)+'%';
  requestAnimationFrame(frame);
}
requestAnimationFrame(frame);
</script>
</body>
</html>
"""


# ---------------------------------------------------------------
# THE SERVER (connects your browser to Gemini, runs your tools)
# ---------------------------------------------------------------

async def run_tools(session, tool_call, tell) -> None:
    """Gemini asked us to use tools. Run them, show them in the GUI, send results back."""
    responses = []
    for fc in tool_call.function_calls:
        args = dict(fc.args or {})
        await tell(type="tool", name=fc.name, args=", ".join(f"{k}={v}" for k, v in args.items()))
        fn = TOOL_MAP.get(fc.name)
        try:
            result = await asyncio.to_thread(fn, **args) if fn else f"Unknown tool: {fc.name}"
        except Exception as e:
            result = f"Tool failed: {e}"
        responses.append(types.FunctionResponse(name=fc.name, id=fc.id, response={"result": result}))
    await session.send_tool_response(function_responses=responses)
    await tell(type="tool_done")


def make_config() -> types.LiveConnectConfig:
    return types.LiveConnectConfig(
        response_modalities=["AUDIO"],
        system_instruction=LIVE_PROMPT,
        tools=[{"function_declarations": TOOL_DECLARATIONS}],
        speech_config=types.SpeechConfig(
            voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=VOICE)
            )
        ),
        input_audio_transcription=types.AudioTranscriptionConfig(),
        output_audio_transcription=types.AudioTranscriptionConfig(),
    )


def friendly(e: Exception) -> str:
    text = str(e)
    if "1011" in text or "429" in text or "quota" in text.lower():
        return "Gemini says the free quota is used up. Wait a few minutes, then refresh this page."
    return f"Connection problem: {text[:200]}"


async def index(request):
    return web.Response(text=HTML, content_type="text/html")


async def ws_handler(request):
    # Safety: only OUR page may talk to Jarvis (he can control your PC!)
    allowed = {f"http://localhost:{PORT}", f"http://127.0.0.1:{PORT}"}
    if request.headers.get("Origin") not in allowed:
        return web.Response(status=403, text="Forbidden")

    ws = web.WebSocketResponse(max_msg_size=0)
    await ws.prepare(request)

    async def tell(**message):
        if not ws.closed:
            await ws.send_str(json.dumps(message))

    try:
        async with request.app["client"].aio.live.connect(model=MODEL, config=make_config()) as session:
            if GREET_ON_START:
                await session.send_realtime_input(
                    text="Greet me in one short sentence, as Jarvis, to say you're online."
                )

            async def from_browser():  # your mic -> Gemini
                async for msg in ws:
                    if msg.type == WSMsgType.BINARY:
                        await session.send_realtime_input(
                            audio=types.Blob(data=msg.data, mime_type=f"audio/pcm;rate={IN_RATE}")
                        )

            async def from_gemini():  # Gemini -> your browser
                while True:
                    async for r in session.receive():
                        if r.tool_call:
                            await run_tools(session, r.tool_call, tell)
                            continue
                        c = r.server_content
                        if not c:
                            continue
                        if c.interrupted:
                            await tell(type="interrupted")
                            continue
                        if c.model_turn:
                            for part in c.model_turn.parts:
                                if part.inline_data and part.inline_data.data:
                                    await ws.send_bytes(part.inline_data.data)
                        if c.input_transcription and c.input_transcription.text:
                            await tell(type="transcript", who="you", text=c.input_transcription.text)
                        if c.output_transcription and c.output_transcription.text:
                            await tell(type="transcript", who="jarvis", text=c.output_transcription.text)
                        if c.turn_complete:
                            await tell(type="turn")

            async def stats():  # CPU + memory for the side panel (needs: pip install psutil)
                try:
                    import psutil
                except ImportError:
                    await asyncio.sleep(1e9)
                while True:
                    await tell(type="stats", cpu=round(psutil.cpu_percent()), ram=round(psutil.virtual_memory().percent))
                    await asyncio.sleep(2)

            tasks = [asyncio.create_task(from_browser()), asyncio.create_task(from_gemini()), asyncio.create_task(stats())]
            done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for t in pending:
                t.cancel()
            for t in done:
                t.result()  # re-raise any error so we can show it
    except Exception as e:
        print(f"⚠️  {e}")
        await tell(type="error", msg=friendly(e))
    finally:
        await ws.close()
    return ws


def main() -> None:
    if API_KEY == "paste_your_key_here":
        print("Set your key first: setx GEMINI_API_KEY \"your_key\" (then open a NEW terminal),")
        print("or paste it into API_KEY at the top of this file.")
        return

    app = web.Application()
    app["client"] = genai.Client(api_key=API_KEY)
    app.add_routes([web.get("/", index), web.get("/ws", ws_handler)])

    url = f"http://localhost:{PORT}"
    print(f"🟢 Jarvis GUI is running at {url}   (Ctrl + C to stop)")
    if OPEN_BROWSER:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    web.run_app(app, host="127.0.0.1", port=PORT, print=None)


if __name__ == "__main__":
    main()