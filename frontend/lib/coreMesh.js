// Shared 3D core-cylinder building blocks: the unit meshes, image loading and
// the segment maths. HoleMap.jsx (cores on the satellite map) and Hole3D.jsx
// (the core viewer) both use them, so the geometry never drifts apart.

import { CylinderGeometry } from "@luma.gl/engine";

// One unit cylinder (radius 1, height 1, centred on its local origin, axis
// along local Y), instanced per segment via SimpleMeshLayer's getTransformMatrix.
export const CORE_MESH = new CylinderGeometry({ radius: 1, height: 1, nradial: 16, topCap: true, bottomCap: true });

// The same cylinder for wrapping a core photo round. A tray photo looks
// straight down on the core, so its width spans the core's DIAMETER, not its
// circumference: wrapping it once round (the default texture coordinates)
// stretches the rock ~3x sideways. Instead every vertex takes the photo column
// directly above it, u = (1 - x) / 2 - seen face-on (Hole3D's default camera)
// the cylinder looks exactly like the photo, labels reading the right way
// round (checked against a "250 m" core block); its back shows the mirror image.
export const PHOTO_CORE_MESH = (() => {
  const shape = { radius: 1, height: 1, nradial: 48, nvertical: 1, topCap: true, bottomCap: true };
  const { POSITION, TEXCOORD_0 } = new CylinderGeometry(shape).attributes;
  const texCoords = TEXCOORD_0.value.slice();
  for (let i = 0; i < texCoords.length / 2; i += 1) {
    texCoords[i * 2] = (1 - POSITION.value[i * 3]) / 2;
  }
  return new CylinderGeometry({ ...shape, attributes: { TEXCOORD_0: { size: 2, value: texCoords } } });
})();

export function loadImage(url) {
  return new Promise((resolve, reject) => {
    const img = new Image();
    img.crossOrigin = "anonymous";
    img.onload = () => resolve(img);
    img.onerror = reject;
    img.src = url;
  });
}

// WebGL's texture size limit: 16384 px here, lower on plenty of devices, and
// an over-limit texture doesn't throw - the mesh silently renders black.
export const MAX_TEXTURE_DIM = 4096;

/** Column-major 4x4 mapping the unit cylinder onto the segment from a to b:
 * local Y (its height axis) becomes the segment direction/length, local X/Z
 * (its radius) are scaled to `radius`, and it's translated to the midpoint. */
export function segmentTransform(a, b, radius) {
  const dx = b[0] - a[0], dy = b[1] - a[1], dz = b[2] - a[2];
  const len = Math.hypot(dx, dy, dz) || 1e-6;
  const dirX = dx / len, dirY = dy / len, dirZ = dz / len;

  // Any reference not parallel to the direction; swap when direction is
  // near-vertical (the common case for a drill hole) to avoid a degenerate cross product.
  const nearVertical = Math.abs(dirY) > 0.99;
  const refX = nearVertical ? 1 : 0, refY = nearVertical ? 0 : 1, refZ = 0;

  let rx = refY * dirZ - refZ * dirY;
  let ry = refZ * dirX - refX * dirZ;
  let rz = refX * dirY - refY * dirX;
  const rlen = Math.hypot(rx, ry, rz) || 1e-6;
  rx /= rlen; ry /= rlen; rz /= rlen;

  const fx = dirY * rz - dirZ * ry;
  const fy = dirZ * rx - dirX * rz;
  const fz = dirX * ry - dirY * rx;

  return [
    rx * radius, ry * radius, rz * radius, 0,
    dirX * len, dirY * len, dirZ * len, 0,
    fx * radius, fy * radius, fz * radius, 0,
    (a[0] + b[0]) / 2, (a[1] + b[1]) / 2, (a[2] + b[2]) / 2, 1,
  ];
}
