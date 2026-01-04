import type { DirectInputPacket } from "./types";

export function packInput(
  index: number,
  input: DirectInputPacket
): ArrayBuffer {
  const buf = new Uint8Array(13);
  const dv = new DataView(buf.buffer);

  buf[0] = index & 0xff;

  // Buttons uint16
  let buttons = 0;
  if (input.A) buttons |= 1 << 0;
  if (input.B) buttons |= 1 << 1;
  if (input.X) buttons |= 1 << 2;
  if (input.Y) buttons |= 1 << 3;

  if (input.DPAD_UP) buttons |= 1 << 4;
  if (input.DPAD_DOWN) buttons |= 1 << 5;
  if (input.DPAD_LEFT) buttons |= 1 << 6;
  if (input.DPAD_RIGHT) buttons |= 1 << 7;

  if (input.L) buttons |= 1 << 8;
  if (input.R) buttons |= 1 << 9;
  if (input.ZL) buttons |= 1 << 10;
  if (input.ZR) buttons |= 1 << 11;

  if (input.PLUS) buttons |= 1 << 12;
  if (input.MINUS) buttons |= 1 << 13;
  if (input.HOME) buttons |= 1 << 14;
  if (input.CAPTURE) buttons |= 1 << 15;

  dv.setUint16(1, buttons, true);

  // JoyCon meta uint8
  let meta = 0;
  if (input.JCL_SR) meta |= 1 << 0;
  if (input.JCL_SL) meta |= 1 << 1;
  if (input.JCR_SR) meta |= 1 << 2;
  if (input.JCR_SL) meta |= 1 << 3;

  buf[3] = meta;

  // Stick press uint8
  let sticks = 0;
  if (input.L_STICK.PRESSED) sticks |= 1 << 0;
  if (input.R_STICK.PRESSED) sticks |= 1 << 1;

  buf[4] = sticks;

  // Sticks int16
  dv.setInt16(5, input.L_STICK.X_VALUE, true);
  dv.setInt16(7, input.L_STICK.Y_VALUE, true);
  dv.setInt16(9, input.R_STICK.X_VALUE, true);
  dv.setInt16(11, input.R_STICK.Y_VALUE, true);

  return buf.buffer;
}
