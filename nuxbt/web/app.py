import os
import json
import asyncio
import pathlib
import pwd
from threading import RLock
from socket import gethostname
import struct
import hashlib
import logging

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from aiortc import RTCPeerConnection, RTCSessionDescription
import uvicorn

from .cert import generate_cert
from ..nuxbt import Nuxbt, PRO_CONTROLLER


app = FastAPI()
templates = Jinja2Templates(directory="nuxbt/web/templates")

nuxbt = None
pcs = set()
user_info_lock = RLock()
USER_INFO = {}


def get_config_dir():
    """
    Get the directory where nuxbt configuration is stored.
    Tries to store in the real user's home if running as root via sudo.
    """
    try:
        # If running as root via sudo, try to get the original user's home
        sudo_user = os.environ.get('SUDO_USER')
        if sudo_user:
            home = pwd.getpwnam(sudo_user).pw_dir
        else:
            home = str(pathlib.Path.home())
    except Exception:
        # Fallback to current user's home
        home = str(pathlib.Path.home())

    config_dir = os.path.join(home, ".config", "nuxbt")
    os.makedirs(config_dir, exist_ok=True)
    return config_dir


def get_macro_dir():
    """
    Get the directory where macros are stored.
    """
    macro_dir = os.path.join(get_config_dir(), "macros")
    os.makedirs(macro_dir, exist_ok=True)
    return macro_dir


def unpack_input(data: bytes):
    index = data[0]

    buttons = struct.unpack_from("<H", data, 1)[0]
    meta = data[3]
    sticks = data[4]

    lx, ly, rx, ry = struct.unpack_from("<hhhh", data, 5)

    packet = {
        "L_STICK": {
            "PRESSED": bool(sticks & 0x01),
            "X_VALUE": lx,
            "Y_VALUE": ly,
            "LS_UP": False,
            "LS_LEFT": False,
            "LS_RIGHT": False,
            "LS_DOWN": False,
        },
        "R_STICK": {
            "PRESSED": bool(sticks & 0x02),
            "X_VALUE": rx,
            "Y_VALUE": ry,
            "RS_UP": False,
            "RS_LEFT": False,
            "RS_RIGHT": False,
            "RS_DOWN": False,
        },

        "DPAD_UP": bool(buttons & (1 << 4)),
        "DPAD_DOWN": bool(buttons & (1 << 5)),
        "DPAD_LEFT": bool(buttons & (1 << 6)),
        "DPAD_RIGHT": bool(buttons & (1 << 7)),

        "L": bool(buttons & (1 << 8)),
        "R": bool(buttons & (1 << 9)),
        "ZL": bool(buttons & (1 << 10)),
        "ZR": bool(buttons & (1 << 11)),

        "PLUS": bool(buttons & (1 << 12)),
        "MINUS": bool(buttons & (1 << 13)),
        "HOME": bool(buttons & (1 << 14)),
        "CAPTURE": bool(buttons & (1 << 15)),

        "Y": bool(buttons & (1 << 3)),
        "X": bool(buttons & (1 << 2)),
        "B": bool(buttons & (1 << 1)),
        "A": bool(buttons & (1 << 0)),

        "JCL_SR": bool(meta & (1 << 0)),
        "JCL_SL": bool(meta & (1 << 1)),
        "JCR_SR": bool(meta & (1 << 2)),
        "JCR_SL": bool(meta & (1 << 3)),
    }

    return index, packet


def get_app_state():
    state_proxy = nuxbt.state.copy()
    state = {}

    for controller in state_proxy.keys():
        state[controller] = state_proxy[controller].copy()

    return state


def make_etag_and_payload(state):
    payload = json.dumps(
        state,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    etag = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return etag, payload


def log_filter():
    class EndpointFilter(logging.Filter):
        def filter(self, record):
            return (
                record.args
                and len(record.args) >= 3
                and (record.args[2] not in ["/state"] and record.args[-1] != 304)
            )
    logging.getLogger("uvicorn.access").addFilter(EndpointFilter())

log_filter()

app.mount("/static", StaticFiles(directory="nuxbt/web/static"), name="static")

@app.get("/")
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


def sanitize(s: str) -> str:
    return "".join(x for x in s if x.isalnum() or x in " -_")


@app.get("/api/macros")
async def list_macros():
    macro_dir = get_macro_dir()
    macros = {}

    if os.path.exists(macro_dir):
        root_macros = []
        for f in os.listdir(macro_dir):
            full = os.path.join(macro_dir, f)
            if os.path.isfile(full) and f.endswith(".txt"):
                root_macros.append(f[:-4])
            elif os.path.isdir(full):
                cat = f
                files = [
                    sf[:-4]
                    for sf in os.listdir(full)
                    if sf.endswith(".txt")
                ]
                if files:
                    macros[cat] = sorted(files)

        if root_macros:
            macros["Uncategorized"] = sorted(root_macros)

    return macros


@app.post("/api/macros")
async def save_macro(data: dict):
    name = data.get("name")
    category = data.get("category", "Uncategorized")
    content = data.get("macro")

    if not name or not content:
        return JSONResponse("Missing name or content", status_code=400)

    name = "".join(x for x in name if x.isalnum() or x in " -_")
    category = "".join(x for x in category if x.isalnum() or x in " -_")

    target = get_macro_dir()
    if category != "Uncategorized":
        target = os.path.join(target, category)

    os.makedirs(target, exist_ok=True)
    with open(os.path.join(target, f"{name}.txt"), "w") as f:
        f.write(content)

    return "Saved"


@app.post("/api/macro")
async def run_macro(data: dict):
    macro_id = nuxbt.macro(data["index"], data["macro"], block=False)
    return macro_id


@app.get("/api/macros/{name}")
async def get_macro_root(name: str):
    return await get_macro("Uncategorized", name)


@app.get("/api/macros/{category}/{name}")
async def get_macro(category, name):
    name = sanitize(name)
    category = sanitize(category)

    macro_dir = get_macro_dir()

    if category == "Uncategorized":
        p1 = os.path.join(macro_dir, f"{name}.txt")
        p2 = os.path.join(macro_dir, category, f"{name}.txt")
        file_path = p1 if os.path.exists(p1) else p2
    else:
        file_path = os.path.join(macro_dir, category, f"{name}.txt")

    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="Macro not found")

    with open(file_path, "r") as f:
        content = f.read()

    return JSONResponse({"macro": content})


@app.delete("/api/macros/{name}")
async def delete_macro_root(name):
    return await delete_macro("Uncategorized", name)


@app.delete("/api/macros/{category}/{name}")
async def delete_macro(category, name):
    name = sanitize(name)
    category = sanitize(category)

    macro_dir = get_macro_dir()
    did_delete = False

    if category == "Uncategorized":
        p1 = os.path.join(macro_dir, f"{name}.txt")
        p2 = os.path.join(macro_dir, category, f"{name}.txt")

        for p in (p1, p2):
            if os.path.exists(p):
                os.remove(p)
                did_delete = True
    else:
        p = os.path.join(macro_dir, category, f"{name}.txt")
        if os.path.exists(p):
            os.remove(p)
            did_delete = True

        cat_dir = os.path.join(macro_dir, category)
        if os.path.exists(cat_dir) and not os.listdir(cat_dir):
            os.rmdir(cat_dir)

    if not did_delete:
        raise HTTPException(status_code=404, detail="Macro not found")

    return PlainTextResponse("Deleted")


@app.post("/api/stop_all_macros")
async def stop_macros():
    if nuxbt:
        nuxbt.clear_all_macros()


@app.get("/api/keybinds")
async def get_keybinds():
    config_dir = get_config_dir()
    path = os.path.join(config_dir, "keybinds.json")

    if os.path.exists(path):
        try:
            with open(path, "r") as f:
                return JSONResponse(json.load(f))
        except Exception:
            pass

    return JSONResponse({})


@app.post("/api/keybinds")
async def save_keybinds(request: Request):
    config_dir = get_config_dir()
    path = os.path.join(config_dir, "keybinds.json")

    try:
        data = await request.json()
        with open(path, "w") as f:
            json.dump(data, f, indent=2)
        return PlainTextResponse("Saved")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/webrtc/offer")
async def webrtc_offer(params: dict):
    pc = RTCPeerConnection()
    pcs.add(pc)

    @pc.on("datachannel")
    def on_datachannel(channel):
        print("WebRTC channel:", channel.label)

        @channel.on("message")
        def on_message(message):
            if isinstance(message, bytes):
                index, packet = unpack_input(message)
                nuxbt.set_controller_input(index, packet)


    await pc.setRemoteDescription(
        RTCSessionDescription(
            sdp=params["sdp"],
            type=params["type"]
        )
    )

    answer = await pc.createAnswer()
    await pc.setLocalDescription(answer)

    return {
        "sdp": pc.localDescription.sdp,
        "type": pc.localDescription.type
    }


@app.post("/api/create_controller")
async def create_controller():
    reconnect = nuxbt.get_switch_addresses()
    index = nuxbt.create_controller(PRO_CONTROLLER, reconnect_address=reconnect)
    return {"index": index}


@app.post("/api/remove_controller")
async def remove_controller(data: dict):
    index = data.get("index")
    if index is not None:
        nuxbt.remove_controller(index)
    return "OK"


@app.get("/state")
async def get_state(request: Request):
    state = get_app_state()
    etag, payload = make_etag_and_payload(state)

    client_etag = request.headers.get("if-none-match")

    if client_etag == etag:
        return Response(status_code=304)

    return Response(
        content=payload,
        media_type="application/json",
        headers={
            "ETag": etag,
            "Cache-Control": "no-cache",
        },
    )


@app.on_event("shutdown")
async def shutdown():
    if nuxbt:
        for controller_index in nuxbt.manager_state.keys():
            nuxbt.remove_controller(controller_index)
    await asyncio.gather(*(pc.close() for pc in pcs))


def start_web_app(ip='0.0.0.0', port=8000, usessl=False, cert_path=None, debug=False):
    global nuxbt

    if nuxbt is None:
        nuxbt = Nuxbt(debug=debug)

    ssl_args = {}

    if usessl:
        config_dir = get_config_dir()
        cert_path = os.path.join(config_dir, "cert.pem")
        key_path = os.path.join(config_dir, "key.pem")

        if not os.path.exists(cert_path):
            print(
                "\n"
                "-----------------------------------------\n"
                "---------------->WARNING<----------------\n"
                "The NUXBT webapp is being run with self-\n"
                "signed SSL certificates for use on your\n"
                "local network.\n"
                "\n"
                "These certificates ARE NOT safe for\n"
                "production use. Please generate valid\n"
                "SSL certificates if you plan on using the\n"
                "NUXBT webapp anywhere other than your own\n"
                "network.\n"
                "-----------------------------------------\n"
                "\n"
                "The above warning will only be shown once\n"
                "on certificate generation."
                "\n"
            )
            print("Generating certificates...")
            cert, key = generate_cert(gethostname())
            with open(cert_path, "wb") as f:
                f.write(cert)
            with open(key_path, "wb") as f:
                f.write(key)

        ssl_args = {
            "ssl_certfile": cert_path,
            "ssl_keyfile": key_path
        }

    uvicorn.run(
        "nuxbt.web.app:app",
        host=ip,
        port=port,
        log_level="debug" if debug else "info",
        **ssl_args
    )
