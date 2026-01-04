let pc: RTCPeerConnection | null = null;
let channel: RTCDataChannel | null = null;

export async function initWebRTC(): Promise<void> {
  pc = new RTCPeerConnection({
    iceServers: [
      {
        urls: "stun:stun.l.google.com:19302"
      }, {
        urls: "turn:000.000.000.000:3478",
        username: "nuxbt",
        credential: "turnserverpw"
      }],
  });

  channel = pc.createDataChannel("input", {
    ordered: false,
    maxRetransmits: 0,
  });

  channel.binaryType = "arraybuffer";

  channel.onopen = () => {
    console.log("WebRTC DataChannel open");
  };

  channel.onerror = (e) => {
    console.error("DataChannel error", e);
  };

  const offer = await pc.createOffer();
  await pc.setLocalDescription(offer);

  const res = await fetch("/webrtc/offer", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      sdp: offer.sdp,
      type: offer.type,
    }),
  });

  const answer = await res.json();
  await pc.setRemoteDescription(answer);
}

export function sendInputPacket(packet: ArrayBuffer) {
  if (!channel || channel.readyState !== "open") return;
  channel.send(packet);
}
